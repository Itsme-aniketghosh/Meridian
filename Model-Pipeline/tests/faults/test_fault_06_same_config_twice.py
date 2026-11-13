"""Fault 06: The same config twice.
Expected: Same predictions, same metrics.
Why: Reproducibility, like phase 1's golden hashes."""
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from model import train
from tests.fake_table import fake_table


class SameConfigTwice(unittest.TestCase):
    def test_same_metrics_bytes_and_the_same_predictions(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        fake_table(n=3000, seed=1).to_parquet(d / "features.parquet")
        for out in ("a", "b"):
            train.main(["--untuned", "--features", str(d / "features.parquet"), "--out", str(d / out),
                        "--reps", "100"])
        metrics = [p.name for p in (d / "a").glob("*.json")]
        self.assertTrue(metrics)
        for name in metrics:
            self.assertEqual(hashlib.md5((d / "a" / name).read_bytes()).hexdigest(),
                             hashlib.md5((d / "b" / name).read_bytes()).hexdigest(), name)
        a, b = (pd.read_csv(d / x / "untuned_val_scores.csv") for x in ("a", "b"))
        self.assertTrue(a[["sha", "label"]].equals(b[["sha", "label"]]))
        # The forest averages its trees across threads, so a score can differ in its last bit (1e-16 here). The
        # scores file isn't among the golden md5s; the metrics are, and they're the same bytes.
        for col in ("lines_x_files", "logreg", "forest", "lgbm"):
            np.testing.assert_allclose(a[col], b[col], rtol=0, atol=1e-12, err_msg=col)


if __name__ == "__main__":
    unittest.main()
