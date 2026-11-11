"""Decision 15b (model/p9rule.py): the AND rule's grid, its 30-alert floor, its tie-break, and its file."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from model import p9rule as R


class Rule(unittest.TestCase):
    def test_cutoff_flags_about_that_share(self):
        s = np.arange(1000, dtype=float)
        self.assertEqual(int((s >= R.cutoff(s, 0.05)).sum()), 50)
        self.assertEqual(int((s >= R.cutoff(s, 0.01)).sum()), 10)

    def test_and_beats_either_alone_when_each_has_its_own_false_alarms(self):
        rng = np.random.default_rng(0)
        n = 5000
        y = (rng.random(n) < 0.12).astype(int)
        # each signal flags the bugs, plus its own, independent false alarms that score even higher than a bug
        clean = y == 0
        model = y * 1.0 + (clean & (rng.random(n) < 0.15)) * 1.5 + rng.random(n) * 0.1
        lxf = y * 1.0 + (clean & (rng.random(n) < 0.15)) * 1.5 + rng.random(n) * 0.1
        chosen, pairs = R.pick(y, model, lxf)
        alone = max(R.best_single(y, model)["precision"], R.best_single(y, lxf)["precision"])
        self.assertGreater(chosen["precision"], alone)
        self.assertTrue(all(p["alerts"] >= R.MIN_ALERTS for p in pairs))

    def test_fewer_than_30_alerts_is_never_picked(self):
        y = np.r_[np.ones(20), np.zeros(80)].astype(int)
        perfect = y.astype(float)
        chosen, pairs = R.pick(y, perfect, perfect)          # only 20 true commits: no pair reaches 30 alerts at 100%
        self.assertTrue(all(p["alerts"] >= 30 for p in pairs))
        if chosen:
            self.assertGreaterEqual(chosen["alerts"], 30)

    def test_tie_goes_to_more_alerts(self):
        y = np.ones(200, dtype=int)                           # every alert is true: precision 1.0 everywhere
        s = np.arange(200, dtype=float)
        chosen, _ = R.pick(y, s, s)
        self.assertEqual(chosen["precision"], 1.0)
        self.assertEqual(chosen["alerts"], 60)                # top 30% of 200: the most alerts the grid allows

    def test_wilson(self):
        lo, hi = R.wilson(24, 30)
        self.assertAlmostEqual(lo, 0.627, places=3)
        self.assertAlmostEqual(hi, 0.905, places=3)


class Command(unittest.TestCase):
    def test_writes_the_rule_file_the_push_check_reads(self):
        rng = np.random.default_rng(1)
        n = 3000
        y = (rng.random(n) < 0.15).astype(int)
        scores = pd.DataFrame({"sha": [f"{i:040x}" for i in range(n)], "label": y,
                               "lines_x_files": y * 3 + rng.random(n) * 4, "forest": y * 2 + rng.random(n) * 3})
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            scores.to_csv(d / "t_val_scores.csv", index=False)
            (d / "candidate.json").write_text(json.dumps({"kind": "forest", "column": "forest", "model_md5": "a" * 32,
                                                          "scores_file": "t_val_scores.csv"}))
            r = R.main(["--candidate", str(d / "candidate.json"), "--out", str(d / "p9_rule.json")])
            saved = json.loads((d / "p9_rule.json").read_text())
        self.assertEqual(saved["model_md5"], "a" * 32)          # the push check checks this against the champion's
        self.assertEqual(saved["adopted"], saved["pick"]["precision"] >= 0.80)
        for k in ("model_cutoff", "lxf_cutoff", "alerts", "precision", "interval"):
            self.assertIn(k, saved["pick"])
        self.assertEqual(r["pairs_tried"], 900)


if __name__ == "__main__":
    unittest.main()
