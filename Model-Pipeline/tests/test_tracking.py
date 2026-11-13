"""model.tracking: the Contract 3 tags on every run, the three experiments, LLM calls, and clear failures.

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
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import mlflow

from model import tracking
from tests import mlflow_uri

BLOCK = "c5118b019d642a9b"
FEATURES = b"not really parquet: only its bytes are hashed"
_env = None
_tmp = None


def setUpModule():
    global _env, _tmp
    _tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
    _env = mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": mlflow_uri(_tmp.name)})
    _env.start()


def tearDownModule():
    while mlflow.active_run():
        mlflow.end_run()
    _env.stop()
    _tmp.cleanup()


def call(**kw):
    """One valid log_llm_call, with any field changed."""
    args = dict(agent="writer", model="small", prompt_version="v1", tokens_in=800, tokens_out=100,
                cost_usd=0.0004, cache_hit=False, gate_pass=True)
    args.update(kw)
    tracking.log_llm_call(**args)


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

        def md5(run):
            return self.client.get_run(run.info.run_id).data.tags["features_md5"]
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
        for owner in ("", "Rochan", "rochan h", None, "ai1", "rochan-tracking"):  # only the six packet owners
            self.assertRefused(ValueError, "owner", owner=owner, **self.paths())
        for decision in ("4", "rochan4", "Rochan-4", "ai1-3"):
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


class LLMCalls(Base):
    def agents_run(self, name="writer", nested=False):
        return tracking.start_run(name, owner="vishwa", experiment="meridian-agents", block_run_id=BLOCK,
                                  nested=nested, meridian_config=self.meridian, config=self.config)

    def table(self, run):
        return tracking.load_llm_calls([run.info.run_id]).sort_values("call").reset_index(drop=True)

    def metrics(self, run):
        return self.client.get_run(run.info.run_id).data.metrics

    def test_one_row_per_call_and_the_totals_with_their_n(self):
        with self.agents_run() as run:
            call(tokens_in=1000, tokens_out=200, cost_usd=0.002, gate_pass=True, repo="apache/spark", team="sql")
            call(tokens_in=0, tokens_out=0, cost_usd=0, cache_hit=True, gate_pass=True)
            call(tokens_in=500, tokens_out=50, cost_usd=0.001, gate_pass=False, source="template")
            call(agent="jev", tokens_in=0, tokens_out=0, cost_usd=0, cache_hit=True, gate_pass=None, route="jev")
        t = self.table(run)
        self.assertEqual(list(t["call"]), [1, 2, 3, 4])
        self.assertEqual(set(tracking.CALL_COLUMNS) | {"run_id"}, set(t.columns))
        self.assertEqual(t.loc[0, "team"], "sql")
        self.assertEqual(t.loc[3, "route"], "jev")
        m = self.metrics(run)
        self.assertEqual(m["llm_calls"], 4)
        self.assertEqual(m["llm_api_calls"], 2)
        self.assertEqual(m["cache_hit_rate"], 0.5)
        self.assertEqual(m["gate_checked"], 3)  # the Jev pick isn't gated: not in the rate's N
        self.assertAlmostEqual(m["gate_pass_rate"], 2 / 3)
        self.assertEqual(m["tokens_in"], 1500)
        self.assertEqual(m["tokens_out"], 250)
        self.assertAlmostEqual(m["cost_usd"], 0.003)

    def test_rows_written_in_batches_add_up(self):
        with mock.patch.object(tracking, "FLUSH_EVERY", 3), self.agents_run() as run:
            for _ in range(7):
                call()
        t = self.table(run)
        self.assertEqual(list(t["call"]), list(range(1, 8)))
        history = self.client.get_metric_history(run.info.run_id, "llm_calls")
        self.assertEqual(sorted(h.step for h in history), [3, 6, 7])

    def test_calls_from_worker_threads(self):
        with self.agents_run() as run:
            with ThreadPoolExecutor(8) as pool:
                list(pool.map(lambda i: call(task=f"t{i}"), range(400)))
        t = self.table(run)
        self.assertEqual(len(t), 400)
        self.assertEqual(sorted(t["call"]), list(range(1, 401)))
        self.assertEqual(self.metrics(run)["llm_calls"], 400)

    def test_a_failed_run_keeps_its_calls(self):
        with self.assertRaises(RuntimeError):
            with self.agents_run() as run:
                call()
                call()
                raise RuntimeError("the model went away")
        self.assertEqual(len(self.table(run)), 2)
        self.assertEqual(self.client.get_run(run.info.run_id).info.status, "FAILED")

    def test_no_agents_run_open(self):
        with self.assertRaisesRegex(RuntimeError, "no meridian-agents run is open"):
            call()
        with tracking.start_run("model", owner="rachna", **self.paths()):
            with self.assertRaisesRegex(ValueError, "isn't in meridian-agents"):
                call()

    def test_two_agents_runs_from_a_thread_need_run_id(self):
        with self.agents_run("a") as a:
            with self.agents_run("b", nested=True):
                with ThreadPoolExecutor(1) as pool:
                    with self.assertRaisesRegex(RuntimeError, "pass run_id"):
                        pool.submit(call).result()
                    pool.submit(lambda: call(run_id=a.info.run_id)).result()
        self.assertEqual(len(self.table(a)), 1)

    def test_bad_values(self):
        bad = [dict(team="jane.doe@example.com"), dict(task="ask a@b.io"), dict(tokens_in=-1),
               dict(tokens_out=1.5), dict(cost_usd=float("nan")), dict(cost_usd=None), dict(cache_hit="yes"),
               dict(gate_pass=1), dict(source="gpt"), dict(route="llm"), dict(agent=""), dict(latency_ms=-3)]
        with self.agents_run() as run:
            for kw in bad:
                with self.subTest(**{k: repr(v) for k, v in kw.items()}), self.assertRaises(ValueError):
                    call(**kw)
            call(model="some-model@20250101", latency_ms=420)  # an @ in a model id is fine
        self.assertEqual(len(self.table(run)), 1)


if __name__ == "__main__":
    unittest.main()
