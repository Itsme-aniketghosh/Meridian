"""Fault 05: Feature table from the wrong run_id.
Expected: Refused, or tagged so it can't be mistaken.
Why: Every number traces to one block."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mlflow

from model import tracking
from tests.fake_table import fake_table

MODEL_TOML = Path(__file__).resolve().parents[2] / "model.toml"


class WrongBlock(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.features = self.d / "features.parquet"
        fake_table(n=500, seed=1).to_parquet(self.features)
        sha = mock.patch("model.tracking.git_state", return_value=("e" * 40, "false"))
        sha.start()
        self.addCleanup(sha.stop)

    def schema(self, block):
        md5 = hashlib.md5(self.features.read_bytes()).hexdigest()
        self.features.with_name("features.schema.json").write_text(
            json.dumps({"md5": md5, "block_run_id": block}), encoding="utf-8")

    def test_a_table_built_from_another_block_is_refused_before_any_run(self):
        self.schema("5bc7deda64fcfa0a")  # model.toml [data] run_id is c5118b019d642a9b
        with self.assertRaisesRegex(ValueError, "built from block 5bc7deda64fcfa0a"):
            tracking.start_run("fault 5", "aniket", config=MODEL_TOML, features=self.features)
        self.assertIsNone(mlflow.active_run())

    def test_the_right_block_runs_and_is_tagged_with_it(self):
        self.schema("c5118b019d642a9b")
        with tracking.start_run("fault 5", "aniket", config=MODEL_TOML, features=self.features) as run:
            tags = mlflow.get_run(run.info.run_id).data.tags
        self.assertEqual(tags["block_run_id"], "c5118b019d642a9b")


if __name__ == "__main__":
    unittest.main()
