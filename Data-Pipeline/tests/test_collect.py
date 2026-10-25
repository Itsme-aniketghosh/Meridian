"""Collect tests (05-test: fixture repo + mock Jira and GitLab; kill and resume; deltas). No network.

    python -m unittest tests.test_collect -v      # from Data-Pipeline/
"""
import gzip, json, os, shutil, subprocess, sys, tempfile, threading, unittest, urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.collect as S
import meridian.tickets as T

TOTAL = 250


class MockJira(BaseHTTPRequestHandler):
    cap = None  # set to an int to fake Jira silently capping maxResults
    changed = {}  # ticket index -> summary: edited since the base pull, and all an `updated >=` query returns
    jqls = []  # every jql asked for

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        start, n = int(q["startAt"][0]), int(q["maxResults"][0])
        n = min(n, self.cap or n)
        MockJira.jqls.append(q["jql"][0])
        ids = sorted(self.changed) if "updated >=" in q["jql"][0] else range(TOTAL)
        issues = [{"key": f"FIX-{i + 1}", "fields": {"summary": self.changed.get(i, f"ticket {i + 1}"), "created": "2026-01-01T00:00:00.000+0000"},
                   "changelog": {"histories": []}} for i in ids[start:start + n]]
        body = json.dumps({"startAt": start, "maxResults": n, "total": len(ids), "issues": issues}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class MockGitLab(BaseHTTPRequestHandler):
    """GraphQL: one page of issues (only the changed ones for an updatedAfter query), and their notes."""
    issues = {}  # iid -> (title, notes)
    changed = set()
    queries = []

    def do_POST(self):
        q = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        MockGitLab.queries.append(q["query"])
        if "notes(" in q["query"]:
            nodes = [{"iid": i, "notes": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                          "nodes": self.issues[i][1]}} for i in q["variables"]["iids"]]
            data = {"project": {"issues": {"nodes": nodes}}}
        else:
            iids = sorted(self.changed if "updatedAfter" in q["query"] else self.issues, key=int)
            nodes = [{"iid": i, "title": self.issues[i][0], "createdAt": "2025-02-01T00:00:00Z", "hasParent": False} for i in iids]
            data = {"project": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}
        body = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def clock(start):
    """A stand-in for collect's only clock: one second later on every call, from start."""
    t = [datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")]
    def now():
        t[0] += timedelta(seconds=1)
        return t[0].strftime("%Y-%m-%dT%H:%M:%SZ")
    return now


def commit(path, i):
    """Commit c<i> on the checked-out branch, with a fixed author and date (2026-01-0<i+1>), so SHAs match everywhere."""
    run = lambda *a, **env: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True,
                                           env={**os.environ, **env}, text=True).stdout.strip()
    (path / "a.py").write_text(f"x = {i}\n")
    run("add", "a.py")
    t = f"2026-01-0{i + 1}T00:00:00Z"
    run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--quiet", "-m", f"c{i}",
        GIT_AUTHOR_DATE=t, GIT_COMMITTER_DATE=t)
    return run("rev-parse", "HEAD")


def fixture_repo(path):
    """Three commits with fixed authors and dates, so the SHAs are the same on every machine."""
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "--quiet", "--initial-branch=main"], check=True)
    for i in range(3):
        commit(path, i)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD~1"], check=True, capture_output=True, text=True).stdout.strip()


# A pinned run as the team's v1 is made: refs pinned, snapshot_at configured, the mirror and a finished base pull
# already on disk, nothing to fetch. Its run_id was made by collect.py before deltas and branch refs (commit a5f420a).
PINNED = """[run]
handoff_dir = "out/{run_id}"
[datasets.fix]
repos = [{ repo = "fixture/repo", ref = "HEAD~1", url = "https://example.invalid/fixture/repo.git" }]
snapshot_at = "2026-01-02T00:00:00Z"
tracker = "jira"
jira = { base = "https://example.invalid/rest/api/2/search", jql = "project=FIX ORDER BY key ASC", page = 100, expand = "changelog" }
"""
PINNED_RUN = {"config_hash": "4fa921f28b531a6407ecb1033949005fb1b7eaa72a353bfb6a53869bf425e2a7", "model_versions": "{}",
              "notes": "jira/FIX pulled 2026-01-03T00:00:00Z..2026-01-03T00:00:05Z | ticket status, resolution and resolved_at "
                       "are as of snapshot_at; other ticket fields are as of the pull",
              "repo_shas": '{"fixture/repo":"327aca897508485cb01090564786b637d8f0bb8d"}', "run_id": "cfa90df0ad17968d",
              "snapshot_at": "2026-01-02T00:00:00Z",
              "source_hashes": '{"jira/FIX":"9a111fdcf82d8c9d79a5512d8c9a95442bf496919753742b132dfd0c2167ef31"}',
              "status": "complete"}


def pinned_cache(root, upstream):
    cache = root / "snapshots" / "_cache"
    subprocess.run(["git", "clone", "--bare", "--quiet", str(upstream), str(cache / "repos" / "fixture__repo.git")], check=True)
    jira = cache / "jira" / "FIX"
    jira.mkdir(parents=True)
    issues = [{"key": f"FIX-{i}", "fields": {"summary": f"ticket {i}", "created": "2026-01-01T00:00:00.000+0000"},
               "changelog": {"histories": []}} for i in (1, 2, 3)]
    body = json.dumps({"startAt": 0, "maxResults": 100, "total": 3, "issues": issues}).encode()
    (jira / "page-000000.json.gz").write_bytes(gzip.compress(body, mtime=0))
    (jira / ".pull.json").write_text(json.dumps({"started_at": "2026-01-03T00:00:00Z", "finished_at": "2026-01-03T00:00:05Z", "total": 3}))
    return jira


class Collect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockJira)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/rest/api/2/search"
        cls.gl_server = ThreadingHTTPServer(("127.0.0.1", 0), MockGitLab)
        threading.Thread(target=cls.gl_server.serve_forever, daemon=True).start()
        cls.graphql = f"http://127.0.0.1:{cls.gl_server.server_port}/api/graphql"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.gl_server.shutdown()

    def setUp(self):
        MockJira.cap, MockJira.changed, MockJira.jqls = None, {}, []
        self.tmp = Path(tempfile.mkdtemp())
        self.sha = fixture_repo(self.tmp / "upstream")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, extra_repos="", threshold=0.30, comment="", snapshot_at="2026-01-02T00:00:00Z", ref="HEAD~1"):
        p = self.tmp / "meridian.toml"
        p.write_text(f"""{comment}
[run]
handoff_dir = "out/{{run_id}}"
[datasets.fix]
repos = [{{ repo = "fixture/repo", ref = "{ref}", url = "{(self.tmp / 'upstream').as_posix()}" }}{extra_repos}]
{f'snapshot_at = "{snapshot_at}"' if snapshot_at else ""}
tracker = "jira"
jira = {{ base = "{self.base}", jql = "project=FIX ORDER BY key ASC", page = 100, expand = "changelog" }}
[ownership]
min_share = {threshold}
""")
        return p

    def run_once(self, cfg, **kw):
        return S.snapshot(cfg, root=self.tmp / "root", **kw)

    def test_same_inputs_same_run(self):
        a = self.run_once(self.config())
        b = self.run_once(self.config())
        self.assertEqual(a, b)
        self.assertEqual(a["status"], "complete")
        snap = self.tmp / "root" / "snapshots" / a["run_id"]
        m = json.loads((snap / "manifest.json").read_text())
        self.assertEqual(m["repos"], {"fixture/repo": self.sha})
        self.assertEqual(sorted(p.name for p in (snap / "jira" / "FIX").iterdir()),
                         ["page-000000.json.gz", "page-000100.json.gz", "page-000200.json.gz"])
        co = snap / "repos" / "fixture__repo"
        git = lambda *x: subprocess.run(["git", "-C", str(co), *x], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(git("rev-parse", "HEAD"), self.sha)
        self.assertEqual(git("rev-list", "--count", "HEAD"), "2")  # full history up to the pin
        run_rows = (self.tmp / "root" / "out" / a["run_id"] / "run.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(r) for r in run_rows], [a])

    def test_run_id_follows_config_values_not_comments(self):
        a = self.run_once(self.config())
        self.assertEqual(a["run_id"], self.run_once(self.config(comment="# a comment"))["run_id"])
        self.assertNotEqual(a["run_id"], self.run_once(self.config(threshold=0.25))["run_id"])

    def test_kill_and_resume_gives_the_same_pages(self):
        jira = {"base": self.base, "jql": "project=FIX ORDER BY key ASC", "page": 100}
        killed = self.tmp / "killed"
        self.assertIsNone(S.pull_jira(killed, jira, stop_after=1))
        self.assertEqual(len(list(killed.glob("page-*"))), 1)
        S.pull_jira(killed, jira)
        S.pull_jira(self.tmp / "clean", jira)
        self.assertEqual(S.dir_hash(killed), S.dir_hash(self.tmp / "clean"))

    def test_capped_jira_page_fails_loudly(self):
        MockJira.cap = 50
        with self.assertRaisesRegex(RuntimeError, "expected 100"):
            S.pull_jira(self.tmp / "capped", {"base": self.base, "jql": "project=FIX", "page": 100})

    def test_unresolvable_refs_make_a_partial_run(self):
        extra = (f', {{ repo = "fixture/later", ref = "TBD-day4", url = "{(self.tmp / "upstream").as_posix()}" }}'
                 f', {{ repo = "fixture/gone", ref = "deadbeef1234", url = "{(self.tmp / "upstream").as_posix()}" }}')
        row = self.run_once(self.config(extra_repos=extra))
        self.assertEqual(row["status"], "partial")
        self.assertIn("fixture/later: ref not set yet (TBD-day4)", row["notes"])
        self.assertIn("fixture/gone: ref deadbeef1234 not found", row["notes"])
        self.assertEqual(json.loads(row["repo_shas"]), {"fixture/repo": self.sha})

    def test_run_wide_snapshot_at_wins(self):
        cfg = self.config()
        cfg.write_text(cfg.read_text().replace("[run]\n", '[run]\nsnapshot_at = "2026-01-03T00:00:00Z"\n'))
        self.assertEqual(self.run_once(cfg)["snapshot_at"], "2026-01-03T00:00:00Z")

    def test_snapshot_at_in_the_future_is_refused(self):
        with self.assertRaisesRegex(SystemExit, "in the future"):
            self.run_once(self.config(snapshot_at="2999-01-01T00:00:00Z"))

    def test_pinned_run_is_unchanged(self):
        """v1's path: pinned refs, configured snapshot_at, mirror and base pull on disk. The same run, row for row,
        as collect.py made before deltas and branch refs, and the deltas a later run pulled don't change it."""
        root = self.tmp / "pinned"
        jira = pinned_cache(root, self.tmp / "upstream")
        cfg = self.tmp / "pinned.toml"
        cfg.write_text(PINNED)
        for name, meta in (("delta-20260204T000000Z", {"started_at": "2026-02-04T00:00:00Z", "finished_at": "2026-02-04T00:00:01Z"}),
                           ("delta-20260205T000000Z", {"started_at": "2026-02-05T00:00:00Z"})):  # the second one killed
            (jira / name).mkdir()
            (jira / name / "cursor.json").write_text('{"since":"2026-01-03T00:00:00Z"}\n')
            (jira / name / "page-000000.json.gz").write_bytes(gzip.compress(b'{"total": 0, "issues": []}', mtime=0))
            (jira / name / ".pull.json").write_text(json.dumps(meta))
        row = S.snapshot(cfg, root=root)
        self.assertEqual({k: v for k, v in row.items() if k != "image_digest"}, PINNED_RUN)
        snap = root / "snapshots" / PINNED_RUN["run_id"]
        self.assertEqual(sorted(p.relative_to(snap).as_posix() for p in (snap / "jira").rglob("*") if p.is_file()),
                         ["jira/FIX/page-000000.json.gz"])
        m = json.loads((snap / "manifest.json").read_text())
        self.assertEqual(sorted(m), ["config_hash", "repos", "run_id", "snapshot_at", "sources"])
        co = snap / "repos" / "fixture__repo"
        git = lambda *x: subprocess.run(["git", "-C", str(co), *x], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(git("rev-parse", "HEAD"), self.sha)
        self.assertEqual(git("rev-list", "--count", "HEAD"), "2")

    def test_follows_the_branch_and_pulls_only_what_changed(self):
        """#14-#16: a branch ref moves with the branch, snapshot_at is the pull time, and Jira is asked only for
        what changed since the last pull. The base pull is never touched; the newest copy of a ticket wins."""
        cfg = self.config(ref="main", snapshot_at=None)
        root, upstream = self.tmp / "root", self.tmp / "upstream"
        with mock.patch.object(S, "now", clock("2026-02-01T00:00:00Z")):
            a = self.run_once(cfg)
        self.assertEqual(a["snapshot_at"], "2026-02-01T00:00:01Z")
        old = json.loads(a["repo_shas"])["fixture/repo"]
        self.assertEqual(MockJira.jqls[-1], "project=FIX ORDER BY key ASC")

        new = commit(upstream, 3)
        MockJira.changed = {4: "ticket 5, edited"}
        n = len(MockJira.jqls)
        with mock.patch.object(S, "now", clock("2026-02-02T00:00:00Z")):
            b = self.run_once(cfg)
        self.assertEqual(b["snapshot_at"], "2026-02-02T00:00:01Z")
        self.assertEqual(json.loads(b["repo_shas"]), {"fixture/repo": new})
        # cursor: the base started at 2026-02-01T00:00:03Z, less a day of overlap (settings.toml)
        self.assertEqual(MockJira.jqls[n:], ['(project=FIX) AND updated >= "2026-01-31 00:00" ORDER BY key ASC'])
        self.assertIn("jira/FIX pulled 2026-02-01T00:00:03Z..", b["notes"])
        self.assertIn("base pull + 1 delta(s)", b["notes"])

        snap_a, snap_b = root / "snapshots" / a["run_id"], root / "snapshots" / b["run_id"]
        delta = snap_b / "jira" / "FIX" / "delta-20260202T000003Z"
        self.assertEqual(sorted(p.name for p in delta.iterdir()), ["cursor.json", "page-000000.json.gz"])
        self.assertEqual(json.loads((delta / "cursor.json").read_text()), {"since": "2026-01-31T00:00:03Z"})
        for p in (snap_a / "jira" / "FIX").glob("page-*"):  # the base pull, unchanged
            self.assertEqual(p.read_bytes(), (snap_b / "jira" / "FIX" / p.name).read_bytes())
        ta, tb = T.read_jira(snap_a / "jira" / "FIX"), T.read_jira(snap_b / "jira" / "FIX")
        self.assertEqual([t["key"] for t in ta], [t["key"] for t in tb])
        self.assertEqual((ta[4]["fields"]["summary"], tb[4]["fields"]["summary"]), ("ticket 5", "ticket 5, edited"))

        git = lambda co, *x: subprocess.run(["git", "-C", str(co), *x], capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(git(snap_b / "repos" / "fixture__repo", "rev-parse", "HEAD"), new)
        self.assertEqual(git(snap_b / "repos" / "fixture__repo", "rev-list", "--count", "HEAD"), "4")
        self.assertEqual(git(snap_a / "repos" / "fixture__repo", "rev-parse", "HEAD"), old)  # the older run is intact
        mirror = root / "snapshots" / "_cache" / "repos" / "fixture__repo.git"
        self.assertEqual(git(mirror, "for-each-ref", "--format=%(refname) %(objectname)", "refs/runs/").splitlines(),
                         sorted([f"refs/runs/{a['run_id']} {old}", f"refs/runs/{b['run_id']} {new}"]))

        MockJira.changed = {4: "ticket 5, edited again"}
        with mock.patch.object(S, "now", clock("2026-02-03T00:00:00Z")):
            c = self.run_once(cfg)
        tc = T.read_jira(root / "snapshots" / c["run_id"] / "jira" / "FIX")
        self.assertEqual([t["key"] for t in tc], [t["key"] for t in ta])
        self.assertEqual(tc[4]["fields"]["summary"], "ticket 5, edited again")

        # b's pull time again: the same pulls (the third delta is later, left out), so the same run, and no query
        n = len(MockJira.jqls)
        self.assertEqual(self.run_once(cfg, snapshot_at=b["snapshot_at"]), b)
        self.assertEqual(len(MockJira.jqls), n)

    def test_gitlab_delta(self):
        """A GitLab delta asks for issues updated after the cursor, each with all its notes again; the newest wins."""
        MockGitLab.issues = {"1": ("one", []), "2": ("two", []), "3": ("three", [])}
        MockGitLab.changed, MockGitLab.queries = set(), []
        ds = {"graphql": self.graphql, "project": "g/p", "issues_created": ["2025-01-01T00:00:00Z", "2025-07-01T00:00:00Z"]}
        pull = lambda d, since: S.pull_gitlab(d, ds, since)
        d = self.tmp / "gitlab"
        with mock.patch.object(S, "now", clock("2026-02-01T00:00:00Z")):
            used, _ = S.pull_source(d, pull, "2026-01-31T00:00:00Z")
        self.assertEqual(used, [d])
        base = (d / "issues.jsonl.gz").read_bytes()

        note = {"body": "marked this issue as blocking #1", "createdAt": "2026-02-01T12:00:00Z", "system": True, "author": None}
        MockGitLab.issues["2"] = ("two, edited", [note])
        MockGitLab.changed = {"2"}
        n = len(MockGitLab.queries)
        with mock.patch.object(S, "now", clock("2026-02-02T00:00:00Z")):
            used, meta = S.pull_source(d, pull, "2026-02-02T00:00:00Z")
        self.assertEqual([p.name for p in used], ["gitlab", "delta-20260202T000001Z"])
        self.assertIn('updatedAfter:"2026-01-31T00:00:01Z"', MockGitLab.queries[n])
        self.assertEqual((d / "issues.jsonl.gz").read_bytes(), base)
        issues = T.read_gitlab(d)
        self.assertEqual([(n["iid"], n["title"], n["notes"]) for n in issues],
                         [("1", "one", []), ("2", "two, edited", [note]), ("3", "three", [])])
        self.assertNotEqual(S.dir_hash(d, used), S.dir_hash(d, used[:1]))

    def test_raw_pages_are_byte_stable(self):
        S.pull_jira(self.tmp / "p", {"base": self.base, "jql": "project=FIX", "page": 100})
        first = self.tmp / "p" / "page-000000.json.gz"
        self.assertEqual(gzip.decompress(first.read_bytes())[:1], b"{")
        self.assertEqual(first.read_bytes()[4:8], b"\0\0\0\0")  # gzip mtime field is 0

    def test_one_command_for_airflow(self):
        """CONTRACTS.md, "Every part runs as one command": data under $MERIDIAN_DATA, exit code, run_id last."""
        cfg = self.config()
        cmd = lambda run_id: subprocess.run([sys.executable, "-m", "meridian.collect", "--run-id", run_id, "--config", str(cfg)],
                                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                                            env={**os.environ, "MERIDIAN_DATA": str(self.tmp / "data")})
        new = cmd("new")
        self.assertEqual(new.returncode, 0, new.stderr)
        run_id = new.stdout.splitlines()[-1].removeprefix("run_id=")
        self.assertTrue((self.tmp / "data" / "snapshots" / run_id / "manifest.json").exists())
        self.assertEqual(cmd(run_id).returncode, 0)  # an Airflow retry: same run
        wrong = cmd("0000000000000000")
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("inputs now give", wrong.stderr)


if __name__ == "__main__":
    unittest.main()
