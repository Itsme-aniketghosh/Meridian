"""The stage-6 tracking tables (suggestion, outcome, push_alert, policy, match) in schema.sql, through the writer and
validate. Step K of Model-Pipeline/packets/utkarsh-data.

    python -m unittest tests.test_tracking -v                 # from Data-Pipeline/
"""
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.graph as G
import meridian.validate as V
import meridian.writer as W
from meridian.common import SCHEMA, load_settings
from tests.test_dashboard import fixture_block
from tests.test_graph import CONFIG

SNAP = "2026-10-01T00:00:00Z"


def suggestion(**kw):
    return {"run_id": "R2", "suggestion_id": "SG1", "kind": "diff", "target_kind": "call_site", "target": "S_c4",
            "owner_team": "checkout", "source": "codemod", "body": "-ugettext(x)\n+gettext(x)", "facts_hash": "f",
            "offered_at": SNAP, "agent": "diff_drafter", "model": None, "route": "rules", "policy_id": None} | kw


def outcome(**kw):
    return {"suggestion_id": "SG1", "decided_run_id": "R2", "outcome": "applied", "decided_at": SNAP,
            "latency_days": 3, "rule": "diff_applied", "evidence": "[]"} | kw


def policy(pid="stall-v0", version=0, **kw):
    return {"policy_id": pid, "job": "stall", "version": version, "author": "us", "created_at": SNAP} | kw


def match(**kw):
    return {"match_id": "M1", "task_id": "T1", "policy_a": "stall-v0", "policy_b": "stall-v1", "result": "a",
            "mode": "live", "verdict_source": "rule", "decided_by": "block_removed", "decided_at": SNAP} | kw


class Load(unittest.TestCase):
    """Only the tables under test, in a folder named for the run, with a run row for snapshot_at."""

    def load(self, tables):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "R2"
        out.mkdir()
        tables = {"run": [{"run_id": "R2", "snapshot_at": SNAP, "repo_shas": "{}", "source_hashes": "{}",
                           "config_hash": "x", "image_digest": "x", "status": "complete"}]} | tables
        for t, rows in tables.items():
            (out / f"{t}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return W.write(out, tmp / "block.sqlite")

    def refused(self, tables, *words):
        with self.assertRaises(W.BadRow) as e:
            self.load(tables)
        for w in words:
            self.assertIn(w, str(e.exception))

    def test_good_rows_load(self):
        n = self.load({"policy": [policy(), policy("stall-v1", 1)],
                       "suggestion": [suggestion(), suggestion(suggestion_id="SG2", kind="stall_reason",
                                                               target_kind="ticket", target="CHK-1", source="llm",
                                                               model="small", route="jev", policy_id="stall-v1")],
                       "outcome": [outcome()], "match": [match()],
                       "push_alert": [{"push_sha": "d1", "base_run_id": "R1", "check_id": "P1", "target": "a.py:3",
                                       "evidence": "[]", "outcome": None, "decided_at": None}]})
        self.assertEqual({t: n[t] for t in W.TRACKING}, {"suggestion": 2, "outcome": 1, "push_alert": 1,
                                                          "policy": 2, "match": 1})

    def test_source_outside_the_allowed_values_is_refused(self):
        self.refused({"suggestion": [suggestion(source="gpt")]}, "suggestion.jsonl:1", "CHECK")

    def test_route_outside_the_allowed_values_is_refused(self):
        self.refused({"suggestion": [suggestion(route="random")]}, "CHECK")

    def test_agent_is_required(self):
        row = suggestion()
        del row["agent"]
        self.refused({"suggestion": [row]}, "missing columns: ['agent']")

    def test_outcome_for_an_unknown_suggestion_is_refused(self):
        self.refused({"suggestion": [suggestion()], "outcome": [outcome(suggestion_id="SG9")]},
                     "outcome.jsonl:1", "'SG9'", "no suggestion row")
        self.refused({"outcome": [outcome()]}, "no suggestion row")  # no suggestion file at all

    def test_match_needs_both_policies(self):
        self.refused({"policy": [policy()], "match": [match()]}, "'stall-v1'", "no policy row")
        self.refused({"policy": [policy(), policy("stall-v1", 1)], "match": [match(policy_b="stall-v0")]}, "CHECK")

    def test_policy_versions_are_unique_per_job(self):
        self.refused({"policy": [policy(), policy("other", 0)]}, "UNIQUE")

    def test_an_earlier_runs_suggestion_rides_along_if_known_by_the_snapshot(self):
        self.load({"suggestion": [suggestion(run_id="R1", offered_at="2026-09-01T00:00:00Z")]})
        self.refused({"suggestion": [suggestion(run_id="R1", offered_at="2026-10-02T00:00:00Z")]}, "earlier run")

    def test_nothing_decided_after_the_snapshot(self):
        late = "2026-10-02T00:00:00Z"
        self.refused({"suggestion": [suggestion()], "outcome": [outcome(decided_at=late)]}, "after this run's")
        self.refused({"policy": [policy(), policy("stall-v1", 1)], "match": [match(decided_at=late)]},
                     "after this run's")
        self.refused({"policy": [policy(created_at=late)]}, "after this run's")


class Block(unittest.TestCase):
    """A complete block (the KB fixture through graph) with the tracking tables empty or left out."""

    def block(self, tracking):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "meridian.toml").write_text(CONFIG)
        out = fixture_block(tmp)
        with redirect_stdout(StringIO()):
            G.main("R2", tmp / "meridian.toml", tmp)
        for t in W.TRACKING:
            (out / f"{t}.jsonl").unlink(missing_ok=True)
            if tracking == "empty":
                (out / f"{t}.jsonl").write_text("")
        return out

    def test_complete_block_loads_with_tracking_left_out_or_empty(self):
        for tracking in ("left out", "empty"):
            with self.subTest(tracking=tracking):
                out = self.block(tracking)
                n = W.write(out, out / "meridian.sqlite", complete=True)
                self.assertEqual({t: n[t] for t in W.TRACKING}, dict.fromkeys(W.TRACKING, 0))
                with closing(sqlite3.connect(out / "meridian.sqlite")) as db:
                    spec = W.tables(SCHEMA.read_text(encoding="utf-8"))
                    stats = {t: dict(zip(("rows", "columns"), V.column_stats(db, t, spec[t], 10))) for t in spec}
                    found = V.check(db, spec, stats, load_settings("validate"), None)
                self.assertEqual([f for f in found if f[2].split(".")[0] in W.TRACKING], [])

    def test_a_part_table_left_out_is_still_an_error(self):
        out = self.block("empty")
        (out / "coverage.jsonl").unlink()
        with self.assertRaises(W.BadRow):
            W.write(out, out / "meridian.sqlite", complete=True)

    def test_references_come_from_schema_sql(self):
        self.assertEqual(sorted(W.references(SCHEMA.read_text(encoding="utf-8"))),
                         [("match", "policy_a", "policy", "policy_id"), ("match", "policy_b", "policy", "policy_id"),
                          ("outcome", "suggestion_id", "suggestion", "suggestion_id"),
                          ("suggestion", "policy_id", "policy", "policy_id")])


if __name__ == "__main__":
    unittest.main()
