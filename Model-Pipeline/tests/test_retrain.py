"""train.py --retrain (decision chaitali-7): --tune without the search gives the same scores and candidate."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from model.run_all import FAKE_SEARCH, MODEL_TOML, write_fake
from model.train import main
from tests import mlflow_uri


class Retrain(unittest.TestCase):
    def test_same_scores_and_candidate_as_the_search(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:  # Windows: SQLAlchemy keeps mlflow.db open
            table, out = write_fake(d), Path(d) / "reports"
            io_args = ["--config", str(MODEL_TOML), "--features", str(table), "--out", str(out)]
            env = {"MLFLOW_TRACKING_URI": mlflow_uri(d)}
            with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                main(["--tune", "logreg", "forest", "lgbm", *FAKE_SEARCH, *io_args])
                searched = pd.read_csv(out / "tuned_val_scores.csv"), json.loads((out / "candidate.json").read_text())
                main(["--retrain", "logreg", "forest", "lgbm", "--reps", "200", *io_args])
                again = pd.read_csv(out / "tuned_val_scores.csv"), json.loads((out / "candidate.json").read_text())
        self.assertEqual(list(searched[0]["sha"]), list(again[0]["sha"]))
        diff = (searched[0].drop(columns="sha") - again[0].drop(columns="sha")).abs().to_numpy().max()
        self.assertLess(diff, 1e-12)  # two fits of the same config: equal up to the last bit
        for key in ("kind", "params", "column", "seed"):
            self.assertEqual(searched[1][key], again[1][key], key)


if __name__ == "__main__":
    unittest.main()
