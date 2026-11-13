"""Fault 03: A feature column missing.
Expected: train.py fails with a clear message.
Why: A silent NaN model is worse than a crash."""
import shutil
import tempfile
import unittest
from pathlib import Path

from model import train
from tests.fake_table import fake_table


class MissingColumn(unittest.TestCase):
    def test_training_stops_and_names_the_column(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        fake_table(n=2000, seed=1).drop(columns=["nf"]).to_parquet(d / "features.parquet")
        with self.assertRaises(SystemExit) as stop:
            train.main(["--untuned", "--features", str(d / "features.parquet"), "--out", str(d / "out")])
        self.assertEqual(stop.exception.code, "feature table: feature table is missing column(s): nf")
        self.assertFalse((d / "out").exists(), "nothing written")


if __name__ == "__main__":
    unittest.main()
