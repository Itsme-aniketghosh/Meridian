"""python -m model.train --untuned: the summary, the score file, the bootstrap, and clear failures."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from model.train import main, paired_bootstrap
from tests.fake_table import fake_table

KINDS = ["logreg", "forest"] + (["lgbm"] if importlib.util.find_spec("lightgbm") else [])
NAMES = {"lines_x_files", "churn", "bug_fixes", *KINDS}
BASE = ["--untuned", "--reps", "200", "--models", *KINDS]


class Untuned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.df = fake_table(n=4000, seed=2)
        cls.df.to_csv(d / "features.csv", index=False)
        (d / "model.toml").write_text(
            '[split]\ntrain = [2015, 2022]\nval = 2023\ntest = 2024\ntest_allowed = false\n[seed]\nvalue = 1\n')
        cls.out = d / "reports"
        main(BASE + ["--config", str(d / "model.toml"), "--features", str(d / "features.csv"), "--out", str(cls.out)])
        cls.summary = json.loads((cls.out / "untuned_val.json").read_text())
        cls.scores = pd.read_csv(cls.out / "untuned_val_scores.csv")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_baseline_and_model_with_n(self):
        val = self.df[self.df["split"] == "val"]
        self.assertEqual(set(self.summary["results"]), NAMES)
        self.assertEqual(self.summary["n"], len(val))
        self.assertEqual(self.summary["positives"], int(val["label"].sum()))

    def test_intervals_hold_their_estimate(self):
        for name, r in self.summary["results"].items():
            if name == "lines_x_files":
                self.assertNotIn("interval", r)
                continue
            lo, hi = r["interval"]
            self.assertLessEqual(lo, hi, name)

    def test_score_file_is_val_only(self):
        val_shas = set(self.df.loc[self.df["split"] == "val", "sha"])
        test_shas = set(self.df.loc[self.df["split"] == "test", "sha"])
        self.assertEqual(set(self.scores["sha"]), val_shas)
        self.assertFalse(set(self.scores["sha"]) & test_shas)
        self.assertTrue(NAMES <= set(self.scores.columns))

    def test_rerun_gives_the_same_numbers(self):
        with tempfile.TemporaryDirectory() as d2:
            d = Path(self.tmp.name)
            main(BASE + ["--config", str(d / "model.toml"), "--features", str(d / "features.csv"), "--out", d2])
            again = json.loads((Path(d2) / "untuned_val.json").read_text())
        for name in NAMES:
            self.assertAlmostEqual(again["results"][name]["pr_auc"], self.summary["results"][name]["pr_auc"],
                                   places=9)


class Failures(unittest.TestCase):
    def test_missing_table_says_where_it_goes(self):
        with self.assertRaisesRegex(SystemExit, "Model-Pipeline/data"):
            main(["--untuned", "--features", "/nonexistent/features.parquet"])

    def test_missing_column_names_it(self):
        with tempfile.TemporaryDirectory() as d:
            fake_table(n=100).drop(columns=["nuc"]).to_csv(Path(d) / "f.csv", index=False)
            with self.assertRaisesRegex(SystemExit, "nuc"):
                main(["--untuned", "--features", str(Path(d) / "f.csv"), "--config", str(Path(d) / "none.toml"),
                      "--out", d])

    def test_nothing_to_do(self):
        with self.assertRaises(SystemExit):
            main([])


class Bootstrap(unittest.TestCase):
    def test_same_scores_give_zero(self):
        rng = np.random.default_rng(0)
        y = (rng.random(500) < 0.2).astype(int)
        s = rng.random(500)
        self.assertEqual(paired_bootstrap(y, s, s, reps=100), (0.0, 0.0, 0.0))

    def test_clearly_better_scores_have_an_interval_above_zero(self):
        rng = np.random.default_rng(1)
        y = (rng.random(2000) < 0.2).astype(int)
        good = y + rng.random(2000) * 0.8
        d, lo, hi = paired_bootstrap(y, good, rng.random(2000), reps=200)
        self.assertGreater(lo, 0)
        self.assertLessEqual(lo, d)
        self.assertLessEqual(d, hi)

    def test_seeded(self):
        rng = np.random.default_rng(2)
        y = (rng.random(300) < 0.3).astype(int)
        a, b = rng.random(300), rng.random(300)
        self.assertEqual(paired_bootstrap(y, a, b, reps=50, seed=4), paired_bootstrap(y, a, b, reps=50, seed=4))


if __name__ == "__main__":
    unittest.main()
