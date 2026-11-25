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
    def test_drops_only_the_migration_floors(self):   # and sets szz_workers (SzzWorkers)
        text = job.SETTINGS_TOML.read_text(encoding="utf-8")
        old, new = tomllib.loads(text), tomllib.loads(job.spark_settings(text))
        v, w = old["validate"], new["validate"]
        self.assertEqual({k: n for k, n in v["rows"].items() if k not in job.MIGRATION_TABLES}, w["rows"])
        self.assertEqual({k: n for k, n in v["coverage"].items() if k not in job.MIGRATION_COVERAGE}, w["coverage"])
        self.assertIn("tickets_mapped_jira", w["coverage"])           # Spark's own coverage stays
        for t in ("git_commit", "bug_inducing", "ticket", "commit_ticket", "person_alias"):
            self.assertIn(t, w["rows"])
        for section in set(old) - {"validate", "history"}:
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

    def __init__(self, root, store, status=0, fail=None, serve_history=None):
        super().__init__(root, store, out=io.StringIO(), serve_history=serve_history)
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
        if mod == "serve.precompute":
            Path(args[args.index("--out") + 1]).write_bytes(b"history state")
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
        # S2's precompute: absent here unless a test says otherwise, so the result doesn't depend on #148 being merged
        patch = mock.patch.object(job, "PRECOMPUTE", Path(self.tmp.name) / "no-precompute.py")
        patch.start()
        self.addCleanup(patch.stop)

    def with_precompute(self):
        here = Path(self.tmp.name) / "precompute.py"
        here.write_text("")
        patch = mock.patch.object(job, "PRECOMPUTE", here)
        patch.start()
        self.addCleanup(patch.stop)

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

    def test_without_s2s_precompute_history_is_skipped_and_logged(self):
        j = FakeJob(self.root, self.store)
        with mock.patch.object(job, "alert"):
            self.assertEqual(j.run(), 0)
        self.assertNotIn("serve.precompute", j.modules())
        line = next(x for x in j.lines() if x["step"] == "history_pkl")
        self.assertTrue(line["history"].startswith("skipped"))
        self.assertFalse([p for p in self.store.puts if p[1].startswith("history")])

    def test_history_pkl_is_made_from_the_new_block_after_publish(self):
        self.with_precompute()
        j = FakeJob(self.root, self.store)
        with mock.patch.object(job, "alert"):
            self.assertEqual(j.run(), 0)
        mods = j.modules()
        self.assertEqual(mods[mods.index("meridian.publish") + 1], "serve.precompute")
        call = j.calls[mods.index("serve.precompute")]
        self.assertEqual(call[call.index("--block") + 1], str(self.root / "published" / f"{RUN}.sqlite"))
        self.assertEqual(call[call.index("--run-id") + 1], RUN)
        line = next(x for x in j.lines() if x["step"] == "history_pkl")
        self.assertEqual(line["history_md5"], "b38455638c286570b5786f99de19ba0d")   # md5 of b"history state"

    def test_history_pkl_goes_up_after_the_block_and_before_the_pointer(self):
        self.with_precompute()
        j = FakeJob(self.root, self.store)
        with mock.patch.object(job, "alert"):
            j.run()
        self.assertEqual(self.store.puts, [("block", f"{RUN}.sqlite"), ("cache", f"history/{RUN}.pkl"),
                                           ("cache", "history/latest.pkl"), ("block", "latest.json"),
                                           ("monitor", f"reports/{RUN}.json")])
        self.assertEqual((self.store.root / "cache" / "history" / "latest.pkl").read_bytes(), b"history state")
        self.assertFalse((self.store.root / "block" / "history.pkl").exists())   # no root history.pkl any more

    def test_history_pkl_goes_where_deploy_toml_serve_history_says(self):
        self.with_precompute()
        j = FakeJob(self.root, self.store, serve_history={"bucket": "block", "object": "history/latest.pkl"})
        with mock.patch.object(job, "alert"):
            j.run()
        self.assertIn(("block", f"history/{RUN}.pkl"), self.store.puts)
        self.assertIn(("block", "history/latest.pkl"), self.store.puts)
        self.assertNotIn(("block", "history.pkl"), self.store.puts)

    def test_main_reads_serve_history_from_deploy_toml(self):
        with mock.patch.object(job, "Job") as J:
            job.main(["--store", f"local:{self.root / 'store'}", "--dry-run"])
        cfg = job.deploy_config.load()
        self.assertEqual(J.call_args.kwargs["serve_history"], cfg["serve_history"])

    def test_a_failed_precompute_alerts_and_pushes_nothing(self):
        self.with_precompute()
        j = FakeJob(self.root, self.store, fail="serve.precompute")
        with mock.patch.object(job, "alert") as alert:
            self.assertEqual(j.run(), 1)
        self.assertIn("history", alert.call_args[0][0])
        self.assertEqual(self.store.puts, [])

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


class ReviewFixes(unittest.TestCase):
    """The five review fixes (Nov 24)."""

    def test_the_gcs_token_is_fetched_for_every_call(self):
        store = job.GcsStore({"block": "b"})
        with mock.patch.object(store, "token", side_effect=["t1", "t2"]) as token, \
                mock.patch.object(job.urllib.request, "urlopen") as urlopen:
            store._call("https://x/1")
            store._call("https://x/2")
        self.assertEqual(token.call_count, 2)
        sent = [c.args[0].get_header("Authorization") for c in urlopen.call_args_list]
        self.assertEqual(sent, ["Bearer t1", "Bearer t2"])

    def test_an_upload_streams_the_file(self):
        store = job.GcsStore({"block": "b"})
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "x.sqlite"
            src.write_bytes(b"0" * 5000)
            with mock.patch.object(store, "_call") as call:
                store.put(src, "block", "x.sqlite")
        body = call.call_args.args[1]
        self.assertTrue(hasattr(body, "read"), "the body is the open file, not its bytes")
        self.assertEqual(call.call_args.kwargs["size"], 5000)

    def test_the_model_version_is_read_for_every_line(self):
        out = io.StringIO()
        j = job.Job(Path(tempfile.mkdtemp()), job.LocalStore(tempfile.mkdtemp()), out=out)
        with mock.patch.object(job, "model_version", side_effect=["old", "new"]):
            j.event("a", 0)
            j.event("b", 0)
        self.assertEqual([json.loads(x)["model_version"] for x in out.getvalue().splitlines()], ["old", "new"])

    def test_scrub_hides_emails_tokens_and_credentials(self):
        text = ("Author: Jane <jane.doe@example.com> token=abc123 Authorization: Bearer ya29.AbC-def "
                "https://hooks.slack.com/services/T0/B0/xyz https://user:pw@github.com/x "
                "https://storage.googleapis.com/o?X-Goog-Signature=deadbeef&a=1 ghp_" + "a" * 36)
        out = job.scrub(text)
        for secret in ("jane.doe@example.com", "abc123", "ya29.AbC-def", "hooks.slack.com/services", "pw@",
                       "deadbeef", "ghp_aaaa"):
            self.assertNotIn(secret, out)
        self.assertIn("<email>", out)

    def test_scrub_keeps_an_ordinary_error(self):
        for line in ("FileNotFoundError: published/latest.json (exit 1: meridian.collect)",
                     "sqlite3.IntegrityError: UNIQUE constraint failed: the primary key of commit",
                     "validate: 3 errors, token budget fine"):
            self.assertEqual(job.scrub(line), line)

    def test_a_broken_guard_withholds_the_text(self):
        with mock.patch.object(job, "SCRUB", [(None, "")]):
            self.assertEqual(job.scrub("anything"), "<output withheld: the guard failed>")

    def test_a_failure_alert_carries_no_email_or_token(self):
        root = Path(tempfile.mkdtemp())
        j = FakeJob(root, RecordingStore(root / "store"), fail="meridian.scan")
        orig = j.sh

        def sh(args, cwd, ok=(0,)):
            if "meridian.scan" in map(str, args):
                raise job.StepFailed("exit 1: meridian.scan for dev@corp.io",
                                     "x\n" * 12 + "commit by dev@corp.io\nBearer ya29.secret\n")
            return orig(args, cwd, ok)
        j.sh = sh
        with mock.patch.object(job, "alert") as alert:
            self.assertEqual(j.run(), 1)
        subject, lines = alert.call_args[0]
        text = subject + "\n".join(lines)
        self.assertNotIn("dev@corp.io", text)
        self.assertNotIn("ya29.secret", text)
        self.assertLessEqual(len(lines), 10)
        error = next(x for x in j.lines() if x.get("outcome") == "failed")["error"]
        self.assertNotIn("dev@corp.io", error)

    def test_the_image_has_the_tools_collect_runs(self):
        # collect shells out to git (the clone) and curl (Jira): a dry run never calls them, a real run does
        text = (DEPLOY / "refresh" / "Dockerfile").read_text(encoding="utf-8")
        install = next(x for x in text.splitlines() if "apt-get install" in x)
        for tool in ("git", "curl", "libgomp1", "ca-certificates"):
            self.assertIn(f" {tool}", install, tool)

    def test_the_image_has_a_pinned_base_and_no_root(self):
        text = (DEPLOY / "refresh" / "Dockerfile").read_text(encoding="utf-8")
        base = next(x for x in text.splitlines() if x.startswith("FROM "))
        self.assertRegex(base, r"^FROM python:3\.11\.\d+-slim-\w+@sha256:[0-9a-f]{64}$")
        user = [x for x in text.splitlines() if x.startswith("USER ")]
        self.assertTrue(user and user[-1].split()[1].split(":")[0] not in ("0", "root"), user)


class SzzWorkers(unittest.TestCase):
    def test_the_job_runs_one_szz_process_per_cpu(self):
        text = (DEPLOY.parent / "Data-Pipeline" / "settings.toml").read_text(encoding="utf-8")
        cfg = tomllib.loads(job.spark_settings(text))
        with open(DEPLOY / "deploy.toml", "rb") as f:
            cpus = int(tomllib.load(f)["jobs"]["refresh"]["cpu"])
        self.assertEqual(cfg["history"]["szz_workers"], cpus)
        self.assertEqual(job.SZZ_WORKERS, cpus)

    def test_the_rest_of_history_is_kept(self):
        text = (DEPLOY.parent / "Data-Pipeline" / "settings.toml").read_text(encoding="utf-8")
        old, new = tomllib.loads(text)["history"], tomllib.loads(job.spark_settings(text))["history"]
        self.assertEqual({k: v for k, v in old.items() if k != "szz_workers"},
                         {k: v for k, v in new.items() if k != "szz_workers"})


class ImagePackages(unittest.TestCase):
    """refresh/requirements.txt: the job's packages only, each at the pipeline's own pin."""

    @staticmethod
    def pins(path):
        out = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-r"):
                name, version = line.split("==")
                out[name.split("[")[0].lower()] = version
        return out

    def test_every_pin_matches_the_pipeline(self):
        repo = DEPLOY.parent
        source = {**self.pins(repo / "Data-Pipeline" / "requirements.txt"),
                  **self.pins(repo / "Model-Pipeline" / "requirements.txt")}
        mine = self.pins(DEPLOY / "refresh" / "requirements.txt")
        self.assertTrue(mine)
        for name, version in mine.items():
            self.assertEqual(source.get(name), version, name)

    def test_what_the_job_never_imports_is_left_out(self):
        mine = self.pins(DEPLOY / "refresh" / "requirements.txt")
        for name in ("dvc", "dvc-gs", "gcsfs", "anthropic", "google-genai", "shap"):
            self.assertNotIn(name, mine)


class Caches(unittest.TestCase):
    """S10: snapshots/_cache goes to the cache bucket after a run and comes back before the next."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = RecordingStore(self.tmp / "store")

    def job_at(self, name):
        return job.Job(self.tmp / name, self.store, out=io.StringIO())

    def fill(self, root):
        cache = root / "snapshots" / "_cache"
        (cache / "szz").mkdir(parents=True)
        (cache / "szz" / "apache__spark.json").write_text('{"fix": "blamed"}')
        (cache / "repos" / "apache__spark.git" / "objects").mkdir(parents=True)
        (cache / "repos" / "apache__spark.git" / "objects" / "pack").write_bytes(b"\x00pack" * 100)
        return cache

    def test_no_cache_yet_is_a_cold_run(self):
        self.assertEqual(self.job_at("a").restore_cache(), {"cache": "cold: none in the store yet"})

    def test_a_saved_cache_comes_back_on_an_empty_disk(self):
        self.fill(self.tmp / "a")
        saved = self.job_at("a").save_cache()
        self.assertEqual(saved["cache"], "saved")
        self.assertIn(("cache", job.CACHE_OBJECT), self.store.puts)
        got = self.job_at("b").restore_cache()
        self.assertEqual(got["cache"], "restored")
        cache = self.tmp / "b" / "snapshots" / "_cache"
        self.assertEqual((cache / "szz" / "apache__spark.json").read_text(), '{"fix": "blamed"}')
        self.assertEqual((cache / "repos" / "apache__spark.git" / "objects" / "pack").read_bytes(), b"\x00pack" * 100)
        self.assertFalse((self.tmp / "b" / "snapshots" / "_cache.tar.gz").exists())   # the archive is removed
        self.assertFalse((self.tmp / "a" / "snapshots" / "_cache.tar.gz").exists())

    def test_a_broken_archive_leaves_the_disk_as_it_was(self):
        cache = self.fill(self.tmp / "b")
        bad = self.tmp / "bad.tar.gz"
        bad.write_bytes(b"not a tar")
        self.store.put(bad, "cache", job.CACHE_OBJECT)
        got = self.job_at("b").restore_cache()
        self.assertTrue(got["cache"].startswith("cold: restore failed"), got)
        self.assertEqual((cache / "szz" / "apache__spark.json").read_text(), '{"fix": "blamed"}')

    def test_an_archive_escaping_the_folder_is_refused(self):
        import tarfile
        evil = self.tmp / "evil.tar.gz"
        payload = self.tmp / "payload"
        payload.write_text("x")
        with tarfile.open(evil, "w:gz") as t:
            t.add(payload, arcname="../../escaped")
        self.store.put(evil, "cache", job.CACHE_OBJECT)
        got = self.job_at("c").restore_cache()
        self.assertTrue(got["cache"].startswith("cold"), got)
        self.assertFalse((self.tmp / "escaped").exists())

    def test_a_failed_save_warns_and_the_run_succeeds(self):
        root = self.tmp / "d"
        self.fill(root)
        j = FakeJob(root, self.store)
        with mock.patch.object(self.store, "put", side_effect=self.store.put) as put:
            def fail_cache(src, bucket, name):
                if name == job.CACHE_OBJECT:
                    raise OSError("bucket unreachable")
                return job.LocalStore.put(self.store, src, bucket, name)
            put.side_effect = fail_cache
            with mock.patch.object(job, "alert") as alert:
                self.assertEqual(j.run(), 0)
        alert.assert_not_called()
        line = next(x for x in j.lines() if x["step"] == "save_cache")
        self.assertEqual(line["severity"], "WARNING")
        self.assertTrue(line["cache"].startswith("not saved"))

    def test_restore_before_the_status_check_and_save_after_the_push(self):
        root = self.tmp / "e"
        self.fill(root)
        j = FakeJob(root, self.store)
        with mock.patch.object(job, "alert"):
            self.assertEqual(j.run(), 0)
        steps = [x["step"] for x in j.lines()]
        self.assertLess(steps.index("restore_cache"), steps.index("anything_new"))
        self.assertLess(steps.index("push"), steps.index("save_cache"))
        self.assertEqual(self.store.puts[-1], ("cache", job.CACHE_OBJECT))   # after the block, history and report

    def test_nothing_new_saves_nothing(self):
        root = self.tmp / "f"
        self.fill(root)
        j = FakeJob(root, self.store, status=job.NOTHING_NEW)
        with mock.patch.object(job, "alert"):
            self.assertEqual(j.run(), 0)
        self.assertNotIn(("cache", job.CACHE_OBJECT), self.store.puts)

    def test_the_cache_is_never_where_the_deploy_account_reads(self):
        with open(DEPLOY / "deploy.toml", "rb") as f:
            history = tomllib.load(f)["serve_history"]["object"]
        self.assertFalse(job.CACHE_OBJECT.startswith(history.rsplit("/", 1)[0] + "/"))


class RecordingStore(job.LocalStore):
    def __init__(self, root):
        super().__init__(root)
        self.puts = []

    def put(self, src, bucket, name):
        self.puts.append((bucket, name))
        super().put(src, bucket, name)


if __name__ == "__main__":
    unittest.main()
