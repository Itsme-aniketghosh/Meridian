"""S4, the refresh job (refresh/job.py): the Spark-only config, the steps in the DAG's order, nothing new ends the run,
a failure alerts and exits 1, the block's pointer goes up after its file, D2's shape, and D5's log lines.

    python -m unittest discover -s Model-Deployment/tests -v

No network, no git, no data: the commands are recorded, not run.
"""
import io
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
from refresh import job  # noqa: E402

RUN = "abcdef0123456789"


class SparkOnly(unittest.TestCase):
    def setUp(self):
        self.text = job.MERIDIAN_TOML.read_text(encoding="utf-8")
        self.full, self.cfg = tomllib.loads(self.text), tomllib.loads(job.spark_only(self.text))

    def test_only_spark_and_its_team(self):
        self.assertEqual(set(self.cfg["datasets"]), {"spark"})
        self.assertEqual(set(self.cfg["teams"]), {"spark"})

    def test_follows_master_with_no_pinned_snapshot(self):
        spark = self.cfg["datasets"]["spark"]
        self.assertEqual(spark["repos"], [{"repo": "apache/spark", "ref": "master"}])
        self.assertNotIn("snapshot_at", spark)

    def test_the_rest_of_spark_and_every_other_section_unchanged(self):
        spark, was = self.cfg["datasets"]["spark"], self.full["datasets"]["spark"]
        self.assertEqual({k: v for k, v in spark.items() if k != "repos"},
                         {k: v for k, v in was.items() if k not in ("repos", "snapshot_at")})
        self.assertEqual(self.cfg["teams"]["spark"], self.full["teams"]["spark"])
        for section in set(self.full) - {"datasets", "teams"}:
            self.assertEqual(self.cfg[section], self.full[section], section)

    def test_a_layout_it_cannot_read_stops(self):
        with self.assertRaises(SystemExit):
            job.spark_only(self.text.replace('repo = "apache/spark", ref', 'repo = "apache/spark", pin'))


class SparkSettings(unittest.TestCase):
    def test_drops_only_the_migration_floors(self):
        text = job.SETTINGS_TOML.read_text(encoding="utf-8")
        old, new = tomllib.loads(text), tomllib.loads(job.spark_settings(text))
        v, w = old["validate"], new["validate"]
        self.assertEqual({k: n for k, n in v["rows"].items() if k not in job.MIGRATION_TABLES}, w["rows"])
        self.assertEqual({k: n for k, n in v["coverage"].items() if k not in job.MIGRATION_COVERAGE}, w["coverage"])
        self.assertIn("tickets_mapped_jira", w["coverage"])           # Spark's own coverage stays
        for t in ("git_commit", "bug_inducing", "ticket", "commit_ticket", "person_alias"):
            self.assertIn(t, w["rows"])
        for section in set(old) - {"validate"}:
            self.assertEqual(new[section], old[section], section)
        self.assertEqual({k: x for k, x in w.items() if k not in ("rows", "coverage")},
                         {k: x for k, x in v.items() if k not in ("rows", "coverage")})


class ModelToml(unittest.TestCase):
    def test_only_the_run_id_moves(self):
        text = job.MODEL_TOML.read_text(encoding="utf-8")
        old, new = tomllib.loads(text), tomllib.loads(job.model_toml_for(text, RUN))
        self.assertEqual(new["data"]["run_id"], RUN)
        new["data"]["run_id"] = old["data"]["run_id"]
        self.assertEqual(new, old)


class Store(unittest.TestCase):
    def test_local_round_trip_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            s = job.LocalStore(Path(d) / "store")
            src = Path(d) / "a.json"
            src.write_text("{}")
            s.put(src, "monitor", "reports/x.json")
            self.assertTrue(s.get("monitor", "reports/x.json", Path(d) / "back.json"))
            self.assertEqual((Path(d) / "back.json").read_text(), "{}")
            self.assertFalse(s.get("block", "latest.json", Path(d) / "none.json"))

    def test_make_store(self):
        self.assertIsInstance(job.make_store("local:/tmp/x", {}), job.LocalStore)
        self.assertIsInstance(job.make_store("gcs", {"buckets": {"block": "b"}}), job.GcsStore)
        with self.assertRaises(SystemExit):
            job.make_store("s3", {})


DRIFT = {"n_current": 454, "drifted_share": 0.625, "retrain": True,
         "reasons": ["PSI 0.28 >= 0.2 on la"], "window": {"days": 30},
         "features": {"la": {"psi": 0.28}, "nf": {"psi": 0.01}}}


class D2(unittest.TestCase):
    def test_contract_keys(self):
        r = job.d2(RUN, "2026-11-20T03:00:00Z", DRIFT)
        self.assertEqual(set(r), {"run_id", "at", "n_new", "drift", "decay", "retrain"})
        self.assertEqual(set(r["drift"]) - {"window"}, {"psi", "evidently_share", "flag", "why"})
        self.assertEqual(r["drift"]["psi"], {"la": 0.28, "nf": 0.01})
        self.assertTrue(r["retrain"])
        self.assertIsNone(r["decay"])

    def test_no_threshold_crossed(self):
        r = job.d2(RUN, "t", {**DRIFT, "retrain": False, "reasons": []})
        self.assertFalse(r["retrain"])
        self.assertEqual(r["drift"]["why"], "no threshold crossed")


class FakeJob(job.Job):
    """Records commands instead of running them, and makes the files a real run would."""

    def __init__(self, root, store, status=0, fail=None):
        super().__init__(root, store, out=io.StringIO())
        self.calls, self.status, self.fail = [], status, fail

    def sh(self, args, cwd, ok=(0,)):
        args = list(map(str, args))
        self.calls.append(args)
        mod = args[2] if args[1] == "-m" else args[1]
        if self.fail and self.fail in mod:
            raise job.StepFailed(f"exit 1: {mod}", "boom\n")
        if mod == "meridian.status":
            return self.status, ""
        if mod == "meridian.collect":
            return 0, f"... status complete\nrun_id={RUN}\n"
        if mod == "meridian.publish":
            pub = self.root / "published"
            pub.mkdir(parents=True, exist_ok=True)
            (pub / f"{RUN}.sqlite").write_bytes(b"block")
            (pub / "latest.json").write_text(json.dumps({"run_id": RUN, "file": f"{RUN}.sqlite"}))
        if mod == "monitor.drift":
            Path(args[args.index("--out") + 1]).write_text(json.dumps(DRIFT))
        return 0, ""

    def modules(self):
        return [a[2] if a[1] == "-m" else a[1] for a in self.calls]

    def lines(self):
        return [json.loads(x) for x in self.out.getvalue().splitlines()]


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"
        self.store = RecordingStore(Path(self.tmp.name) / "store")
        drift = DEPLOY / "monitor" / "drift.py"
        if not drift.exists():   # S5 isn't merged on this branch: the job only checks it's there
            self.skipTest("Model-Deployment/monitor/drift.py (S5) isn't here")

    def tearDown(self):
        self.tmp.cleanup()

    def test_steps_in_the_dags_order(self):
        j = FakeJob(self.root, self.store)
        with mock.patch.object(job, "alert") as alert:
            self.assertEqual(j.run(), 0)
        alert.assert_not_called()
        self.assertEqual(j.modules(), ["meridian.status", "meridian.collect", *[f"meridian.{p}" for p in job.PARTS],
                                       "meridian.publish", "model.data", "monitor.drift"])
        for call in j.calls[2:10]:
            self.assertEqual(call[call.index("--run-id") + 1], RUN)
        self.assertTrue(all(c[c.index("--config") + 1].endswith("meridian.spark.toml") for c in j.calls[:10]))

    def test_pointer_after_its_file_then_the_report(self):
        j = FakeJob(self.root, self.store)
        with mock.patch.object(job, "alert"):
            j.run()
        self.assertEqual(self.store.puts, [("block", f"{RUN}.sqlite"), ("block", "latest.json"),
                                           ("monitor", f"reports/{RUN}.json")])
        report = json.loads((self.store.root / "monitor" / "reports" / f"{RUN}.json").read_text())
        self.assertTrue(report["retrain"])
        self.assertEqual(report["run_id"], RUN)

    def test_nothing_new_ends_the_run(self):
        j = FakeJob(self.root, self.store, status=job.NOTHING_NEW)
        self.assertEqual(j.run(), 0)
        self.assertEqual(j.modules(), ["meridian.status"])
        self.assertEqual(self.store.puts, [])
        self.assertEqual(j.lines()[-1]["outcome"], "nothing new")

    def test_a_failed_part_alerts_and_exits_1(self):
        j = FakeJob(self.root, self.store, fail="meridian.validate")
        with mock.patch.object(job, "alert") as alert:
            self.assertEqual(j.run(), 1)
        self.assertIn("update", alert.call_args[0][0])
        self.assertNotIn("meridian.publish", j.modules())
        self.assertEqual(self.store.puts, [])
        self.assertEqual(j.lines()[-1]["severity"], "ERROR")

    def test_last_block_pulled_first(self):
        self.store.root.joinpath("block").mkdir(parents=True)
        (self.store.root / "block" / "latest.json").write_text(json.dumps({"run_id": "1" * 16, "file": "old.sqlite"}))
        (self.store.root / "block" / "old.sqlite").write_bytes(b"old")
        j = FakeJob(self.root, self.store, status=job.NOTHING_NEW)
        j.run()
        self.assertEqual((self.root / "published" / "old.sqlite").read_bytes(), b"old")
        self.assertEqual(j.lines()[0]["last_run"], "1" * 16)

    def test_d5_lines(self):
        j = FakeJob(self.root, self.store)
        with mock.patch.object(job, "alert"):
            j.run()
        for line in j.lines():
            self.assertTrue({"severity", "event", "model_version", "latency_ms"} <= set(line), line)
            self.assertIn(line["event"], ("refresh", "drift", "retrain"))
            self.assertIsInstance(line["latency_ms"], int)
            self.assertNotIn("@", json.dumps(line))   # no email, ever


class RecordingStore(job.LocalStore):
    def __init__(self, root):
        super().__init__(root)
        self.puts = []

    def put(self, src, bucket, name):
        self.puts.append((bucket, name))
        super().put(src, bucket, name)


if __name__ == "__main__":
    unittest.main()
