"""Fault 04: A column's type changed.
Expected: data.py or train.py refuses it.
Why: Schema drift is the most common real break."""
import shutil
import tempfile
import unittest
from pathlib import Path

from model import train
from tests.fake_table import fake_table


class ColumnType(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        df = fake_table(n=2000, seed=1)
        df["la"] = df["la"].astype(str)  # lines added arrive as text: "40", not 40
        df.to_parquet(self.d / "features.parquet")

    def train(self):
        train.main(["--untuned", "--features", str(self.d / "features.parquet"), "--out", str(self.d / "out")])

    def test_training_never_runs_on_it(self):
        with self.assertRaises(SystemExit):
            self.train()
        self.assertFalse((self.d / "out").exists(), "nothing written")

    def test_it_is_refused_with_a_message_naming_the_column(self):
        with self.assertRaises(SystemExit) as stop:
            self.train()
        self.assertRegex(stop.exception.code, r"^feature table: feature column\(s\) not numbers: la \(\w+\)$")


if __name__ == "__main__":
    unittest.main()
