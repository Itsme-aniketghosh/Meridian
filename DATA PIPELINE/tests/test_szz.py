"""D6 tests: SZZ blame on a fixture repo, which commits count as fixes, and file_bug_fixes' window.

    python -m unittest tests.test_szz -v      # from DATA PIPELINE/
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.szz as S

RUN = "abcdef0123456789"


def fixture_repo(path):
    """c0 writes app.py, c1 brings the bug in, fix changes c1's line plus a comment, a blank and a whitespace-only line,
    and its test. add_only only adds lines. Returns {subject: sha}."""
    path.mkdir()
    t = iter(f"2026-01-{d:02d}T00:00:00Z" for d in range(1, 20))

    def run(*a, date=None):
        env = {**os.environ, "GIT_AUTHOR_DATE": date or "2026-01-31T00:00:00Z", "GIT_COMMITTER_DATE": "2026-02-01T00:00:00Z"}
        return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True, env=env).stdout.strip()

    def commit(msg, files):
        for name, text in files.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        run("add", ".")
        run("-c", "user.name=Ann", "-c", "user.email=ann@example.com", "commit", "--quiet", "-m", msg, date=next(t))
        return run("rev-parse", "HEAD")

    run("init", "--quiet", "--initial-branch=main")
    app = ["def f(x):", "    # check x", "    if x > 0:", "        return 1", "", "    return 0"]
    shas = {"c0": commit("c0", {"app.py": "\n".join(app) + "\n", "tests/test_app.py": "assert True\n"})}
    app[3] = "        return 2"  # the bug
    shas["c1"] = commit("c1", {"app.py": "\n".join(app) + "\n"})
    fixed = ["def f(x):", "    # check x is positive", "    if  x > 0:", "        return 1", "    return 0"]
    shas["fix"] = commit("fix", {"app.py": "\n".join(fixed) + "\n", "tests/test_app.py": "assert f(1) == 1\n"})
    shas["add_only"] = commit("add_only", {"app.py": "\n".join(fixed + ["", "def g():", "    return 3"]) + "\n"})
    return shas


class Blame(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.co = self.tmp / "repo"
        self.sha = fixture_repo(self.co)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_blames_the_changed_line_only(self):
        # comment, blank and whitespace-only edits don't count, and the test file is skipped
        got = S.szz(self.co, self.sha["fix"], self.sha["c1"])
        self.assertEqual(got, {"app.py": {self.sha["c1"]: 1}})

    def test_add_only_fix_has_nothing_to_blame(self):
        self.assertEqual(S.szz(self.co, self.sha["add_only"], self.sha["fix"]), {})

    def test_bug_inducing_rows(self):
        c = {"repo": "fix/repo", "sha": self.sha["fix"], "parent_sha": self.sha["c1"]}
        rows = S.bug_inducing({("fix/repo", c["sha"]): (c, ["X-1", "X-2"])}, lambda repo: self.co, RUN, workers=1)
        self.assertEqual(rows, [{"run_id": RUN, "repo": "fix/repo", "fix_sha": self.sha["fix"], "key": "X-1",
                                 "path": "app.py", "inducing_sha": self.sha["c1"]}])


def commit(sha, at="2026-06-01T00:00:00Z", fp=1, merge=0):
    return {"repo": "r", "sha": sha, "parent_sha": "p", "first_parent": fp, "is_merge": merge, "authored_at": at}


def ticket(key, typ="Bug", res="Fixed", bulk=0):
    return {"key": key, "effective_type": typ, "resolution": res, "bulk_closed": bulk}


class Fixes(unittest.TestCase):
    RULE = {"effective_type": "Bug", "resolution": "Fixed"}

    def test_which_commits_are_fixes(self):
        commits = [commit("ok"), commit("old", at="2014-12-31T23:59:59Z"), commit("side", fp=0), commit("merge", merge=1),
                   commit("feature"), commit("wontfix"), commit("bulk"), commit("two")]
        tickets = [ticket("B-1"), ticket("F-1", typ="Improvement"), ticket("W-1", res="Won't Fix"), ticket("B-2", bulk=1), ticket("B-3")]
        ct = [{"repo": "r", "sha": s, "key": k} for s, k in [("ok", "B-1"), ("old", "B-1"), ("side", "B-1"), ("merge", "B-1"),
              ("feature", "F-1"), ("wontfix", "W-1"), ("bulk", "B-2"), ("two", "B-3"), ("two", "B-1")]]
        got = {sha: keys for (_, sha), (_, keys) in S.fixes(commits, tickets, ct, self.RULE).items()}
        self.assertEqual(got, {"ok": ["B-1"], "two": ["B-1", "B-3"]})

    def test_file_bug_fixes_window(self):
        snap = "2026-10-01T00:00:00Z"
        fx = {("r", s): (commit(s, at=at), keys) for s, at, keys in [
            ("in1", "2026-09-30T00:00:00Z", ["B-2"]), ("in2", "2025-10-01T00:00:01Z", ["B-1"]),
            ("edge", "2025-10-01T00:00:00Z", ["B-3"]), ("after", "2026-10-01T00:00:01Z", ["B-4"])]}
        files = [{"repo": "r", "sha": s, "path": p} for s, p in
                 [("in1", "a.py"), ("in2", "a.py"), ("edge", "a.py"), ("after", "a.py"), ("in1", "b.py"), ("nofix", "c.py")]]
        got = sorted(S.file_bug_fixes(fx, files, snap, 365, RUN), key=lambda r: r["path"])
        self.assertEqual([(r["path"], r["bug_fixes"], json.loads(r["fix_keys"])) for r in got],
                         [("a.py", 2, ["B-1", "B-2"]), ("b.py", 1, ["B-2"])])
        self.assertEqual((got[0]["window_start"], got[0]["window_end"]), ("2025-10-01T00:00:00Z", snap))


if __name__ == "__main__":
    unittest.main()
