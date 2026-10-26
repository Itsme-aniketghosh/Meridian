"""GX audit tests. The suite's specs and the never-gate rules run anywhere; the Great Expectations run itself only where
great_expectations is installed (the [gx] python environment, requirements-gx.txt; CI's gx job).

    python -m unittest tests.test_gx -v                       # from Data-Pipeline/: specs and rules
    /tmp/gx/bin/python -m unittest tests.test_gx -v           # with GX: the suite on a small block, and its Data Docs
"""
import importlib.util
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.gx as G

TOML = '[run]\nhandoff_dir = "out/{run_id}"\n'
SETTINGS = """\
[publish]
dir = "published"
[validate.rows]
git_commit = 2
[validate.missing]
default = 0.0
"git_commit.parent_sha" = 0.5
[validate.dates]
columns = '.*_at|at'
earliest = "2000-01-01T00:00:00Z"
[validate.ranges]
"*.seq" = [0, 1000]
[[validate.formats]]
name = "a UTC time"
columns = '.*_at'
pattern = '\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\dZ'
[[validate.formats]]
name = "a sorted JSON list"
columns = 'components'
pattern = "sorted_json_list"
"""
EXP = {"rows": {"git_commit": 2}, "missing": {"default": 0.0, "git_commit.parent_sha": 0.5},
       "dates": {"columns": ".*_at|at", "earliest": "2000-01-01T00:00:00Z"}, "ranges": {"*.seq": [0, 1000]},
       "formats": [{"name": "a UTC time", "columns": ".*_at", "pattern": r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"},
                   {"name": "a sorted JSON list", "columns": "components", "pattern": "sorted_json_list"}]}


def block(path, commits):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE run (run_id TEXT, snapshot_at TEXT)")
        db.execute("INSERT INTO run VALUES ('R', '2026-10-01T00:00:00Z')")
        db.execute("CREATE TABLE git_commit (sha TEXT, parent_sha TEXT, seq INTEGER, authored_at TEXT)")
        db.executemany("INSERT INTO git_commit VALUES (?, ?, ?, ?)", commits)


GOOD = [("a", None, 0, "2026-01-01T00:00:00Z"), ("b", "a", 1, "2026-02-01T00:00:00Z")]


class Specs(unittest.TestCase):
    def test_checks_come_from_validate_settings(self):
        got = G.specs(EXP, "git_commit", ["sha", "parent_sha", "seq", "authored_at", "components"], "2026-10-01T00:00:00Z")
        kinds = {(s["kind"], s.get("column")) for s in got}
        self.assertIn(("rows", None), kinds)
        self.assertIn(("not_null", "sha"), kinds)                                  # default 0.0: no NULL or ''
        self.assertIn(("between", "seq"), kinds)
        self.assertIn(("regex", "authored_at"), kinds)
        self.assertIn(("dates", "authored_at"), kinds)
        self.assertNotIn(("regex", "components"), kinds)                           # sorted JSON lists stay in validate
        self.assertEqual(next(s for s in got if s.get("column") == "parent_sha")["mostly"], 0.5)
        self.assertEqual(next(s for s in got if s["kind"] == "dates")["max"], "2026-10-01T00:00:00Z")

    def test_year_0010_survives_as_a_datetime(self):   # pandas timestamps stop at 1677; the 289 Jira dates are year 0010
        self.assertEqual(G.as_datetime("0010-04-03T23:04:00Z").year, 10)
        self.assertIsNone(G.as_datetime(""))
        self.assertIsNone(G.as_datetime("2026-01-01 00:00:00"))   # not a UTC time: the format check reports it

    def test_a_check_gx_couldnt_run_is_told_apart_from_a_failure(self):
        r = lambda info: mock.Mock(exception_info=info)
        self.assertTrue(G.crashed(r({"raised_exception": True})))
        self.assertTrue(G.crashed(r({"metric-id": {"raised_exception": True, "exception_message": "x"}})))
        self.assertFalse(G.crashed(r({"raised_exception": False, "exception_message": None})))

    def test_never_gates_failures_warn_and_a_broken_check_is_an_error(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "meridian.toml").write_text(TOML)
        (tmp / "settings.toml").write_text(SETTINGS)
        block(tmp / "out" / "R" / "meridian.sqlite", GOOD)
        site = tmp / "site"
        site.mkdir()
        (site / "index.html").write_text("docs")
        failed = [{"severity": "warn", "check": "dates", "where": "t.c", "unexpected": 1, "of": 2, "examples": []}]
        broken = [{"check": "dates", "where": "t.c"}]
        run = lambda f, b: G.main("R", tmp / "meridian.toml", tmp, settings=tmp / "settings.toml")
        with mock.patch.object(G, "audit", return_value=(5, failed, [], site)), mock.patch.object(G.alert, "send") as send, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(run(failed, []), 0)                                       # a failure alerts, the task succeeds
            self.assertEqual(send.call_args.args[2], "warn")
        with mock.patch.object(G, "audit", return_value=(5, [], broken, site)), mock.patch.object(G.alert, "send") as send, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(run([], broken), 1)                                       # a broken audit is never "clean"
            self.assertEqual(send.call_args.args[2], "error")
        self.assertEqual((tmp / "published" / "gx" / "R" / "index.html").read_text(), "docs")
        self.assertEqual(json.loads((tmp / "published" / "gx" / "latest.json").read_text())["index"], "R/index.html")

    def test_no_block_no_audit(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "meridian.toml").write_text(TOML)
        with self.assertRaisesRegex(SystemExit, "run meridian.validate first"):
            G.main("R", tmp / "meridian.toml", tmp)


@unittest.skipUnless(importlib.util.find_spec("great_expectations"), "GX isn't installed here: run it in the [gx] environment")
class Audit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "meridian.toml").write_text(TOML)
        (self.tmp / "settings.toml").write_text(SETTINGS)

    def run_gx(self, commits, publish_root=None):
        db = self.tmp / "out" / "R" / "meridian.sqlite"
        db.unlink(missing_ok=True)
        block(db, commits)
        with mock.patch.object(G.alert, "send") as send, redirect_stdout(io.StringIO()):
            code = G.main("R", self.tmp / "meridian.toml", self.tmp, publish_root, self.tmp / "settings.toml")
        return code, json.loads((self.tmp / "out" / "R" / "gx.json").read_text()), send

    def test_a_clean_block_passes_and_its_data_docs_are_published(self):
        code, got, send = self.run_gx(GOOD)
        self.assertEqual((code, got["failed"], got["crashed"]), (0, [], []))
        self.assertGreater(got["expectations"], 5)
        send.assert_not_called()
        self.assertTrue((self.tmp / "published" / "gx" / "R" / "index.html").exists())

    def test_bad_rows_are_found_and_alerted_not_gated(self):
        bad = GOOD + [("c", "b", 5000, "0010-04-03T23:04:00Z"), ("d", "c", 3, "2026-01-01 00:00:00")]
        code, got, send = self.run_gx(bad, publish_root=self.tmp / "main")
        found = {(f["check"], f["where"]) for f in got["failed"]}
        self.assertIn(("dates", "git_commit.authored_at"), found)        # year 0010: kept as a datetime, so caught
        self.assertIn(("ranges", "git_commit.seq"), found)               # 5000 > 1000
        self.assertIn(("formats", "git_commit.authored_at"), found)      # a space, not ISO
        self.assertEqual(got["crashed"], [])                             # every check ran: none mistaken for a failure
        self.assertEqual(code, 0)                                        # alerted, never gated
        self.assertEqual(send.call_args.args[2], "warn")
        self.assertTrue((self.tmp / "main" / "published" / "gx" / "R" / "index.html").exists())   # where views read


if __name__ == "__main__":
    unittest.main()
