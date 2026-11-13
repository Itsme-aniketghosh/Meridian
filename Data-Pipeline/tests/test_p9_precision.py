"""P9's precision on real pushes (meridian/p9_precision.py): the draw, the scoring, the table's number and the
agreement check. The push check is injected; tests/test_pushcheck.py tests it."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian import p9_precision as P
from meridian import replay as RP


def features(n=400, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    la, ld, nf = rng.integers(0, 400, n), rng.integers(0, 100, n), rng.integers(1, 12, n)
    lxf = (la + ld) * nf
    label = ((lxf > 2000) & (rng.random(n) < 0.6)) | ((lxf <= 2000) & (rng.random(n) < 0.1))
    return pd.DataFrame({"sha": [f"{i:040x}" for i in range(n)], "split": ["val"] * (n - 20) + ["train"] * 20,
                         "label": label.astype(int), "la": la, "ld": ld, "nf": nf})


class Draw(unittest.TestCase):
    def test_over_and_under_and_seeded(self):
        v = P.val_rows_from(features())
        s = P.draw(v, 2000, over=20, under=10, seed=1)
        self.assertEqual(int((s["lxf"] > 2000).sum()), 20)
        self.assertEqual(int((s["lxf"] <= 2000).sum()), 10)
        self.assertEqual(s["sha"].tolist(), P.draw(v, 2000, over=20, under=10, seed=1)["sha"].tolist())

    def test_table_precision_counts_all_val(self):
        v = P.val_rows_from(features())
        t = P.table_precision(v, 2000)
        flag = v["lxf"] > 2000
        self.assertEqual(t["alerts"], int(flag.sum()))
        self.assertAlmostEqual(t["precision"], v.loc[flag, "label"].mean())
        self.assertEqual(t["of"], len(v))


class Score(unittest.TestCase):
    def test_precision_bar_skips_and_disagreements(self):
        v = P.val_rows_from(features())
        s = P.draw(v, 2000, over=40, under=5, seed=2)
        results = []
        for i, r in enumerate(s.to_dict("records")):
            if i == 0:
                results.append(("apache/spark", r["sha"], "skipped", []))
                continue
            p9 = r["lxf"] > 2000 if i != 1 else not (r["lxf"] > 2000)   # one planted disagreement
            rows = [{"check_id": "P9"}] if p9 else []
            results.append(("apache/spark", r["sha"], "checked", rows))
        out = P.score(s, results, 2000)
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(len(out["disagree_with_table"]), 1)
        alerted = [p for p in out["per_push"] if p.get("p9")]
        self.assertEqual(out["alerts"], len(alerted))
        self.assertAlmostEqual(out["precision"], sum(p["label"] for p in alerted) / len(alerted))
        self.assertEqual(out["meets_bar"], out["alerts"] >= 30 and out["precision"] >= 0.80)


class Command(unittest.TestCase):
    def test_writes_rows_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            f = d / "features.csv"
            features().to_csv(f, index=False)
            (d / "out").mkdir()
            db = d / "out" / "meridian.sqlite"
            sqlite3.connect(db).close()
            calls = []

            def fake_check(db_path, repo, co, changed, cfg, settings, rng, collect=None):
                calls.append(rng)
                collect.append(("P9", "push", "big"))
                return "head\n\n- **P9 Defect risk**: big\n"

            orig = RP.git
            RP.git = lambda *a: "c" * 40 + "\n" if a[-2:] == ("rev-parse", "HEAD") else ""
            try:
                mirrors = d / "mirrors"
                (mirrors / "apache__spark.git").mkdir(parents=True)
                r = P.main(["--run-id", "R", "--db", str(db), "--mirrors", str(mirrors), "--features", str(f),
                            "--over", "3", "--under", "2", "--out", str(d / "p9.json")], check=fake_check)
            finally:
                RP.git = orig
            self.assertEqual(len(calls), 5)
            self.assertTrue(all("^1..." in c for c in calls))                        # parent...commit
            self.assertTrue((d / "out" / "push_alert.jsonl").exists())
            self.assertEqual(json.loads((d / "p9.json").read_text())["decision"], "rachna-15c")
        self.assertIn("table_all_val", r)


if __name__ == "__main__":
    unittest.main()
