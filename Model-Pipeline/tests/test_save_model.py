"""The model file: train.py saves the judged candidate, proves it's the same model, and P9 can score with it."""
import json
import tempfile
import unittest
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from model.train import (MODEL_FILE, evaluate, file_md5, input_columns, main, pick_candidate, rows, save_model,
                         score_with)
from tests.fake_table import fake_table


class SaveModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d = Path(cls.tmp.name)
        cls.df = fake_table(n=3000, seed=14)
        evaluate(cls.df, 1, cls.d, {"logreg": None, "forest": {"n_estimators": 60}}, "t", reps=50)
        pick_candidate(cls.d, "t", 1, reps=50)
        cls.path, cls.c = save_model(cls.df, cls.d / "candidate.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_file_md5_and_candidate_record(self):
        self.assertEqual(self.path, self.d / MODEL_FILE)
        saved = json.loads((self.d / "candidate.json").read_text())
        self.assertEqual(saved["model_file"], MODEL_FILE)
        self.assertEqual(saved["model_md5"], file_md5(self.path))
        self.assertLessEqual(saved["refit_max_diff"], 1e-9)
        for k in ("kind", "params", "scores_file", "column", "seed"):
            self.assertIn(k, saved)

    def test_the_saved_model_scores_val_as_judged(self):
        model = joblib.load(self.path)
        judged = pd.read_csv(self.d / "t_val_scores.csv")[self.c["column"]].to_numpy()
        again = score_with(model, rows(self.df, "val"))
        self.assertLessEqual(float(np.max(np.abs(judged - again))), 1e-9)

    def test_a_tampered_scores_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            evaluate(self.df, 1, d, {"forest": {"n_estimators": 60}}, "t", reps=50)
            pick_candidate(d, "t", 1, reps=50)
            scores = pd.read_csv(d / "t_val_scores.csv")
            scores["forest"] += 0.01
            scores.to_csv(d / "t_val_scores.csv", index=False)
            with self.assertRaisesRegex(ValueError, "not the model that was judged"):
                save_model(self.df, d / "candidate.json")

    def test_p9_style_scoring_of_new_rows(self):
        model = joblib.load(self.path)
        new = rows(self.df, "val").head(5).copy()
        new["ticket_type"] = "Epic"           # never seen in training: scored as all-zero ticket columns
        s = score_with(model, new)
        self.assertEqual(len(s), 5)
        self.assertTrue(np.isfinite(s).all())
        self.assertIn("la", input_columns(model))

    def test_rows_missing_a_feature_are_refused(self):
        model = joblib.load(self.path)
        with self.assertRaises(KeyError):
            score_with(model, rows(self.df, "val").drop(columns=["la"]))


class Command(unittest.TestCase):
    def test_pick_saves_the_model(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fake_table(n=3000, seed=15).to_csv(d / "f.csv", index=False)
            args = ["--features", str(d / "f.csv"), "--config", str(d / "none.toml"), "--out", str(d / "r"),
                    "--reps", "50"]
            main(["--untuned", "--models", "logreg", "forest", *args])
            main(["--pick", "untuned", *args])
            c = json.loads((d / "r" / "candidate.json").read_text())
            self.assertTrue((d / "r" / MODEL_FILE).exists())
            self.assertEqual(c["model_md5"], file_md5(d / "r" / MODEL_FILE))


if __name__ == "__main__":
    unittest.main()
