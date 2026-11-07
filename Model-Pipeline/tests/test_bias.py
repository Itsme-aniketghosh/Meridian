"""bias.py (Chaitali, step F): the flag rule on exact counts, the interval, power, and bias_report.json.

Every table here is made up from a seed. Nothing reads the real feature table or its test rows."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from scipy.stats import binom, binomtest

from model import bias
from tests.fake_table import fake_table

BIAS = """
[bias]
slices = ["module", "component", "exp_bucket", "size_bucket"]
min_n = 30
max_gap = 0.10
power_gap = 0.15
pool_below = 0
baseline = "lines_x_files"
accepted = {accepted}
"""
CONFIG = """
[data]
run_id = "fake0000000000aa"
window_days = 365
follow_up_end = "2025-10-01T00:00:00Z"
start_year = 2015
repo = "apache/spark"
prior_fix_days = 365

[split]
train = [2015, 2022]
val = 2023
test = 2024
test_allowed = false

[seed]
value = 1

[eval]
bootstrap_reps = 200
interval = 0.95
effort = 0.2
effort_ranking = "risk_per_line"
threshold = "{rule}"
threshold_precision = 0.8
threshold_min_alerts = 5
threshold_alert_rate = 0.2
""" + BIAS
B = {"slices": ["module"], "min_n": 30, "max_gap": 0.10, "power_gap": 0.15, "pool_below": 0,
     "baseline": "lines_x_files", "accepted": []}


class Rule(unittest.TestCase):
    """PASS-BARS §6.1: the flag rule, the interval and judging, on exact counts so nothing is left to luck."""

    def test_wilson_is_phase_1s_interval(self):
        # phase 1's bias.py calls scipy's binomtest(...).proportion_ci(method="wilson")
        for k, n in [(0, 30), (1, 30), (15, 30), (30, 30), (175, 500), (250, 500), (3, 4113), (486, 4113)]:
            ci = binomtest(k, n).proportion_ci(method="wilson")
            lo, hi = bias.wilson(k, n)
            self.assertAlmostEqual(float(lo), ci.low, places=12)
            self.assertAlmostEqual(float(hi), ci.high, places=12)

    def test_planted_gap_is_flagged_and_no_gap_is_not(self):
        d = bias.decision_3(B)
        self.assertTrue(d["recall gap 0.15"]["flagged"])      # 175 of 500 = 0.35 against 0.50: interval up to 0.393
        self.assertFalse(d["recall no gap"]["flagged"])       # 250 of 500 = 0.50
        self.assertTrue(d["fpr gap 0.15"]["flagged"])         # 125 of 500 = 0.25 against 0.10: worse is higher
        self.assertFalse(d["fpr no gap"]["flagged"])

    def test_n_29_is_listed_not_judged(self):
        c29 = pd.Series({"tp": 0, "fn": 29, "fp": 0, "tn": 100})  # recall 0 of 29: as bad as it gets
        e = bias.share("recall", c29, 0.9, B, 0.95, None)
        self.assertEqual((e["n"], e["judged"], e["flagged"], e["power"]), (29, False, False, None))
        c30 = pd.Series({"tp": 0, "fn": 30, "fp": 0, "tn": 100})
        e = bias.share("recall", c30, 0.9, B, 0.95, None)
        self.assertEqual((e["n"], e["judged"], e["flagged"]), (30, True, True))

    def test_a_slice_with_no_positives_has_no_recall(self):
        e = bias.share("recall", pd.Series({"tp": 0, "fn": 0, "fp": 3, "tn": 40}), 0.5, B, 0.95, None)
        self.assertEqual((e["value"], e["judged"], e["flagged"]), (None, False, False))

    def test_power_is_the_exact_binomial_sum(self):
        # by hand: every k whose Wilson upper end is under 0.40, weighted by Binomial(500, 0.35)
        k = np.arange(501)
        hit = [binomtest(int(x), 500).proportion_ci(method="wilson").high < 0.40 for x in k]
        expected = binom.pmf(k, 500, 0.35)[hit].sum()
        self.assertAlmostEqual(bias.power("recall", 500, 0.50, B), expected, places=12)

    def test_power_grows_with_n(self):
        p = [bias.power("recall", n, 0.5, B) for n in (30, 100, 400, 1000)]
        self.assertEqual(p, sorted(p))
        self.assertLess(p[0], 0.1)     # PASS-BARS §6: the rule rarely flags a real gap in a small slice
        self.assertGreater(p[-1], 0.85)

    def test_power_none_where_it_cannot_apply(self):
        self.assertIsNone(bias.power("recall", 29, 0.5, B))      # not judged
        self.assertIsNone(bias.power("recall", 100, 0.10, B))    # 0.10 - 0.15 is below 0
        self.assertIsNone(bias.power("pr_auc", 100, 0.5, B))

    def test_pooling_merges_small_groups_and_lists_them(self):
        g = pd.Series(["a"] * 40 + ["b"] * 5 + ["c"] * 3)
        out, members = bias.pooled(g, 30)
        self.assertEqual(sorted(out.unique()), [bias.OTHER, "a"])
        self.assertEqual(members, {bias.OTHER: ["b", "c"]})
        same, none = bias.pooled(g, 0)
        self.assertTrue((same == g).all())
        self.assertEqual(none, {})

    def test_settings_refuse_a_missing_key(self):
        with self.assertRaises(ValueError):
            bias.bias_settings({"bias": {k: v for k, v in B.items() if k != "max_gap"}})


class Threshold(unittest.TestCase):
    """Decision 10: [eval]'s rule when it flags enough val commits, else the P9 alert rate."""
    S = {"threshold": "precision", "threshold_precision": 0.8, "threshold_min_alerts": 5, "threshold_alert_rate": 0.2}

    def test_falls_back_when_eval_flags_too_few(self):
        rng = np.random.default_rng(3)
        y, s = rng.integers(0, 2, 200), rng.random(200)          # no signal: precision 0.8 is never reached
        t, rule, why = bias.threshold_for(y, s, self.S)
        self.assertEqual(rule, "alert_rate")
        self.assertIn("flags 0", why)
        self.assertEqual(int((s >= t).sum()), 40)                # 20% of 200

    def test_keeps_eval_when_it_works(self):
        y = np.array([1] * 20 + [0] * 80)
        s = np.concatenate([np.linspace(1, 0.9, 20), np.linspace(0.5, 0, 80)])   # the bugs score highest
        t, rule, _ = bias.threshold_for(y, s, self.S)
        self.assertEqual(rule, "precision")


class Report(unittest.TestCase):
    """bias_report.json end to end, on a made-up table and made-up scores."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="meridian-bias-"))
        t = fake_table(n=6000, seed=4, years=[2021, 2022, 2023])
        rng = np.random.default_rng(4)
        t["exp_bucket"] = rng.choice(["0", "1", "2-10", ">10"], len(t))
        t["size_bucket"] = pd.qcut(t["la"] + t["ld"], 4, labels=["Q1", "Q2", "Q3", "Q4"]).astype(str)
        cls.features = cls.dir / "features.parquet"
        t.to_parquet(cls.features, index=False)
        val = t[t["split"] == "val"].reset_index(drop=True)
        cls.val = val
        lines = (val["la"] + val["ld"]) * val["nf"]
        cls.scores = cls.dir / "val_scores.csv"
        pd.DataFrame({"sha": val["sha"], "label": val["label"], "lines_x_files": lines,
                      "model": val["label"] * 0.5 + rng.random(len(val))}).to_csv(cls.scores, index=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def config(self, rule="alert_rate", accepted="[]"):
        path = Path(tempfile.mkdtemp(dir=self.dir)) / "model.toml"
        path.write_text(CONFIG.format(rule=rule, accepted=accepted), encoding="utf-8")
        return path

    def run_bias(self, config=None, scores=None, out=None):
        out = out or Path(tempfile.mkdtemp(dir=self.dir))
        config = config or self.config()
        bias.main(["--scores", str(scores or self.scores), "--column", "model", "--config", str(config),
                   "--features", str(self.features), "--out", str(out), "--no-mlflow", "--no-charts",
                   "--candidate", str(self.dir / "none.json")])
        return out / "bias_report.json"

    def test_every_slice_has_its_numbers(self):
        rep = json.loads(self.run_bias().read_text())
        self.assertEqual(rep["n"], len(self.val))
        self.assertEqual(sorted(rep["slices"]), ["component", "exp_bucket", "module", "size_bucket"])
        for gs in rep["slices"].values():
            for e in gs.values():
                self.assertIn("n", e)
                self.assertIn("positives", e)
                for m in bias.METRICS:
                    for k in ("value", "n", "ci", "judged", "flagged", "bonferroni_flagged", "power"):
                        self.assertIn(k, e[m])
        self.assertIn("pass", rep)
        self.assertEqual(rep["threshold"]["rule"], "alert_rate")

    def test_same_inputs_same_bytes(self):
        a, b = self.run_bias().read_bytes(), self.run_bias().read_bytes()
        self.assertEqual(a, b)

    def test_scores_that_dont_match_the_val_rows_are_refused(self):
        bad = self.dir / "bad_scores.csv"
        s = pd.read_csv(self.scores)
        s.loc[0, "sha"] = "f" * 40
        s.to_csv(bad, index=False)
        with self.assertRaises(SystemExit):
            self.run_bias(scores=bad)

    def test_size_only_scores_flag_by_size(self):
        # a model that is only the size rule should treat small commits worse: the report has to see it
        out = Path(tempfile.mkdtemp(dir=self.dir))
        s = pd.read_csv(self.scores)
        s["model"] = s["lines_x_files"]
        path = out / "size.csv"
        s.to_csv(path, index=False)
        rep = json.loads(self.run_bias(scores=path, out=out).read_text())
        size = rep["slices"]["size_bucket"]
        self.assertGreater(size["Q4"]["recall"]["value"], size["Q1"]["recall"]["value"])

    def planted(self):
        """Scores that are right everywhere except module core, where they are noise: core must be flagged."""
        out = Path(tempfile.mkdtemp(dir=self.dir))
        s = pd.read_csv(self.scores)
        core = (self.val["module"] == "core").to_numpy()
        s["model"] = np.where(core, np.random.default_rng(9).random(len(s)), s["label"] + 0.01 * s.index / len(s))
        path = out / "planted.csv"
        s.to_csv(path, index=False)
        return path, out

    def test_a_planted_bad_module_is_flagged(self):
        path, out = self.planted()
        rep = json.loads(self.run_bias(scores=path, out=out).read_text())
        self.assertIn("module=core:recall", {f"{f['slice']}:{f['metric']}" for f in rep["flags"]})
        self.assertFalse(rep["pass"])

    def test_accepted_flags_let_the_report_pass(self):
        path, out = self.planted()
        rep = json.loads(self.run_bias(scores=path, out=out).read_text())
        names = json.dumps([f"{f['slice']}:{f['metric']}" for f in rep["flags"]])
        again = json.loads(self.run_bias(config=self.config(accepted=names), scores=path, out=out).read_text())
        self.assertTrue(rep["flags"])
        self.assertFalse(rep["pass"])
        self.assertTrue(again["pass"])
        self.assertEqual(again["open_flags"], 0)

    def test_logs_a_run_tagged_chaitali(self):
        store = Path(tempfile.mkdtemp(dir=self.dir))
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": f"sqlite:///{store / 'mlflow.db'}"}):
            out = Path(tempfile.mkdtemp(dir=self.dir))
            config = self.config()
            bias.main(["--scores", str(self.scores), "--column", "model", "--config", str(config),
                       "--features", str(self.features), "--out", str(out), "--no-charts",
                       "--candidate", str(self.dir / "none.json")])
            import mlflow
            runs = mlflow.search_runs(experiment_names=["meridian-defect-risk"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs.iloc[0]["tags.owner"], "chaitali")


if __name__ == "__main__":
    unittest.main()
