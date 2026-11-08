"""Decision 1's pick and reports/candidate.json (Contract 4): the rule, the file, and the commands."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from model.evaluate import read_candidate
from model.train import main, pick_candidate, read_params
from tests.fake_table import fake_table

CONTRACT_KEYS = {"kind", "params", "scores_file", "column", "seed"}


def write_run(d, tag, scores, params):
    """A <tag>_val.json and <tag>_val_scores.csv as --untuned or --tune would write them."""
    pd.DataFrame(scores).to_csv(Path(d) / f"{tag}_val_scores.csv", index=False)
    (Path(d) / f"{tag}_val.json").write_text(json.dumps({"tag": tag, "params": params}))


def labels(n=3000, rate=0.15, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.random(n) < rate).astype(int), rng


class Rule(unittest.TestCase):
    def test_clearly_best_model_wins(self):
        y, rng = labels()
        strong = y * 1.5 + rng.random(len(y))
        weak = y * 0.2 + rng.random(len(y))
        with tempfile.TemporaryDirectory() as d:
            write_run(d, "t", {"sha": [f"{i:040x}" for i in range(len(y))], "label": y, "lines_x_files": weak,
                               "logreg": weak, "lgbm": strong}, {"logreg": None, "lgbm": {"num_leaves": 15}})
            c = pick_candidate(d, "t", seed=1, reps=200)
        self.assertEqual(c["kind"], "lgbm")
        self.assertEqual(c["params"], {"num_leaves": 15})
        self.assertFalse(c["compared"]["logreg"]["tied_with_best"])

    def test_a_simpler_tied_model_wins(self):
        y, rng = labels()
        # Two equally good models with their own noise: whichever edges ahead, the interval contains 0.
        a = y * 1.0 + rng.random(len(y))
        b = y * 1.0 + rng.random(len(y))
        with tempfile.TemporaryDirectory() as d:
            write_run(d, "t", {"sha": [f"{i:040x}" for i in range(len(y))], "label": y, "lines_x_files": a,
                               "logreg": a, "forest": b}, {"logreg": None, "forest": None})
            c = pick_candidate(d, "t", seed=1, reps=200)
        other = "forest" if c["best_by_pr_auc"] == "logreg" else "logreg"
        self.assertTrue(c["compared"][other]["tied_with_best"])
        self.assertEqual(c["kind"], "logreg")
        self.assertEqual(c["params"], {})

    def test_contract_4_and_evaluate_can_read_it(self):
        y, rng = labels(n=500)
        with tempfile.TemporaryDirectory() as d:
            write_run(d, "t", {"sha": [f"{i:040x}" for i in range(len(y))], "label": y,
                               "lines_x_files": rng.random(len(y)), "forest": y + rng.random(len(y))},
                      {"forest": {"n_estimators": 100}})
            c = pick_candidate(d, "t", seed=3, reps=50)
            saved = json.loads((Path(d) / "candidate.json").read_text())
            read = read_candidate(Path(d) / "candidate.json")
            cols = pd.read_csv(read["scores_file"]).columns
        self.assertTrue(CONTRACT_KEYS <= set(saved))
        self.assertEqual(saved, json.loads(json.dumps(c)))
        self.assertEqual(saved["scores_file"], "t_val_scores.csv")
        self.assertIn(saved["column"], cols)
        self.assertEqual(saved["seed"], 3)

    def test_missing_run_is_refused(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(FileNotFoundError):
            pick_candidate(d, "tuned", seed=1)

    def test_seed_noise_reads_a_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "candidate.json"
            p.write_text(json.dumps({"kind": "forest", "params": {"n_estimators": 50}}))
            self.assertEqual(read_params(p), ("forest", {"n_estimators": 50}))
            p.write_text(json.dumps({"kind": "logreg", "params": None}))
            self.assertEqual(read_params(p), ("logreg", {}))


class Command(unittest.TestCase):
    def test_untuned_then_pick(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fake_table(n=3000, seed=12).to_csv(d / "f.csv", index=False)
            args = ["--features", str(d / "f.csv"), "--config", str(d / "none.toml"), "--out", str(d / "r"),
                    "--reps", "100"]
            main(["--untuned", "--models", "logreg", "forest", *args])
            main(["--pick", "untuned", *args])
            c = json.loads((d / "r" / "candidate.json").read_text())
        self.assertIn(c["kind"], ("logreg", "forest"))
        self.assertEqual(c["scores_file"], "untuned_val_scores.csv")
        self.assertEqual(c["tag"], "untuned")


if __name__ == "__main__":
    unittest.main()
