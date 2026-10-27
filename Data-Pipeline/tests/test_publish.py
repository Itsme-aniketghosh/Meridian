"""Publish, compare and status tests: what the two DAGs do after validate, and the "anything new?" step. Small temp
data roots, a tiny git repo as the mirror; alerts are mocked.

    python -m unittest tests.test_publish -v      # from Data-Pipeline/
"""
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.collect as Col
import meridian.compare as C
import meridian.publish as Pub
import meridian.status as S
from meridian import alert
from meridian.common import git, load_config

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
stale_after_hours = 24
[collect]
mirrors = "mirrors"
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

    def block(self, root, run_id="R1", anomalies=(), shas=None, db=b"block", status="complete",
              snapshot_at="2026-10-01T00:00:00Z"):
        out = root / "out" / run_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "meridian.sqlite").write_bytes(db)
        (out / "anomalies.json").write_text(json.dumps(list(anomalies)), encoding="utf-8")
        (out / "run.jsonl").write_text(json.dumps({"run_id": run_id, "snapshot_at": snapshot_at,
                                                   "repo_shas": json.dumps(shas or {"o/r": "a" * 40}), "status": status,
                                                   "notes": "", "config_hash": self.config_hash()}) + "\n")
        return out

    def config_hash(self):
        return Col.sha256(Col.canon(load_config(self.config)).encode())

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
        self.assertEqual((doc["run_id"], doc["prev_run"], doc["file"], doc["repo_shas"]),
                         ("R1", "R0", "R1.sqlite", {"o/r": "a" * 40}))
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
        (self.block(self.tmp) / "only_main.jsonl").write_text("{}\n")
        self.publish("R1")
        (self.block(self.tmp / "rebuild", db=b"rebuilt") / "gx_project").mkdir()
        self.publish("R1", from_root=self.tmp / "rebuild")
        pub = self.tmp / "published"
        self.assertEqual(((pub / "R1.sqlite").read_bytes(), (pub / "R1.rebuild.sqlite").read_bytes()),
                         (b"block", b"rebuilt"))
        self.assertEqual(Pub.latest(self.tmp, self.settings)["file"], "R1.rebuild.sqlite")
        # the update's out/ was wrong: the rebuild's replaces it, for the next update's join. Nothing stale survives
        main = self.tmp / "out" / "R1"
        self.assertEqual((main / "meridian.sqlite").read_bytes(), b"rebuilt")
        self.assertEqual(sorted(p.name for p in main.iterdir()), ["anomalies.json", "meridian.sqlite", "run.jsonl"])
        self.assertEqual(sorted(p.name for p in main.parent.iterdir()), ["R1"])   # no tmp left

    def test_a_rebuild_never_rolls_back_a_newer_run(self):
        self.block(self.tmp)
        self.block(self.tmp / "rebuild", db=b"rebuilt")
        self.publish("R1")
        self.block(self.tmp, "R2")
        self.publish("R2", prev_run="R1")
        with self.assertRaisesRegex(SystemExit, "R2 is live now"):
            self.publish("R1", from_root=self.tmp / "rebuild")
        self.assertEqual(Pub.latest(self.tmp, self.settings)["run_id"], "R2")
        # refused before the swap: the update's out/R1 is untouched, and no tmp is left
        self.assertEqual((self.tmp / "out" / "R1" / "meridian.sqlite").read_bytes(), b"block")
        self.assertEqual(sorted(p.name for p in (self.tmp / "out").iterdir()), ["R1", "R2"])

    def test_an_update_never_rolls_back_a_newer_run(self):   # an older run_id triggered, or a slow update
        self.block(self.tmp, "R2", snapshot_at="2026-10-02T00:00:00Z")
        self.publish("R2", prev_run="R1")
        self.block(self.tmp, "R1")
        with self.assertRaisesRegex(SystemExit, "R2 is live now"):
            self.publish("R1", prev_run="R0")
        self.assertEqual(Pub.latest(self.tmp, self.settings)["run_id"], "R2")
        self.assertEqual(self.publish("R2", prev_run="R1")["run_id"], "R2")   # the same run again is no rollback

    def test_an_update_keeps_only_the_live_and_prev_runs_data(self):   # and the frozen team run
        old, prev, new, was, was_prev, frozen = "0" * 16, "1" * 16, "2" * 16, "3" * 16, "4" * 16, "f" * 16
        self.settings.write_text(SETTINGS + f'[pipeline]\nrun_id = "{frozen}"\n', encoding="utf-8")
        mirror = self.tmp / "mirrors" / "o__r.git"
        mirror.mkdir(parents=True)
        git(mirror, "init", "--quiet")
        git(mirror, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "--quiet", "--allow-empty", "-m", "c")
        sha = git(mirror, "rev-parse", "HEAD").strip()

        def runs(*rs):
            for r in rs:
                self.block(self.tmp, r)
                (self.tmp / "snapshots" / r).mkdir(parents=True)
                git(mirror, "update-ref", f"refs/runs/{r}", sha)
        runs(was, was_prev, frozen)
        self.publish(was, prev_run=was_prev)   # live until new publishes: a rebuild of it may be linking its prev now
        pub = self.tmp / "published"
        for f in (f"{old}.sqlite", f"{old}.rebuild.sqlite", f"{frozen}.sqlite", f"gx/{old}/index.html"):
            (pub / f).parent.mkdir(parents=True, exist_ok=True)
            (pub / f).write_bytes(b"block")
        runs(old, prev, new)
        for other in ("snapshots/.staging-" + old, "out/" + old + ".tmp"):
            (self.tmp / other).mkdir()
        self.publish(new, prev_run=prev)
        self.assertEqual(sorted(p.name for p in (self.tmp / "snapshots").iterdir()),
                         [".staging-" + old, prev, new, was, was_prev, frozen])
        self.assertEqual(sorted(p.name for p in (self.tmp / "out").iterdir()),
                         [old + ".tmp", prev, new, was, was_prev, frozen])
        self.assertEqual(git(mirror, "for-each-ref", "--format=%(refname)", "refs/runs/").split(),
                         [f"refs/runs/{r}" for r in (prev, new, was, was_prev, frozen)])
        # published blocks too, never latest.json or the GX docs
        self.assertEqual(sorted(p.name for p in pub.iterdir()),
                         [f"{new}.sqlite", f"{was}.sqlite", f"{frozen}.sqlite", "gx", "latest.json"])
        self.assertTrue((pub / "gx" / old / "index.html").exists())

    def test_a_partial_run_needs_allow_partial(self):   # a tracker down: code only
        self.block(self.tmp, status="partial")
        with self.assertRaisesRegex(SystemExit, "R1 is partial"):
            self.publish("R1")
        self.assertEqual(self.publish("R1", allow_partial=True)["run_id"], "R1")

    def test_only_the_block_ships_and_latest_has_the_config_hash(self):   # bias.json stays in out/: nothing reads it
        (self.block(self.tmp / "rebuild") / "bias.json").write_text('{"flags": []}')
        doc = self.publish("R1", from_root=self.tmp / "rebuild")
        self.assertEqual(sorted(p.name for p in (self.tmp / "published").iterdir()),
                         ["R1.rebuild.sqlite", "latest.json"])
        self.assertEqual(doc["config_hash"], self.config_hash())

    def test_dvc_only_for_the_run_dvc_yaml_tracks(self):
        self.block(self.tmp)
        self.settings.write_text(SETTINGS.replace("push = false", "push = true") + '[pipeline]\nrun_id = "R0"\n',
                                 encoding="utf-8")
        with mock.patch("subprocess.run") as run:
            self.publish("R1")
            run.assert_not_called()
            self.settings.write_text(self.settings.read_text().replace("R0", "R1"), encoding="utf-8")
            self.publish("R1")
        self.assertEqual([c.args[0][3] for c in run.call_args_list], ["commit", "push"])
        # a failed push after latest.json is written: alerted, the publish stays live and the task succeeds
        with mock.patch("subprocess.run", side_effect=Pub.subprocess.CalledProcessError(1, "dvc push")), \
                mock.patch.object(alert, "send") as sent:
            self.assertEqual(self.publish("R1")["run_id"], "R1")
        self.assertEqual((sent.call_args.args[0], sent.call_args.args[2]), ("meridian R1: published, but dvc failed",
                                                                            "warn"))


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
        self.assertEqual(C.compare(self.tmp / "a" / "out" / "R1", self.tmp / "b" / "out" / "R1"),
                         [("t", 2, 3), ("v", 1, None)])
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
        self.assertEqual(C.PARTS["report"][0], ("validate",))               # as in the DAGs

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
            git(self.repo, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "--quiet",
                "--allow-empty", "-m", f"c{i}")
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
        self.assertEqual((st["repos"], st["new_commits"], st["stale"], st["new"]),
                         ({"o/r": 2, "o/gone": None}, 2, False, True))
        code, line = self.main()
        self.assertEqual(code, 0)
        self.assertRegex(line, r"^data \d+ days old · 2 new commits · run R1 · 1 repos unknown$")

    def test_the_tip_published_and_a_fresh_publish_is_nothing_new(self):
        self.published(self.shas[-1])
        self.assertEqual(self.main()[0], S.NOTHING_NEW)

    def test_a_push_since_the_last_collect_is_seen(self):   # the mirror is fetched first for a followed branch
        upstream = self.tmp / "upstream"
        upstream.mkdir()
        git(upstream, "init", "--quiet", "--initial-branch=main")
        commit = lambda m: git(upstream, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "--quiet",
                               "--allow-empty", "-m", m)
        commit("c0")
        mirror = self.tmp / "mirrors" / "o__f.git"
        git(self.tmp, "clone", "--bare", "--quiet", str(upstream), str(mirror))
        self.config.write_text(CONFIG.replace('{ repo = "o/gone", ref = "HEAD" }', '{ repo = "o/f", ref = "main" }'),
                               encoding="utf-8")
        self.block(self.tmp, shas={"o/r": self.shas[-1], "o/f": git(mirror, "rev-parse", "main").strip()})
        self.publish("R1")
        self.assertEqual(self.main()[0], S.NOTHING_NEW)
        commit("c1")   # pushed upstream after the last collect: only a fetch can see it
        st = S.status(self.config, self.tmp, self.settings)
        self.assertEqual((st["repos"], st["new"]), ({"o/r": 0, "o/f": 1}, True))
        self.settings.write_text(SETTINGS.replace("stale_after_hours", "fetch = false\nstale_after_hours"),
                                 encoding="utf-8")
        commit("c2")
        self.assertEqual(S.status(self.config, self.tmp, self.settings)["repos"]["o/f"], 1)   # not fetched: c2 unseen

    def test_a_new_repo_or_a_config_edit_is_new(self):   # no mirror yet for the new repo: still new
        self.published(self.shas[-1])
        self.config.write_text(CONFIG.replace(" }]", ' }, { repo = "o/added", ref = "main" }]'), encoding="utf-8")
        st = S.status(self.config, self.tmp, self.settings)
        self.assertEqual((st["new_repos"], st["config_changed"], st["new"]), (["o/added"], True, True))
        self.config.write_text(CONFIG + "[ownership]\nmin_share = 0.3\n", encoding="utf-8")
        st = S.status(self.config, self.tmp, self.settings)
        self.assertEqual((st["new_repos"], st["config_changed"], st["new"]), ([], True, True))
        self.assertRegex(self.main()[1], " · config changed$")

    def test_an_old_publish_is_new_for_the_tickets_once_a_repo_follows_a_branch(self):
        self.published(self.shas[-1], "2000-01-01T00:00:00Z")
        st = S.status(self.config, self.tmp, self.settings)   # every repo pinned: collect would make the same run
        self.assertEqual((st["new_commits"], st["stale"], st["new"]), (0, False, False))
        branch = git(self.repo, "branch", "--show-current").strip()
        git(self.tmp, "clone", "--bare", "--quiet", str(self.repo), str(self.tmp / "mirrors" / "o__gone.git"))
        self.config.write_text(CONFIG.replace('"o/gone", ref = "HEAD"', f'"o/gone", ref = "{branch}"'),
                               encoding="utf-8")
        self.published(self.shas[-1], "2000-01-01T00:00:00Z")
        st = S.status(self.config, self.tmp, self.settings)
        self.assertEqual((st["config_changed"], st["stale"], st["new"]), (False, True, True))


class Trigger(Base):
    """The Update graph button: Airflow's REST API, the login from .env, at most one queued run."""
    def click(self, queued, env="# login\nAF_USER=admin\nAF_PASSWORD=pw\n"):
        self.settings.write_text(SETTINGS + '[dags.update]\ndag_id = "u"\napi = "http://af/api/v1"\n'
                                 'api_user_env = "AF_USER"\napi_password_env = "AF_PASSWORD"\n', encoding="utf-8")
        (self.tmp / ".env").write_text(env, encoding="utf-8")
        answers = iter([{"dag_runs": queued}, {"dag_run_id": "manual__1"}])
        reply = lambda req, timeout: mock.MagicMock(
            **{"__enter__.return_value": io.BytesIO(json.dumps(next(answers)).encode())})
        with mock.patch("urllib.request.urlopen", side_effect=reply) as urlopen, mock.patch.dict("os.environ",
                                                                                                 {}, clear=True):
            said = S.trigger(self.settings, self.tmp / ".env")
        return said, [c.args[0] for c in urlopen.call_args_list]

    def test_a_click_queues_a_run(self):
        said, reqs = self.click([])
        self.assertEqual(said, "queued: manual__1")
        self.assertEqual([(r.get_method(), r.full_url) for r in reqs],
                         [("GET", "http://af/api/v1/dags/u/dagRuns?state=queued"),
                          ("POST", "http://af/api/v1/dags/u/dagRuns")])
        self.assertEqual(reqs[1].get_header("Authorization"), "Basic YWRtaW46cHc=")   # admin:pw

    def test_a_second_click_while_one_is_queued_adds_nothing(self):
        said, reqs = self.click([{"dag_run_id": "manual__0"}])
        self.assertEqual((said, len(reqs)), ("already queued: manual__0", 1))

    def test_quotes_and_spaces_in_env_are_not_the_login_and_its_port_is_used(self):   # was 401: '"pw"' sent
        for env in ('AF_USER = admin\nAF_PASSWORD="pw"\n', "AF_USER='admin'\nAF_PASSWORD = pw \n"):
            reqs = self.click([], env)[1]
            self.assertEqual(reqs[1].get_header("Authorization"), "Basic YWRtaW46cHc=")   # admin:pw
        self.assertEqual(self.click([], 'AIRFLOW_PORT = "8091"\n')[1][0].full_url,
                         "http://af:8091/api/v1/dags/u/dagRuns?state=queued")   # docker-compose.yaml's port


if __name__ == "__main__":
    unittest.main()
