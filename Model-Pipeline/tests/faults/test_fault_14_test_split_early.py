"""Fault 14: `--split test` before the go.
Expected: Refused.
Why: The one look at 2024 stays one."""
import tomllib
import unittest
from pathlib import Path

from model import evaluate, train
from tests.fake_table import fake_table

MODEL_TOML = Path(__file__).resolve().parents[2] / "model.toml"


class TestSplitEarly(unittest.TestCase):
    def test_the_repo_config_has_not_allowed_it_yet(self):
        cfg = tomllib.loads(MODEL_TOML.read_text(encoding="utf-8"))
        self.assertIs(cfg["split"]["test_allowed"], False)

    def test_evaluate_refuses_before_reading_anything(self):
        with self.assertRaises(SystemExit) as stop:
            evaluate.main(["--split", "test", "--config", str(MODEL_TOML), "--features", "nowhere.parquet"])
        self.assertIn("refused: --split test needs model.toml [split] test_allowed = true", str(stop.exception.code))

    def test_train_never_reads_test_rows(self):
        with self.assertRaises(PermissionError):
            train.rows(fake_table(n=200, seed=1), "test")


if __name__ == "__main__":
    unittest.main()
