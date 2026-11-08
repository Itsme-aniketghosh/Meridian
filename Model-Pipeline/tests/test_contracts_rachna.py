"""Rachna's Contract 4 examples in tests/contracts/ hold their contract, and still match what train.py writes."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from model.evaluate import read_candidate
from tests.fake_table import fake_table
from tests.make_contracts_rachna import CONTRACTS, TABLE, make

BASELINE_COLUMNS = ["lines_x_files", "churn", "bug_fixes"]


class Examples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scores = pd.read_csv(CONTRACTS / "val_scores.csv")
        cls.candidate = json.loads((CONTRACTS / "candidate.json").read_text())
        cls.noise = json.loads((CONTRACTS / "seed_noise.json").read_text())
        table = fake_table(**TABLE)
        cls.val = table[table["split"] == "val"].reset_index(drop=True)

    def test_val_scores_one_row_per_val_row_in_order(self):
        self.assertEqual(list(self.scores.columns[:2]), ["sha", "label"])
        self.assertEqual(self.scores["sha"].tolist(), self.val["sha"].tolist())
        self.assertEqual(self.scores["label"].tolist(), self.val["label"].tolist())

    def test_val_scores_has_every_baseline_and_finite_scores(self):
        for c in BASELINE_COLUMNS:
            self.assertIn(c, self.scores.columns)
        for c in self.scores.columns[2:]:
            self.assertTrue(np.isfinite(self.scores[c].to_numpy(dtype=float)).all(), c)

    def test_candidate_has_contract_4_and_points_at_its_column(self):
        for k in ("kind", "params", "scores_file", "column", "seed"):
            self.assertIn(k, self.candidate)
        self.assertEqual(self.candidate["scores_file"], "val_scores.csv")
        self.assertIn(self.candidate["column"], self.scores.columns)
        self.assertEqual(Path(read_candidate(CONTRACTS / "candidate.json")["scores_file"]).name, "val_scores.csv")

    def test_seed_noise_has_mean_sd_and_five_seeds(self):
        for k in ("kind", "params", "n", "seeds", "pr_auc", "mean", "sd", "two_sd"):
            self.assertIn(k, self.noise)
        self.assertEqual(self.noise["seeds"], [1, 2, 3, 4, 5])
        self.assertEqual(len(self.noise["pr_auc"]), 5)
        self.assertEqual(self.noise["kind"], self.candidate["kind"])

    def test_train_py_still_writes_this_shape(self):
        """If train.py's output changes shape, this fails: rerun python -m tests.make_contracts_rachna and tell
        the consumers (S10)."""
        with tempfile.TemporaryDirectory() as d:
            make(d)
            fresh_scores = pd.read_csv(Path(d) / "val_scores.csv")
            fresh_candidate = json.loads((Path(d) / "candidate.json").read_text())
            fresh_noise = json.loads((Path(d) / "seed_noise.json").read_text())
        self.assertEqual(list(fresh_scores.columns), list(self.scores.columns))
        self.assertEqual(fresh_scores["sha"].tolist(), self.scores["sha"].tolist())
        self.assertEqual(set(fresh_candidate), set(self.candidate))
        self.assertEqual(set(fresh_noise), set(self.noise))


if __name__ == "__main__":
    unittest.main()
