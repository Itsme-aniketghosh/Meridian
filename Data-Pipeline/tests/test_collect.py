"""Collect tests (05-test: fixture repo + mock Jira; kill and resume). No network.

    python -m unittest tests.test_collect -v      # from Data-Pipeline/
"""
import gzip, json, os, shutil, subprocess, sys, tempfile, threading, unittest, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.collect as S

TOTAL = 250


class MockJira(BaseHTTPRequestHandler):
    cap = None  # set to an int to fake Jira silently capping maxResults

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        start, n = int(q["startAt"][0]), int(q["maxResults"][0])
        n = min(n, self.cap or n)
        issues = [{"key": f"FIX-{i + 1}", "fields": {"summary": f"ticket {i + 1}", "created": "2026-01-01T00:00:00.000+0000"},
                   "changelog": {"histories": []}} for i in range(start, min(start + n, TOTAL))]
        body = json.dumps({"startAt": start, "maxResults": n, "total": TOTAL, "issues": issues}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def fixture_repo(path):
    """Three commits with fixed authors and dates, so the SHAs are the same on every machine."""
    path.mkdir()
    run = lambda *a, **env: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True,
                                           env={**os.environ, **env}, text=True).stdout.strip()
    run("init", "--quiet", "--initial-branch=main")
    for i in range(3):
        (path / "a.py").write_text(f"x = {i}\n")
        run("add", "a.py")
        t = f"2026-01-0{i + 1}T00:00:00Z"
        run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--quiet", "-m", f"c{i}",
            GIT_AUTHOR_DATE=t, GIT_COMMITTER_DATE=t)
    return run("rev-parse", "HEAD~1")


class Collect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockJira)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/rest/api/2/search"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        MockJira.cap = None
        self.tmp = Path(tempfile.mkdtemp())
        self.sha = fixture_repo(self.tmp / "upstream")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, extra_repos="", threshold=0.30, comment="", snapshot_at="2026-01-02T00:00:00Z"):
        p = self.tmp / "meridian.toml"
        p.write_text(f"""{comment}
[run]
handoff_dir = "out/{{run_id}}"
[datasets.fix]
repos = [{{ repo = "fixture/repo", ref = "HEAD~1", url = "{(self.tmp / 'upstream').as_posix()}" }}{extra_repos}]
snapshot_at = "{snapshot_at}"
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

    def test_pull_before_snapshot_at_is_refused(self):
        with self.assertRaisesRegex(SystemExit, "before snapshot_at"):
            self.run_once(self.config(snapshot_at="2999-01-01T00:00:00Z"))

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
