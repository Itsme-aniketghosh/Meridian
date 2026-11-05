"""Seed noise (decision 7): five seeds of one config, the file AI 2 and AI 3 read, and the command."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from model.train import main, read_params, seed_noise
from tests.fake_table import fake_table

HAS_LGBM = importlib.util.find_spec("lightgbm") is not None


class SeedNoise(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = fake_table(n=4000, seed=8)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.r = seed_noise(cls.df, "forest", {"n_estimators": 100, "min_samples_leaf": 10}, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_contract_fields(self):
        saved = json.loads((self.out / "seed_noise.json").read_text())
        for key in ("kind", "params", "n", "seeds", "pr_auc", "mean", "sd", "two_sd"):
            self.assertIn(key, saved)
        self.assertEqual(saved["seeds"], [1, 2, 3, 4, 5])
        self.assertEqual(len(saved["pr_auc"]), 5)

    def test_mean_and_sample_sd(self):
        v = np.array(self.r["pr_auc"])
        self.assertAlmostEqual(self.r["mean"], v.mean())
        self.assertAlmostEqual(self.r["sd"], v.std(ddof=1))
        self.assertAlmostEqual(self.r["two_sd"], 2 * self.r["sd"])

    def test_a_seeded_forest_really_varies(self):
        self.assertGreater(self.r["sd"], 0)

    def test_scores_file_is_val_only_one_column_per_seed(self):
        scores = pd.read_csv(self.out / "seed_noise_val_scores.csv")
        self.assertEqual(set(scores["sha"]), set(self.df.loc[self.df["split"] == "val", "sha"]))
        self.assertEqual([c for c in scores.columns if c.startswith("seed_")], [f"seed_{s}" for s in range(1, 6)])

    def test_rerun_is_identical(self):
        with tempfile.TemporaryDirectory() as d:
            again = seed_noise(self.df, "forest", {"n_estimators": 100, "min_samples_leaf": 10}, d)
        np.testing.assert_allclose(again["pr_auc"], self.r["pr_auc"], atol=1e-9)

    def test_needs_two_different_seeds(self):
        with self.assertRaises(ValueError):
            seed_noise(self.df, "forest", None, self.out, seeds=(1, 1))

    def test_seedless_model_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            r = seed_noise(self.df, "logreg", None, d, seeds=(1, 2))
        self.assertEqual(r["sd"], 0)
        self.assertIn("note", r)

    @unittest.skipUnless(HAS_LGBM, "lightgbm not installed")
    def test_lgbm_with_subsampling_varies(self):
        with tempfile.TemporaryDirectory() as d:
            r = seed_noise(self.df, "lgbm", {"n_estimators": 100}, d, seeds=(1, 2, 3))
        self.assertGreater(r["sd"], 0)


class Command(unittest.TestCase):
    def test_reads_the_tuned_config_and_writes_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fake_table(n=3000, seed=9).to_csv(d / "f.csv", index=False)
            (d / "tuning_forest.json").write_text(json.dumps(
                {"kind": "forest", "best_params": {"n_estimators": 100, "min_samples_leaf": 20}}))
            main(["--seed-noise", str(d / "tuning_forest.json"), "--seeds", "1", "2", "3",
                  "--features", str(d / "f.csv"), "--config", str(d / "none.toml"), "--out", str(d / "r")])
            saved = json.loads((d / "r" / "seed_noise.json").read_text())
        self.assertEqual(saved["kind"], "forest")
        self.assertEqual(saved["params"], {"n_estimators": 100, "min_samples_leaf": 20})
        self.assertEqual(saved["seeds"], [1, 2, 3])

    def test_refuses_a_file_that_isnt_a_study(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text("{}")
            with self.assertRaises(ValueError):
                read_params(p)


if __name__ == "__main__":
    unittest.main()
