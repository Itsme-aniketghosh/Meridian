"""model.tracking: the Contract 3 tags on every run, the three experiments, and clear failures.

Every test writes to one temporary SQLite store (made once: a new store costs about 2 s of migrations),
never to the repo's mlflow.db. Run from Model-Pipeline/: python -m pytest tests/test_tracking.py
"""
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import mlflow

from model import tracking

BLOCK = "c5118b019d642a9b"
FEATURES = b"not really parquet: only its bytes are hashed"
_env = None
_tmp = None


def setUpModule():
    global _env, _tmp
    _tmp = tempfile.TemporaryDirectory()
    _env = mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": f"sqlite:///{_tmp.name}/mlflow.db"})
    _env.start()


def tearDownModule():
    while mlflow.active_run():
        mlflow.end_run()
    _env.stop()
    _tmp.cleanup()


class Base(unittest.TestCase):
    """A model.toml, meridian.toml and feature table with its schema, in a folder of their own."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=_tmp.name))
        self.config = self.dir / "model.toml"
        self.config.write_text(f'[data]\nrun_id = "{BLOCK}"\nwindow_days = 365\n[seed]\nvalue = 1\n')
        self.meridian = self.dir / "meridian.toml"
        self.meridian.write_text('[run]\nname = "test"\n')
        self.features = self.dir / "features.parquet"
        self.features.write_bytes(FEATURES)
        self.write_schema(hashlib.md5(FEATURES).hexdigest(), BLOCK)
        self.client = mlflow.MlflowClient()

    def tearDown(self):
        while mlflow.active_run():
            mlflow.end_run()

    def write_schema(self, md5, block):
        (self.dir / "features.schema.json").write_text(json.dumps({"md5": md5, "block_run_id": block}))

    def paths(self, **kw):
        return {"config": self.config, "meridian_config": self.meridian, "features": self.features, **kw}

    def run_count(self):
        ids = [e.experiment_id for e in self.client.search_experiments()]
        return len(self.client.search_runs(ids)) if ids else 0


class Tags(Base):
    def test_a_model_run_has_every_contract_3_tag(self):
        with tracking.start_run("lgbm", owner="rachna", **self.paths()) as run:
            pass
        r = self.client.get_run(run.info.run_id)
        tags = r.data.tags
        head = subprocess.run(["git", "-C", str(tracking.ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        self.assertEqual(tags["owner"], "rachna")
        self.assertEqual(tags["block_run_id"], BLOCK)  # from model.toml [data] run_id
        self.assertEqual(tags["git_sha"], head)
        self.assertIn(tags["git_dirty"], {"true", "false"})
        self.assertEqual(tags["config_sha256"], hashlib.sha256(self.config.read_bytes()).hexdigest())
        self.assertEqual(tags["meridian_config_sha256"], hashlib.sha256(self.meridian.read_bytes()).hexdigest())
        self.assertEqual(tags["features_md5"], hashlib.md5(FEATURES).hexdigest())
        self.assertEqual(r.info.run_name, "lgbm")
        self.assertEqual(r.info.status, "FINISHED")
        self.assertEqual(self.client.get_experiment(r.info.experiment_id).name, "meridian-defect-risk")

    def test_a_changed_table_gets_a_new_md5(self):
        with tracking.start_run("a", owner="rachna", **self.paths()) as first:
            pass
        self.features.write_bytes(FEATURES + b" and a new row")
        self.write_schema(hashlib.md5(self.features.read_bytes()).hexdigest(), BLOCK)
        with tracking.start_run("b", owner="rachna", **self.paths()) as second:
            pass
        md5 = lambda run: self.client.get_run(run.info.run_id).data.tags["features_md5"]
        self.assertNotEqual(md5(first), md5(second))
        self.assertEqual(md5(second), hashlib.md5(self.features.read_bytes()).hexdigest())

    def test_decision_and_extra_tags(self):
        with tracking.start_run("d4", owner="rochan", decision="rochan-4", tags={"reps": 1000},
                                **self.paths()) as run:
            pass
        tags = self.client.get_run(run.info.run_id).data.tags
        self.assertEqual(tags["decision"], "rochan-4")
        self.assertEqual(tags["reps"], "1000")

    def test_agents_and_auditor_runs_need_the_block_and_not_the_table(self):
        with self.assertRaisesRegex(ValueError, "block_run_id"):
            tracking.start_run("writer", owner="vishwa", experiment="meridian-agents", **self.paths())
        for exp in ("meridian-agents", "meridian-auditor"):
            with tracking.start_run("x", owner="vishwa", experiment=exp, block_run_id="fixture01",
                                    meridian_config=self.meridian, config=self.dir / "none.toml") as run:
                pass
            r = self.client.get_run(run.info.run_id)
            self.assertEqual(self.client.get_experiment(r.info.experiment_id).name, exp)
            self.assertEqual(r.data.tags["block_run_id"], "fixture01")
            self.assertEqual(r.data.tags["config_sha256"], "missing")  # no model.toml: said, not hidden
            self.assertNotIn("features_md5", r.data.tags)

    def test_nested_runs_carry_the_tags(self):
        with tracking.start_run("tune", owner="rachna", **self.paths()) as parent:
            with tracking.start_run("trial-0", owner="rachna", nested=True, **self.paths()) as child:
                pass
        tags = self.client.get_run(child.info.run_id).data.tags
        self.assertEqual(tags["mlflow.parentRunId"], parent.info.run_id)
        self.assertEqual(tags["features_md5"], hashlib.md5(FEATURES).hexdigest())

    def test_an_exception_marks_the_run_failed_and_still_raises(self):
        with self.assertRaises(KeyError):
            with tracking.start_run("boom", owner="rachna", **self.paths()) as run:
                raise KeyError("x")
        self.assertEqual(self.client.get_run(run.info.run_id).info.status, "FAILED")
        self.assertIsNone(mlflow.active_run())

    def test_the_print_command_logs_nothing(self):
        before = self.run_count()
        out = io.StringIO()
        with redirect_stdout(out):
            tags = tracking.main(["--owner", "rochan", "--config", str(self.config), "--meridian-config",
                                  str(self.meridian), "--features", str(self.features)])
        self.assertEqual(json.loads(out.getvalue())["tags"], tags)
        self.assertEqual(self.run_count(), before)


class Refusals(Base):
    """Each bad call fails with a message that says what to fix, before any run is made."""

    def assertRefused(self, error, pattern, **kw):
        before = self.run_count()
        with self.assertRaisesRegex(error, pattern):
            tracking.start_run(kw.pop("name", "x"), kw.pop("owner", "rochan"), **kw)
        self.assertEqual(self.run_count(), before)
        self.assertIsNone(mlflow.active_run())

    def test_no_feature_table(self):
        self.features.unlink()
        self.assertRefused(FileNotFoundError, "feature table", **self.paths())

    def test_no_model_toml(self):
        self.assertRefused(FileNotFoundError, "model.toml", **self.paths(config=self.dir / "none.toml"))

    def test_table_and_schema_from_different_builds(self):
        self.write_schema("0" * 32, BLOCK)
        self.assertRefused(ValueError, "different builds", **self.paths())

    def test_table_from_another_block(self):
        self.write_schema(hashlib.md5(FEATURES).hexdigest(), "5bc7deda64fcfa0a")
        self.assertRefused(ValueError, "built from block 5bc7deda64fcfa0a", **self.paths())

    def test_unknown_experiment_makes_no_experiment(self):
        self.assertRefused(ValueError, "Contract 3", experiment="meridian-defect", **self.paths())
        self.assertIsNone(self.client.get_experiment_by_name("meridian-defect"))

    def test_owner_and_decision_format(self):
        for owner in ("", "Rochan", "rochan h", None):
            self.assertRefused(ValueError, "owner", owner=owner, **self.paths())
        for decision in ("4", "rochan4", "Rochan-4"):
            self.assertRefused(ValueError, "decision", decision=decision, **self.paths())

    def test_tracking_tags_cant_be_overwritten(self):
        self.assertRefused(ValueError, "set by tracking.py", tags={"git_sha": "abc"}, **self.paths())

    def test_no_run_name(self):
        self.assertRefused(ValueError, "run name", name=" ", **self.paths())


class Store(unittest.TestCase):
    def test_default_store_is_the_repo_root_from_any_folder(self):
        with mock.patch.dict(os.environ, clear=True):
            uri = tracking.tracking_uri()
        self.assertEqual(uri, f"sqlite:///{tracking.ROOT / 'mlflow.db'}")
        self.assertTrue((tracking.ROOT / "Model-Pipeline").is_dir())

    def test_the_env_var_wins(self):
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": "sqlite:////elsewhere/m.db"}):
            self.assertEqual(tracking.tracking_uri(), "sqlite:////elsewhere/m.db")

    def test_artifacts_sit_next_to_the_store(self):
        eid = tracking.experiment_id("meridian-auditor")
        loc = mlflow.MlflowClient().get_experiment(eid).artifact_location
        self.assertEqual(loc, (Path(_tmp.name).resolve() / "mlruns" / "meridian-auditor").as_uri())

    def test_outside_git_the_sha_comes_from_the_env(self):
        tracking.git_state.cache_clear()
        try:
            with mock.patch("subprocess.run", side_effect=FileNotFoundError("git")), \
                    mock.patch.dict(os.environ, {"MERIDIAN_GIT_SHA": "abc123"}):
                self.assertEqual(tracking.git_state(), ("abc123", "unknown"))
        finally:
            tracking.git_state.cache_clear()


if __name__ == "__main__":
    unittest.main()
