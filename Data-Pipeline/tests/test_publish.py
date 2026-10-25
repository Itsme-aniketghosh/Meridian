"""Publish, compare and status tests: what the two DAGs do after validate, and the "anything new?" step. Small temp
data roots, a tiny git repo as the mirror; alerts are mocked.

    python -m unittest tests.test_publish -v      # from Data-Pipeline/
"""
import io, json, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.compare as C
import meridian.publish as Pub
import meridian.status as S
from meridian import alert
from meridian.common import git

CONFIG = """\
[run]
handoff_dir = "out/{run_id}"
[datasets.x]
repos = [{ repo = "o/r", ref = "HEAD" }, { repo = "o/gone", ref = "HEAD" }]
"""
SETTINGS = """\
[publish]
dir = "published"
[status]
mirrors = "mirrors"
stale_after_hours = 24
[dvc]
push = false
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config, self.settings = self.tmp / "meridian.toml", self.tmp / "settings.toml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.settings.write_text(SETTINGS, encoding="utf-8")

    def block(self, root, run_id="R1", anomalies=(), shas=None, db=b"block"):
        out = root / "out" / run_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "meridian.sqlite").write_bytes(db)
        (out / "anomalies.json").write_text(json.dumps(list(anomalies)), encoding="utf-8")
        (out / "run.jsonl").write_text(json.dumps({"run_id": run_id, "snapshot_at": "2026-10-01T00:00:00Z",
                                                   "repo_shas": json.dumps(shas or {"o/r": "a" * 40})}) + "\n")
        return out

    def publish(self, *a, **k):
        with redirect_stdout(io.StringIO()):
            return Pub.main(*a, config=self.config, root=self.tmp, settings=self.settings, **k)


class Publish(Base):
    def test_a_validated_run_is_copied_and_becomes_latest(self):
        self.block(self.tmp, anomalies=[["warn", "missing", "t.c", "10%"]])   # warnings don't stop it
        doc = self.publish("R1", prev_run="R0")
        pub = self.tmp / "published"
        self.assertEqual((pub / "R1.sqlite").read_bytes(), b"block")
        self.assertEqual(json.loads((pub / "latest.json").read_text()), doc)
        self.assertEqual((doc["run_id"], doc["prev_run"], doc["file"], doc["repo_shas"]), ("R1", "R0", "R1.sqlite", {"o/r": "a" * 40}))
        self.assertEqual(sorted(p.name for p in pub.iterdir()), ["R1.sqlite", "latest.json"])   # no tmp left

    def test_errors_or_no_validate_refuse_and_the_last_good_block_stays_live(self):
        self.block(self.tmp)
        self.publish("R1")
        self.block(self.tmp, "R2", anomalies=[["error", "formats", "t.c", "bad"]])
        with self.assertRaisesRegex(SystemExit, "1 data errors"):
            self.publish("R2")
        (self.block(self.tmp, "R3") / "anomalies.json").unlink()
        with self.assertRaisesRegex(SystemExit, "validate R3 first"):
            self.publish("R3")
        self.assertEqual(Pub.latest(self.tmp, self.settings)["run_id"], "R1")
        self.assertFalse((self.tmp / "published" / "R2.sqlite").exists())

    def test_a_rebuild_publishes_beside_the_update_never_over_it(self):
        self.block(self.tmp)
        self.publish("R1")
        self.block(self.tmp / "rebuild", db=b"rebuilt")
        self.publish("R1", from_root=self.tmp / "rebuild")
        pub = self.tmp / "published"
        self.assertEqual(((pub / "R1.sqlite").read_bytes(), (pub / "R1.rebuild.sqlite").read_bytes()), (b"block", b"rebuilt"))
        self.assertEqual(Pub.latest(self.tmp, self.settings)["file"], "R1.rebuild.sqlite")


class Compare(Base):
    def tables(self, root, **t):
        out = self.block(root)
        for name, lines in t.items():
            (out / f"{name}.jsonl").write_text("".join(f"{x}\n" for x in lines), encoding="utf-8", newline="\n")

    def run_it(self):
        with redirect_stdout(io.StringIO()), mock.patch.object(alert, "send") as sent:
            try:
                C.main("R1", self.tmp / "b", self.config, self.tmp / "a", self.settings)
                failed = None
            except SystemExit as e:
                failed = str(e)
        return failed, sent

    def test_same_tables_pass_and_run_jsonl_is_left_out(self):
        self.tables(self.tmp / "a", t=[1, 2], u=[3])
        self.tables(self.tmp / "b", t=[1, 2], u=[3])
        (self.tmp / "b" / "out" / "R1" / "run.jsonl").write_text('{"image_digest": "other machine"}\n')
        failed, sent = self.run_it()
        self.assertIsNone(failed)
        sent.assert_not_called()

    def test_a_different_or_missing_table_alerts_with_its_rows(self):
        self.tables(self.tmp / "a", t=[1, 2], u=[3], v=[4])
        self.tables(self.tmp / "b", t=[1, 5, 6], u=[3])
        self.assertEqual(C.compare(self.tmp / "a" / "out" / "R1", self.tmp / "b" / "out" / "R1"), [("t", 2, 3), ("v", 1, None)])
        failed, sent = self.run_it()
        self.assertEqual(failed, str(C.DIFFERENT))   # not 1: a crash fails the task instead of publishing
        self.assertIn("the weekly rebuild is the truth", sent.call_args[0][0])
        report = json.loads((self.tmp / "a" / "out" / "R1" / "compare.json").read_text(encoding="utf-8"))
        t = report["tables"][0]
        self.assertEqual((t["table"], t["only_rebuild"], t["only_update"]), ("t", 1, 2))
        self.assertEqual((t["examples_rebuild"], t["examples_update"]), (["2"], ["5", "6"]))

    def test_says_which_part_went_wrong(self):
        # resolve's input (candidate) is the same, its output isn't: resolve, not the parts after it
        self.assertEqual(C.where({"resolution", "call_edge", "call_site", "blast_radius"}), ["resolve"])
        self.assertEqual(C.where({"candidate", "resolution"}), ["scan"])
        self.assertEqual(C.where({C.REPORT}), ["report"])                    # every table right, the report wrong
        self.assertEqual(C.where({"blast_radius", C.REPORT}), ["graph"])    # the report only followed graph

    def test_a_wrong_change_report_is_caught(self):
        self.tables(self.tmp / "a", t=[1])
        self.tables(self.tmp / "b", t=[1])
        (self.tmp / "a" / "out" / "R1" / "changes.json").write_text('{"totals": {"call_edges_added": 2}}\n')
        (self.tmp / "b" / "out" / "R1" / "changes.json").write_text('{"totals": {"call_edges_added": 3}}\n')
        failed, sent = self.run_it()
        self.assertEqual(failed, str(C.DIFFERENT))
        self.assertIn("went wrong in: report", sent.call_args[0][1][0])

    def test_nothing_to_compare_is_an_error(self):
        failed, sent = self.run_it()
        self.assertIn("nothing to compare", failed)


class Status(Base):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "mirrors" / "o__r.git"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "--quiet")
        self.shas = []
        for i in range(3):
            git(self.repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "--quiet", "--allow-empty", "-m", f"c{i}")
            self.shas.append(git(self.repo, "rev-parse", "HEAD").strip())

    def published(self, sha, published_at=None):
        self.block(self.tmp, shas={"o/r": sha, "o/gone": "b" * 40})
        self.publish("R1")
        if published_at:
            latest = self.tmp / "published" / "latest.json"
            latest.write_text(json.dumps({**json.loads(latest.read_text()), "published_at": published_at}))

    def main(self):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict("os.environ", {"MERIDIAN_DATA": str(self.tmp),
                                                                  "MERIDIAN_SETTINGS": str(self.settings)}):
            code = S.main(["--config", str(self.config)])
        return code, out.getvalue().strip()

    def test_nothing_published_is_new(self):
        self.assertEqual(self.main(), (0, "nothing published yet"))

    def test_new_commits_since_the_published_sha_are_new(self):
        self.published(self.shas[0])
        st = S.status(self.config, self.tmp, self.settings)
        self.assertEqual((st["repos"], st["new_commits"], st["stale"], st["new"]), ({"o/r": 2, "o/gone": None}, 2, False, True))
        code, line = self.main()
        self.assertEqual(code, 0)
        self.assertRegex(line, r"^data \d+ days old · 2 new commits · run R1 · 1 repos unknown$")

    def test_the_tip_published_and_a_fresh_publish_is_nothing_new(self):
        self.published(self.shas[-1])
        self.assertEqual(self.main()[0], S.NOTHING_NEW)

    def test_an_old_publish_is_new_for_the_tickets(self):
        self.published(self.shas[-1], "2000-01-01T00:00:00Z")
        st = S.status(self.config, self.tmp, self.settings)
        self.assertEqual((st["new_commits"], st["stale"], st["new"]), (0, True, True))


class Trigger(Base):
    """The Update graph button: Airflow's REST API, the login from .env, at most one queued run."""
    def click(self, queued):
        self.settings.write_text(SETTINGS + '[dags.update]\ndag_id = "u"\napi = "http://af/api/v1"\n'
                                 'api_user_env = "AF_USER"\napi_password_env = "AF_PASSWORD"\n', encoding="utf-8")
        (self.tmp / ".env").write_text("# login\nAF_USER=admin\nAF_PASSWORD=pw\n", encoding="utf-8")
        answers = iter([{"dag_runs": queued}, {"dag_run_id": "manual__1"}])
        reply = lambda req, timeout: mock.MagicMock(**{"__enter__.return_value": io.BytesIO(json.dumps(next(answers)).encode())})
        with mock.patch("urllib.request.urlopen", side_effect=reply) as urlopen, mock.patch.dict("os.environ", {}, clear=True):
            said = S.trigger(self.settings, self.tmp / ".env")
        return said, [c.args[0] for c in urlopen.call_args_list]

    def test_a_click_queues_a_run(self):
        said, reqs = self.click([])
        self.assertEqual(said, "queued: manual__1")
        self.assertEqual([(r.get_method(), r.full_url) for r in reqs],
                         [("GET", "http://af/api/v1/dags/u/dagRuns?state=queued"), ("POST", "http://af/api/v1/dags/u/dagRuns")])
        self.assertEqual(reqs[1].get_header("Authorization"), "Basic YWRtaW46cHc=")   # admin:pw

    def test_a_second_click_while_one_is_queued_adds_nothing(self):
        said, reqs = self.click([{"dag_run_id": "manual__0"}])
        self.assertEqual((said, len(reqs)), ("already queued: manual__0", 1))


if __name__ == "__main__":
    unittest.main()
