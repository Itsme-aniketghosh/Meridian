"""D3 tests: the writer (same rows -> same bytes, bad rows refused) and git_commit / commit_file on a fixture repo.

    python -m unittest tests.test_history -v      # from DATA PIPELINE/
"""
import filecmp, json, os, shutil, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.history as C
import meridian.writer as W

RUN = "abcdef0123456789"


def fixture_repo(path):
    """main: c0 -> c1 (rename + binary) -> merge of side branch (s0, s1) -> c3. Fixed authors and dates."""
    path.mkdir()
    t = iter(f"2026-01-{d:02d}T00:00:00Z" for d in range(1, 20))

    def run(*a, date="2026-01-31T00:00:00Z"):
        return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True,
                              env={**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": "2026-02-01T00:00:00Z"}).stdout.strip()

    def commit(msg, who="Ann <Ann@Example.com>"):
        run("-c", "user.name=Committer", "-c", "user.email=merger@example.com", "commit", "--quiet", "--author", who, "-m", msg, date=next(t))

    run("init", "--quiet", "--initial-branch=main")
    (path / "old.py").write_text("".join(f"line {i}\n" for i in range(50)))
    run("add", ".")
    commit("c0 add old.py")
    run("mv", "old.py", "new.py")
    (path / "logo.png").write_bytes(bytes(range(256)))
    run("add", ".")
    commit("c1 rename and binary")
    run("checkout", "--quiet", "-b", "side")
    for i in range(2):
        (path / f"side{i}.py").write_text(f"s = {i}\n")
        run("add", ".")
        commit(f"s{i}", who="Bo <bo@example.com>")
    run("checkout", "--quiet", "main")
    run("-c", "user.name=Committer", "-c", "user.email=merger@example.com", "merge", "--quiet", "--no-ff", "-m", "merge side", "side")
    (path / "new.py").write_text("changed\n")
    run("add", ".")
    commit("c3")
    return run("rev-parse", "HEAD")


class Commits(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sha = fixture_repo(self.tmp / "repo")
        self.commits, self.files = C.read_repo(self.tmp / "repo", "fix/repo", self.sha, RUN)
        self.by_subject = {c["subject"]: c for c in self.commits}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seq_first_parent_and_merges(self):
        got = {s: (c["seq"], c["first_parent"], c["is_merge"]) for s, c in self.by_subject.items()}
        self.assertEqual(got, {"c0 add old.py": (0, 1, 0), "c1 rename and binary": (1, 1, 0), "s0": (2, 0, 0),
                               "s1": (2, 0, 0), "merge side": (2, 1, 1), "c3": (3, 1, 0)})
        self.assertIsNone(self.by_subject["c0 add old.py"]["parent_sha"])

    def test_author_not_committer_and_utc(self):
        c = self.by_subject["c0 add old.py"]
        self.assertEqual((c["author_email"], c["committer_email"]), ("ann@example.com", "merger@example.com"))
        self.assertEqual(c["authored_at"], "2026-01-01T00:00:00Z")

    def test_rename_binary_and_merge_files(self):
        files = {(f["sha"], f["path"]): f for f in self.files}
        c1, merge = self.by_subject["c1 rename and binary"]["sha"], self.by_subject["merge side"]["sha"]
        self.assertEqual(files[(c1, "new.py")]["old_path"], "old.py")
        self.assertEqual((files[(c1, "logo.png")]["added"], files[(c1, "logo.png")]["deleted"]), (None, None))
        self.assertEqual(sorted(p for s, p in files if s == merge), ["side0.py", "side1.py"])  # vs first parent

    def test_end_to_end_loads(self):
        out = self.tmp / "out" / RUN
        out.mkdir(parents=True)
        C.write_jsonl(out / "git_commit.jsonl", self.commits, ("repo", "sha"))
        C.write_jsonl(out / "commit_file.jsonl", self.files, ("repo", "sha", "path"))
        counts = W.write(out)
        self.assertEqual((counts["git_commit"], counts["commit_file"]), (6, len(self.files)))


class Commands(unittest.TestCase):
    """CONTRACTS.md, "Every part runs as one command"."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cmd(self, part, *args):
        return subprocess.run([sys.executable, "-m", f"meridian.{part}", "--run-id", RUN, *args],
                              cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                              env={**os.environ, "MERIDIAN_DATA": str(self.tmp)})

    def test_history_then_writer(self):
        self.assertNotEqual(self.cmd("history").returncode, 0)  # no snapshot yet: fails, never guesses
        snap = self.tmp / "snapshots" / RUN
        (snap / "repos").mkdir(parents=True)
        sha = fixture_repo(snap / "repos" / "fix__repo")
        (snap / "manifest.json").write_text(json.dumps({"run_id": RUN, "snapshot_at": "2026-02-01T00:00:00Z",
                                                        "repos": {"fix/repo": sha}, "sources": {}}))
        for _ in range(2):  # an Airflow retry replaces the same rows
            r = self.cmd("history")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("fix/repo", r.stdout)
            self.assertIn("jira/SPARK", r.stdout)  # no ticket source in this snapshot: said, not skipped silently
        self.assertEqual((self.tmp / "out" / RUN / "ticket.jsonl").read_text(), "")
        r = self.cmd("writer")
        self.assertEqual(r.returncode, 0, r.stderr)
        db = sqlite3.connect(self.tmp / "out" / RUN / "meridian.sqlite")
        self.assertEqual(db.execute("SELECT count(*) FROM git_commit").fetchone(), (6,))


class Writer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / RUN
        self.out.mkdir()
        self.coverage([{"run_id": RUN, "metric": "b", "numerator": 1, "denominator": 2},
                       {"run_id": RUN, "metric": "a", "numerator": 3, "denominator": 4, "note": "x"}])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def coverage(self, rows, table="coverage"):
        (self.out / f"{table}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_same_rows_same_bytes(self):
        W.write(self.out, self.tmp / "a.sqlite")
        self.coverage([{"run_id": RUN, "metric": "a", "numerator": 3, "denominator": 4, "note": "x"},
                       {"run_id": RUN, "metric": "b", "numerator": 1, "denominator": 2}])  # other input order
        W.write(self.out, self.tmp / "b.sqlite")
        self.assertTrue(filecmp.cmp(self.tmp / "a.sqlite", self.tmp / "b.sqlite", shallow=False))
        rows = sqlite3.connect(self.tmp / "a.sqlite").execute("SELECT metric, note FROM coverage").fetchall()
        self.assertEqual(rows, [("a", "x"), ("b", "")])  # PK order; left-out column gets its DEFAULT

    def refused(self, rows, msg, table="coverage"):
        self.coverage(rows, table)
        with self.assertRaisesRegex(W.BadRow, msg):
            W.write(self.out, self.tmp / "x.sqlite")
        self.assertFalse((self.tmp / "x.sqlite").exists())

    def test_bad_rows_are_refused(self):
        ok = {"run_id": RUN, "metric": "a", "numerator": 1, "denominator": 2}
        self.refused([{**ok, "extra": 1}], "not in schema.sql")
        self.refused([{"run_id": RUN, "metric": "a"}], "missing columns")
        self.refused([{**ok, "numerator": "1"}], "should be INTEGER")
        self.refused([{**ok, "numerator": True}], "should be INTEGER")
        self.refused([{**ok, "run_id": "dev"}], "folder is")
        self.refused([ok, ok], "same primary key")
        self.refused([{"run_id": RUN, "repo": "r", "path": "p", "error": "e"}], "no table", table="nope")

    def test_check_constraints(self):
        self.refused([{"run_id": RUN, "key": "K-1", "target_kind": "fuzzy", "target": "", "method": "none"}],
                     "CHECK constraint", table="ticket_map")

    def test_sqlite_version_is_pinned(self):
        cfg = self.tmp / "m.toml"
        cfg.write_text('[run]\nsqlite_version = "0.0.1"\n')
        with self.assertRaisesRegex(SystemExit, "pins 0.0.1"):
            W.write(self.out, config=cfg)


if __name__ == "__main__":
    unittest.main()
