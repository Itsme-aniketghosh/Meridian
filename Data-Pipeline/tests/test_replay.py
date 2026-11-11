"""The push replay (meridian/replay.py): the 21 pushes, push_alert rows the writer accepts, the merge, the block, and
05-test's alerts-per-push bar. The push check itself is injected here; tests/test_pushcheck.py tests it."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian import replay as R
from meridian import writer as W
from meridian.common import CONFIG, SCHEMA, load_config

CFG = load_config(CONFIG)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x.org", "GIT_COMMITTER_NAME": "a",
                        "GIT_COMMITTER_EMAIL": "a@x.org"})


def mirror(root, repo):
    """A bare mirror holding two commits; returns (parent sha, child sha)."""
    work = Path(root) / "work"
    work.mkdir()
    git(work, "init", "-q")
    (work / "a.py").write_text("x = 1\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "one")
    (work / "a.py").write_text("x = 2\n")
    git(work, "commit", "-q", "-am", "two")
    shas = subprocess.run(["git", "rev-list", "--reverse", "HEAD"], cwd=work, capture_output=True,
                          text=True).stdout.split()
    git(root, "clone", "-q", "--bare", str(work), str(Path(root) / (repo.replace("/", "__") + ".git")))
    return shas[0], shas[1]


def fake_check(answers):
    """A push check that 'finds' answers[repo] and says Skipped for the repos in answers['skip']."""
    def check(db, repo, co, changed, cfg, settings, rng, collect=None):
        if repo in answers.get("skip", ()):
            return "head\n\nSkipped: 400 files in scope, over the limit\n"
        collect.extend(answers.get(repo, []))
        return "head\n\n- **P9 Defect risk**: x\n" if answers.get(repo) else ""
    return check


class Pushes(unittest.TestCase):
    def test_the_21_migration_commits(self):
        c = R.next_commits(CFG)
        self.assertEqual(len(c), 21)
        self.assertEqual(c["django/django"], ("4353640ea9", "c651331b34"))
        for repo, (parent, child) in c.items():
            if repo != "django/django":
                self.assertEqual(parent, child + "^1")


class Rows(unittest.TestCase):
    def test_rows_are_push_alerts_the_writer_accepts(self):
        found = [("P9", "push", "15 lines × 3 files = 45, over 2000"), ("P1", "a.py:3", "new call site of `url`"),
                 ("P1", "a.py:3", "a duplicate of the same (check, target)")]
        rows = R.rows_for("c" * 40, "70dd1dabfed544b8", found)
        self.assertEqual(len(rows), 2)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "push_alert.jsonl"
            R.merge(path, rows, {"c" * 40})
            spec = W.tables(SCHEMA.read_text(encoding="utf-8"))["push_alert"]
            checked = W.read_rows(path, spec, "70dd1dabfed544b8", "2026-10-26T00:00:00Z")
        self.assertEqual(len(checked), 2)
        self.assertEqual(json.loads(rows[0]["evidence"])["what"], found[0][2])
        self.assertTrue(all(r["outcome"] is None and r["decided_at"] is None for r in rows))

    def test_merge_keeps_other_pushes_and_replaces_a_replayed_one(self):
        old = R.rows_for("a" * 40, "R1", [("P1", "x.py:1", "old")]) + R.rows_for("b" * 40, "R1", [("P9", "push", "y")])
        new = R.rows_for("b" * 40, "R1", [("P7", "z.py", "new")])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "push_alert.jsonl"
            R.merge(path, old, set())
            rows = R.merge(path, new, {"b" * 40})
        self.assertEqual([(r["push_sha"][0], r["check_id"]) for r in rows], [("a", "P1"), ("b", "P7")])


class Replay(unittest.TestCase):
    def test_each_push_checked_from_its_mirror(self):
        with tempfile.TemporaryDirectory() as d:
            parent, child = mirror(d, "acme/shop")
            commits = {"acme/shop": (parent, child), "acme/gone": ("p", "c"), "acme/big": (parent, child)}
            Path(d, "acme__big.git").symlink_to(Path(d, "acme__shop.git"))
            check = fake_check({"acme/shop": [("P9", "push", "45, over 10")], "skip": {"acme/big"}})
            out = R.replay(Path(d) / "m.sqlite", "R1", d, CFG, {}, commits=commits, check=check)
        by = {repo: (sha, status, rows) for repo, sha, status, rows in out}
        self.assertEqual(by["acme/shop"][0], child)
        self.assertEqual(by["acme/shop"][1], "checked")
        self.assertEqual([r["check_id"] for r in by["acme/shop"][2]], ["P9"])
        self.assertEqual(by["acme/gone"][1:], ("missing", []))
        self.assertEqual(by["acme/big"][1], "skipped")
        s = R.summary(out)
        self.assertEqual((s["checked"], s["skipped"], s["missing"], s["alerts"]), (1, 1, 1, 1))

    def test_05_test_bar_is_on_checked_pushes(self):
        res = [("r1", "a", "checked", [{"check_id": "P9"}]), ("r2", "b", "checked", []), ("r3", "c", "checked", []),
               ("r4", "d", "skipped", [])]
        s = R.summary(res)
        self.assertEqual(s["median_per_push"], 0)
        self.assertTrue(s["meets_05_test"])
        busy = [("r", str(i), "checked", [{"check_id": "P1"}] * 2) for i in range(3)]
        self.assertFalse(R.summary(busy)["meets_05_test"])


class Apply(unittest.TestCase):
    def test_into_a_block_with_or_without_push_alert(self):
        rows = R.rows_for("c" * 40, "R1", [("P9", "push", "45")])
        for has_table in (True, False):
            with self.subTest(has_table=has_table), tempfile.TemporaryDirectory() as d:
                path = Path(d) / "m.sqlite"
                db = sqlite3.connect(path)
                db.execute("CREATE TABLE run (run_id TEXT)")
                if has_table:
                    ddl = SCHEMA.read_text(encoding="utf-8")
                    start = ddl.index("CREATE TABLE push_alert")
                    db.execute(ddl[start:ddl.index(";", start) + 1])
                    db.execute("INSERT INTO push_alert VALUES (?, 'R1', 'P1', 'old.py:1', '[]', NULL, NULL)",
                               ("c" * 40,))
                db.commit()
                db.close()
                R.apply(path, rows, {"c" * 40})
                R.apply(path, rows, {"c" * 40})                  # twice: replaced, not doubled
                got = sqlite3.connect(path).execute("SELECT check_id, target FROM push_alert").fetchall()
                self.assertEqual(got, [("P9", "push")])


if __name__ == "__main__":
    unittest.main()
