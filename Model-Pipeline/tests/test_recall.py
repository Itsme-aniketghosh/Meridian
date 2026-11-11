"""model/recall.py: the recall metric, tuning that never sees val, and a run end to end on the fake table."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path


from model import recall as R
from tests.fake_table import fake_table


class Metric(unittest.TestCase):
    def test_share_for_recall(self):
        y = [1, 0, 1, 0, 0, 0, 0, 0, 0, 1]
        perfect = [9, 1, 8, 1, 1, 1, 1, 1, 1, 7]
        self.assertAlmostEqual(R.share_for_recall(y, perfect, 0.9), 0.3)   # all 3 bugs in the top 3
        self.assertAlmostEqual(R.share_for_recall(y, perfect, 0.6), 0.2)   # 2 of 3 in the top 2
        worst = [-v for v in perfect]
        self.assertAlmostEqual(R.share_for_recall(y, worst, 0.9), 1.0)
        self.assertAlmostEqual(R.recall_at_share(y, perfect, 0.2), 2 / 3)

    def test_no_positives_is_refused(self):
        with self.assertRaises(ValueError):
            R.share_for_recall([0, 0], [1, 2])


class Tuning(unittest.TestCase):
    def test_folds_refuse_val_rows(self):
        df = fake_table(n=600, seed=3)
        with self.assertRaises(PermissionError):
            R.fold_share(df, "logreg", 1, None, "none", 0.9)

    def test_end_to_end_on_the_fake_table(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "features.parquet"
            fake_table(n=1500, seed=5).to_parquet(path, index=False)
            with contextlib.redirect_stdout(io.StringIO()):
                R.main(["--features", str(path), "--out", t, "--champion-scores", str(Path(t) / "none.csv"),
                        "--kinds", "logreg", "--trials", "2"])
            out = json.loads((Path(t) / "recall_val.json").read_text(encoding="utf-8"))
            self.assertEqual(out["reference"], "lines_x_files")
            self.assertIn(out["configs"]["logreg"]["imbalance"], ("none", "balanced"))
            r = out["results"]["recall_logreg"]
            self.assertTrue(0 < r["share_for_recall"] <= 1 and 0 <= r["recall_at_budget"] <= 1)
            self.assertEqual(len(r["share_vs_ref"]), 3)


if __name__ == "__main__":
    unittest.main()
