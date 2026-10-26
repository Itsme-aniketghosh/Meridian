"""D6 tests: SZZ blame on a fixture repo, which commits count as fixes, and file_bug_fixes' window.

    python -m unittest tests.test_szz -v      # from Data-Pipeline/
"""
import contextlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.common as common
import meridian.szz as S
import meridian.szz_check as SC

RUN = "abcdef0123456789"
CFG = common.load_config(common.CONFIG)
CODE = S.code_rules(CFG)


def fixture_repo(path):
    """c0 writes app.py, c1 brings the bug in, fix changes c1's line plus a comment, a blank and a whitespace-only line,
    and its test. add_only only adds lines. headers deletes a SQL comment ('--- x' in the diff) and edits a path with
    a space. Returns {subject: sha}."""
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
    shas = {"c0": commit("c0", {"app.py": "\n".join(app) + "\n", "tests/test_app.py": "assert True\n",
                                "q.sql": "-- x\nselect 1;\nselect 2;\nselect 3;\nselect 4;\n", "my file.py": "a = 1\nb = 2\n"})}
    app[3] = "        return 2"  # the bug
    shas["c1"] = commit("c1", {"app.py": "\n".join(app) + "\n"})
    fixed = ["def f(x):", "    # check x is positive", "    if  x > 0:", "        return 1", "    return 0"]
    shas["fix"] = commit("fix", {"app.py": "\n".join(fixed) + "\n", "tests/test_app.py": "assert f(1) == 1\n"})
    shas["add_only"] = commit("add_only", {"app.py": "\n".join(fixed + ["", "def g():", "    return 3"]) + "\n"})
    shas["headers"] = commit("headers", {"q.sql": "select 1;\nselect 2;\nselect 3;\nselect 5;\n", "my file.py": "a = 1\nb = 3\n"})
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
        got = S.szz(self.co, self.sha["fix"], self.sha["c1"], CODE)
        self.assertEqual(got, {"app.py": {self.sha["c1"]: 1}})

    def test_add_only_fix_has_nothing_to_blame(self):
        self.assertEqual(S.szz(self.co, self.sha["add_only"], self.sha["fix"], CODE), {})

    def test_bug_inducing_rows(self):
        c = {"repo": "fix/repo", "sha": self.sha["fix"], "parent_sha": self.sha["c1"]}
        rows = S.bug_inducing({("fix/repo", c["sha"]): (c, ["X-1", "X-2"])}, lambda repo: self.co, RUN, CODE, workers=1)
        self.assertEqual(rows, [{"run_id": RUN, "repo": "fix/repo", "fix_sha": self.sha["fix"], "key": "X-1",
                                 "path": "app.py", "inducing_sha": self.sha["c1"]}])

    def test_file_headers_only_in_the_header(self):
        # the deleted '-- x' must not end q.sql's hunks, and 'my file.py' must not end in a tab
        got = S.szz(self.co, self.sha["headers"], self.sha["add_only"], CODE)
        self.assertEqual(got, {"my file.py": {self.sha["c0"]: 1}, "q.sql": {self.sha["c0"]: 1}})

    def test_root_commit_skipped_and_counted(self):
        c = {"repo": "fix/repo", "sha": self.sha["c0"], "parent_sha": None}
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rows = S.bug_inducing({("fix/repo", c["sha"]): (c, ["X-1"])}, lambda repo: self.co, RUN, CODE, workers=1)
        self.assertEqual(rows, [])
        self.assertIn("1 root commits skipped", out.getvalue())


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
        got = {sha: keys for (_, sha), (_, keys) in S.fixes(commits, tickets, ct, self.RULE, CFG["szz"]["fix_since"]).items()}
        self.assertEqual(got, {"ok": ["B-1"], "two": ["B-1", "B-3"]})

    def test_code_rules_from_config(self):
        self.assertEqual([S.code_file(p, CODE) for p in ("a.py", "q.sql", "README.md", "tests/test_a.py")], [True, True, False, False])
        sql_only = S.code_rules({"szz": {**CFG["szz"], "comment_prefixes": [{"ext": ["sql"], "prefixes": ["--"]}]}})
        self.assertFalse(S.code_file("a.py", sql_only))
        self.assertIs(SC.git, common.git)

    def test_file_bug_fixes_window(self):
        snap = "2026-10-01T00:00:00Z"
        fx = {("r", s): (commit(s, at=at), keys) for s, at, keys in [
            ("in1", "2026-09-30T00:00:00Z", ["B-2"]), ("in2", "2025-10-01T00:00:01Z", ["B-1"]),
            ("edge", "2025-10-01T00:00:00Z", ["B-3"]), ("after", "2026-10-01T00:00:01Z", ["B-4"])]}
        files = [{"repo": "r", "sha": s, "path": p} for s, p in
                 [("in1", "a.py"), ("in2", "a.py"), ("edge", "a.py"), ("after", "a.py"), ("in1", "b.py"), ("nofix", "c.py")]]
        got = sorted(S.file_bug_fixes(fx, files, snap, 365, RUN, CFG["run"]["time_format"]), key=lambda r: r["path"])
        self.assertEqual([(r["path"], r["bug_fixes"], r["fix_keys"]) for r in got],
                         [("a.py", 2, '["B-1","B-2"]'), ("b.py", 1, '["B-2"]')])  # compact, as tickets.jlist
        self.assertEqual((got[0]["window_start"], got[0]["window_end"]), ("2025-10-01T00:00:00Z", snap))



class Fragility(unittest.TestCase):
    def test_no_fixes_next_year_says_na(self):
        db = sqlite3.connect(":memory:")
        db.executescript(common.SCHEMA.read_text(encoding="utf-8"))
        db.execute("INSERT INTO ticket (run_id, key, tracker, type, effective_type, status, resolution, created_at, bulk_closed, "
                   "desc_empty, summary) VALUES (?, 'B-1', 'jira', 'Bug', 'Bug', 'Resolved', 'Fixed', '2025-01-01T00:00:00Z', 0, 0, '')", (RUN,))
        db.execute("INSERT INTO git_commit VALUES (?, 'r', 'c1', NULL, 0, 1, 0, '2025-06-01T00:00:00Z', 'a@x', 'a@x', 'B-1')", (RUN,))
        db.execute("INSERT INTO commit_file VALUES (?, 'r', 'c1', 'src/a.py', NULL, 1, 1)", (RUN,))
        db.execute("INSERT INTO commit_ticket VALUES (?, 'r', 'c1', 'B-1', 'subject')", (RUN,))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            SC.fragility(db, "2026-10-01T00:00:00Z", CFG)  # no fix in the year after the cutoff: nothing to divide by
        self.assertIn("lift n/a", out.getvalue())


if __name__ == "__main__":
    unittest.main()
