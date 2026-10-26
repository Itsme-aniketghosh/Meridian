"""Bias tests: the owner rule sliced by module and by team, its mitigations, ticket mapping and resolver slices, on a
small synthetic Spark-like history.

    python -m unittest tests.test_bias -v      # from Data-Pipeline/
"""
import io
import json
import shutil
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.bias as B
from meridian.common import write_jsonl

REPO = "acme/engine"
CONFIG = textwrap.dedent("""\
    [run]
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    handoff_dir = "out/{run_id}"
    [datasets.engine]
    repos = [{ repo = "acme/engine", ref = "x" }]
    [teams.engine]
    rule = "person_component"
    unassigned = "unassigned"
    [ownership]
    window_days = 180
    min_share = 0.30
    ignore_revs_file = ".git-blame-ignore-revs"
    [checks]
    source_files = '\\.(scala|java|py)$'
    test_files = '(^|/)tests?/'
    [scanner]
    edit_roles = ["import", "call", "alias"]
    """)
SETTINGS = textwrap.dedent("""\
    [bias]
    min_n = 1
    max_gap = 0.10
    min_shares = [0.30]
    team_lookback_days = [180, 730]
    """)

# (sha, author, authored_at, ticket key or None, [paths]). The cut is 2026-04-04: owners from (2025-10-06, cut]
COMMITS = [
    ("a0", "ann@x.test", "2025-01-10T00:00:00Z", "ENG-9", ["core/old.scala"]),   # Ann's only keyed commit: 2 years back
    ("a1", "sql@x.test", "2026-01-10T00:00:00Z", "ENG-1", ["sql/a.scala"]),
    ("a2", "sql@x.test", "2026-02-10T00:00:00Z", "ENG-1", ["sql/a.scala"]),
    ("a3", "ann@x.test", "2026-02-11T00:00:00Z", None, ["core/b.scala"]),
    ("a4", "sql@x.test", "2026-05-10T00:00:00Z", None, ["sql/a.scala"]),         # after the cut: next author, SQL
    ("a5", "ann@x.test", "2026-05-11T00:00:00Z", None, ["core/b.scala"]),        # after the cut: next author, Ann
]
TICKETS = {"ENG-1": (["SQL"], "Bug", "2026-01-01T00:00:00Z"), "ENG-9": (["Core"], "Bug", "2015-01-01T00:00:00Z"),
           "ENG-2": (["Core"], "Wish", "0012-10-19T00:09:00Z"),
           "ENG-3": (["SQL", "Core"], "Bug", "2026-02-01T00:00:00Z")}


class Bias(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "meridian.toml").write_text(CONFIG)
        (cls.tmp / "settings.toml").write_text(SETTINGS)
        out = cls.tmp / "out" / "R"
        out.mkdir(parents=True)
        people = sorted({c[1] for c in COMMITS})
        tables = {
            "run": [{"run_id": "R", "snapshot_at": "2026-10-01T00:00:00Z", "repo_shas": json.dumps({REPO: "x"})}],
            "git_commit": [dict(repo=REPO, sha=s, seq=i, is_merge=0, first_parent=1, authored_at=at, author_email=e)
                           for i, (s, e, at, _, _) in enumerate(COMMITS)],
            "commit_file": [dict(repo=REPO, sha=s, path=p, old_path=None) for s, _, _, _, ps in COMMITS for p in ps],
            "commit_ticket": [dict(repo=REPO, sha=s, key=k) for s, _, _, k, _ in COMMITS if k],
            "ticket": [dict(key=k, tracker="jira", components=json.dumps(c), effective_type=t, created_at=at)
                       for k, (c, t, at) in TICKETS.items()],
            "person": [dict(person_id=e, is_bot=0) for e in people],
            "person_alias": [dict(email=e, person_id=e) for e in people],
            "ticket_map": [dict(key="ENG-1", target_kind="path", target=f"{REPO}:sql/a.scala"),
                           dict(key="ENG-2", target_kind="unmapped", target=""),
                           dict(key="ENG-9", target_kind="unmapped", target="")],
            "candidate": [dict(repo=REPO, path="m.py", line=n, col=0, role="call") for n in (1, 2)],
            "resolution": [dict(repo=REPO, path="m.py", line=1, col=0, detection="ast_resolved"),
                           dict(repo=REPO, path="m.py", line=2, col=0, detection="dropped")],
        }
        for name, rows in tables.items():
            write_jsonl(out / f"{name}.jsonl", rows)
        with redirect_stdout(io.StringIO()):
            cls.report = B.main("R", cls.tmp / "meridian.toml", cls.tmp, cls.tmp / "settings.toml")
        cls.saved = json.loads((out / "bias.json").read_text())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_owner_rule_by_module(self):
        r = self.report["owner_rule"]
        # core/b.scala: Ann has no team, not scored
        self.assertEqual(list(r["accuracy_by_module"]["by_group"]), ["sql"])
        self.assertEqual(r["accuracy_by_module"]["by_group"]["sql"]["accuracy"], 1.0)
        core = r["owned_share_by_module"]["by_group"]["core"]
        self.assertEqual((core["owned_share"], r["assigned_share_by_module"]["by_group"]["core"]["assigned_share"]),
                         (0.0, 0.0))
        self.assertGreater(core["ci_high"], 0.4)                    # 0 of 1: its interval reaches the overall 0.5 ...
        self.assertEqual(r["owned_share_by_module"]["flagged"], [])   # ... so N = 1 can't be flagged even at min_n = 1

    def test_next_author_prior_commits(self):   # SQL made a1, a2 before the cut
        self.assertEqual(list(self.report["owner_rule"]["accuracy_by_next_author_prior_commits"]["by_group"]), ["2-10"])

    def test_a_longer_lookback_gives_ann_a_team(self):
        m = self.report["owner_rule"]["mitigations"]
        self.assertEqual(m["min_share 0.3, teams from 180 days"]["files_owned"], 1)
        self.assertEqual(m["teams from 730 days"]["files_owned"], 2)          # ENG-9 puts Ann in Core
        self.assertEqual((m["teams from 730 days"]["accuracy"], m["teams from 730 days"]["accuracy_assigned"]),
                         (1.0, 1.0))

    def test_held_out_cut_is_one_window_earlier(self):   # nothing spans 2025-10-06 here: the configured variant stays
        h = self.report["owner_rule"]["held_out"]
        self.assertEqual((h["cut"], h["files"], h["picked_at_earlier_cut"]),
                         ("2025-10-06T00:00:00Z", 0, "min_share 0.3, teams from 180 days"))
        self.assertEqual(h["delta"], 0.0)

    def test_ci_flag_rule_and_n_at_min_n(self):
        # A 30/30, B 0/30 (exactly min_n), C 0/3 (under it), D 9/30: overall 39/93 = 0.419, flag line 0.319
        by = ["A"] * 30 + ["B"] * 30 + ["C"] * 3 + ["D"] * 30
        y = pd.Series([1] * 30 + [0] * 33 + [1] * 9 + [0] * 21)
        r = B.frame({"rate": B.selection_rate, "n": B.count}, y, y, pd.Series(by), {"min_n": 30, "max_gap": 0.10})
        self.assertEqual([f["slice"] for f in r["flagged"]], ["B"])   # Wilson 0/30: [0, 0.114]
        self.assertFalse(r["by_group"]["C"]["flagged"])               # 0.0, but N = 3 isn't judged
        # 0.30 < 0.319, but its interval [0.167, 0.479] isn't
        self.assertFalse(r["by_group"]["D"]["flagged"])
        self.assertAlmostEqual(r["by_group"]["D"]["ci_high"], 0.479, places=3)
        self.assertEqual((r["tested"], r["difference"], r["ratio"]), (3, 1.0, 0.0))   # Fairlearn's, over A, B, D

    def test_mitigation_scoring(self):
        # variant "long" is right on 3 of 4 at the earlier cut (base 1 of 4), on every file now; the unassigned next
        # author is scored under neither
        rows = lambda base, long: pd.DataFrame({"owner|base": [o for o, _ in base], "next|base": [t for _, t in base],
                                                "owner|long": [o for o, _ in long], "next|long": [t for _, t in long]})
        early = rows([("X", "X"), ("X", "Y"), ("X", "Y"), ("X", "Y")], [("X", "X"), ("Y", "Y"), ("Y", "Y"), ("X", "Y")])
        now = rows([("X", "X"), ("X", "Y"), ("X", "X"), ("X", "Y"), ("X", "unassigned")],
                   [("X", "X"), ("Y", "Y"), ("X", "X"), ("Y", "Y"), ("X", "unassigned")])
        h = B.held_out(early, now, "unassigned", {"base": (0.3, 180), "long": (0.3, 730)})
        self.assertEqual((h["picked_at_earlier_cut"], h["accuracy_at_earlier_cut"]),
                         ("long", {"base": 0.25, "long": 0.75}))
        self.assertEqual((h["accuracy_now"], h["delta"]), ({"base": 0.5, "long": 1.0}, 0.5))
        lo, hi = h["delta_ci"]
        self.assertTrue(0 <= lo <= 0.5 <= hi <= 1)
        self.assertEqual(h, B.held_out(early, now, "unassigned", {"base": (0.3, 180), "long": (0.3, 730)}))   # seeded

    def test_ticket_mapping_and_resolver_slices(self):
        t = self.report["ticket_mapping"]
        self.assertEqual(t["mapped_share_by_component"]["by_group"]["Core"]["mapped_share"], 0.0)
        # ENG-3 is SQL and Core: counts in both
        self.assertEqual(t["mapped_share_by_component"]["by_group"]["SQL"]["n"], 2)
        self.assertEqual(t["mapped_share_by_year"]["by_group"]["0012"]["n"], 1.0)
        self.assertEqual(self.report["resolver"]["kept_share_by_repo"]["overall"]["kept_share"], 0.5)

    def test_no_jira_tickets_is_no_ticket_slices_not_a_crash(self):   # a dataset without a tracker
        out = self.tmp / "nojira"
        out.mkdir()
        write_jsonl(out / "ticket.jsonl", [])
        write_jsonl(out / "ticket_map.jsonl", [])
        self.assertEqual(B.mapping_report(out, {"min_n": 30, "max_gap": 0.1}), {})

    def test_written_as_json(self):
        self.assertEqual(self.saved["owner_rule"]["accuracy_by_module"],
                         self.report["owner_rule"]["accuracy_by_module"])


if __name__ == "__main__":
    unittest.main()
