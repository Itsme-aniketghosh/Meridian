"""The auditor (04 §9): policy v0, ε-greedy, the verdict rules, live matches, Elo and the promotion guard, against
hand calculations and Model-Pipeline/packets/rochan-tracking/pass-bars.md, section 4.

    python -m unittest tests.test_auditor -v      # from Data-Pipeline/

Policy v0 runs on KNOWLEDGE-BASE.md's fixture (tests/test_kb.py), whose stall reasons the doc already proves. Elo is
checked by hand, and on simulated policies of known skill that all rank every stall in shadow, as the stall job runs.
"""
import contextlib
import dataclasses
import io
import json
import math
import random
import sqlite3
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import auditor as A
from meridian import auditor_sim as S
from meridian.common import CONFIG, load_config

CFG = load_config(CONFIG)


def fixture(at=K.R2[1]):
    return A.point(K.build(), at, K.REPO, K.SYMBOL, "R2")


def build():
    """The fixture, with R1 having written the tables the stall reasons derive from too, the same as R2's: the
    story's owners, imports and the platform block were all there by Sep 1. K.build() has them at R2 only, like a real
    block, so as of R1 the reasons that read them are unknown there (KnownAt)."""
    db = K.build()
    for t in ("blocked_by", "file_owner", "person_alias", "team_map", "module_import"):
        cols = ", ".join(r[1] for r in db.execute(f"PRAGMA table_info({t})") if r[1] != "run_id")
        db.execute(f"INSERT INTO {t} (run_id, {cols}) SELECT 'R1', {cols} FROM {t} WHERE run_id = 'R2'")
    return db


def match(i, a, b, result, mode="live", at="2026-10-01T00:00:00Z"):
    return dict(match_id=f"m{i:05d}", task_id=f"t{i}", policy_a=a, policy_b=b, result=result, mode=mode,
                verdict_source="rule", decided_by="fixture", decided_at=at)


def shadow(skills, n, seed):
    return S.shadow(skills, n, seed)


def order(ratings):
    return S.order(ratings)


def r1_stalls(db):
    return {x.team: x for x in A.block_stalls(db, CFG, K.R1[1], "R2")}


def add_ticket(db, key, created_at, site):
    db.execute("INSERT INTO ticket (run_id, key, tracker, type, effective_type, status, created_at, bulk_closed, "
               "desc_empty, summary) VALUES ('R2', ?, 'jira', 'Task', 'Task', 'Open', ?, 0, 0, 'fixture')",
               (key, created_at))
    db.execute("INSERT INTO ticket_map VALUES ('R2', ?, 'call_site', ?, 'symbol')", (key, site))


def remove_site(db, site, on, run=K.R2):
    """A new version of `site`: removed on `on`, known at `run`."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(call_site)")]
    row = dict(zip(cols, db.execute("SELECT * FROM call_site WHERE call_site_id = ? ORDER BY known_at DESC",
                                    (site,)).fetchone()))
    row.update(run_id=run[0], status="removed", effective_date=on, known_at=run[1])
    db.execute(f"INSERT INTO call_site ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
               [row[c] for c in cols])


def resumed_by(db, site, email):
    """The commit that removed `site` (and resumed its team) was authored by `email`."""
    sha = (site.encode().hex() * 40)[:40]
    db.execute("UPDATE call_site SET effective_sha = ? WHERE call_site_id = ? AND status = 'removed'", (sha, site))
    db.execute("INSERT INTO git_commit VALUES ('R2', 'acme/shop', ?, NULL, 99, 1, 0, '2026-09-12T00:00:00Z', ?, ?, "
               "'remove')", (sha, email, email))


def stall_v8(stall):
    """A test policy: the reasons that fire, highest number first."""
    return sorted(stall.facts, reverse=True)


def early(policy_id):
    """A policy as if it had been written before the fixture's runs."""
    return dataclasses.replace(A.POLICIES[policy_id], created_at="2026-01-01T00:00:00Z")


# the as-of ticket_map before it was built as one list (PR: same rows, 7 minutes -> 0.3 s on a real block)
OLD_TICKET_MAP = """SELECT m.* FROM main.ticket_map m, asof
WHERE NOT EXISTS (SELECT 1 FROM main.ticket t WHERE t.key = m.key AND t.created_at > asof.at)"""


class AsOfTicketMap(unittest.TestCase):
    def test_it_keeps_the_same_rows_as_before_at_every_moment(self):
        db = K.build()  # the tickets go in before point() puts its views over the tables
        add_ticket(db, "CHK-900", "2026-09-20T00:00:00Z", "S_c1")  # created between R1 and R2
        add_ticket(db, "PAY-900", "2026-09-25T00:00:00Z", "S_p2")
        for at in (K.R1[1], "2026-09-15T00:00:00Z", "2026-09-22T00:00:00Z", K.R2[1], "2026-01-01T00:00:00Z"):
            A.point(db, at, K.REPO, K.SYMBOL, "R2")
            now = sorted(db.execute("SELECT * FROM ticket_map").fetchall())
            before = sorted(db.execute(OLD_TICKET_MAP).fetchall())
            self.assertEqual(now, before, at)
        A.point(db, K.R1[1], K.REPO, K.SYMBOL, "R2")
        later = db.execute("SELECT count(*) FROM main.ticket WHERE created_at > ?", (K.R1[1],)).fetchone()[0]
        self.assertGreater(later, 0)  # the moments above do drop maps: the test isn't vacuous
        self.assertLess(db.execute("SELECT count(*) FROM ticket_map").fetchone()[0],
                        db.execute("SELECT count(*) FROM main.ticket_map").fetchone()[0])


def add_run(db, run):
    db.execute("INSERT INTO run (run_id, snapshot_at, repo_shas, source_hashes, config_hash, image_digest, status) "
               "VALUES (?, ?, '{}', '{}', 'x', 'x', 'complete')", run)


class KnownAt(unittest.TestCase):
    """05-test's Auditor bar 'known_at leak' (pass-bars.md §4): facts as of a moment read only the rows known by then.
    Rows go in before point() puts its views over the tables."""

    def test_a_table_a_run_derives_is_unknown_before_that_runs_snapshot(self):
        """K.build() has R2's owners, imports and blocks only, like a real block. As of R1 the reasons that read them
        are unknown and don't fire: an empty module_import would have made 7 Test gap fire for every team."""
        db = K.build()
        tables = ["blocked_by", "file_owner", "module_import", "person_alias", "team_map"]
        self.assertEqual(A.unknown_at(db, K.R1[1]), ([1, 4, 7, 8], tables))
        r1 = {x.team: sorted(x.facts) for x in A.block_stalls(db, CFG, K.R1[1], "R2")}
        self.assertEqual(r1, {"billing": [2], "checkout": [5], "fulfillment": [], "payments": [], "platform": [],
                              "search": [2]})
        self.assertEqual(A.unknown_at(db, K.R2[1]), ([], []))
        self.assertEqual(A.unknown_at(build(), K.R1[1]), ([], []))  # R1 wrote its own

    def test_a_tickets_status_and_resolution_are_rebuilt_as_of_the_moment(self):
        db = K.build()
        db.executemany("INSERT INTO ticket_event VALUES ('R2', 'PAY-10', ?, ?, ?, ?, 'pia@acme.test', ?)", [
            ("2026-08-10T00:00:00Z", "status", "Open", "In Progress", 0),
            ("2026-09-15T00:00:00Z", "status", "In Progress", "Closed", 0),
            ("2026-09-15T00:00:00Z", "resolution", None, "Fixed", 1)])

        def then(at):
            return A.point(db, at).execute("SELECT status, resolution, resolved_at FROM ticket WHERE key = 'PAY-10'"
                                           ).fetchone()

        self.assertEqual(then("2026-08-01T00:00:00Z"), ("Open", None, None))  # what its first change changed from
        self.assertEqual(then(K.R1[1]), ("In Progress", None, None))
        self.assertEqual(then(K.R2[1]), ("Closed", "Fixed", "2026-09-15T00:00:00Z"))
        self.assertIsNone(then("2026-05-01T00:00:00Z"))  # created Jun 1

    def test_with_no_change_on_record_a_ticket_resolved_later_wasnt_done_then(self):
        """PAY-10 was closed on Sep 15 and its site is still there: 3 Ticket mismatch at R2, not at R1."""
        db, q = build(), A.kb_queries()

        def mismatch(at):
            return A.point(db, at, K.REPO, K.SYMBOL).execute(q["stall_ticket_mismatch"]).fetchall()

        self.assertEqual((mismatch(K.R1[1]), mismatch(K.R2[1])), ([], [("payments", "PAY-10", "S_p1")]))
        A.point(db, K.R1[1])
        self.assertIsNone(db.execute("SELECT status FROM ticket WHERE key = 'PAY-10'").fetchone()[0])

    def test_commits_and_their_files_are_those_authored_by_then(self):
        """So 8 Competing load at R1 counts Bob's 2 commits in the 14 days before, not his 4 from late September."""
        db = A.point(build(), K.R1[1])
        tables = ("git_commit", "commit_file", "main.git_commit", "main.commit_file")
        self.assertEqual([db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables], [9, 9, 14, 15])
        r1 = {x.team: x.facts for x in A.block_stalls(db, CFG, K.R1[1], "R2")}
        self.assertEqual(r1["checkout"][8], [(2, 1.75)])

    @staticmethod
    def planted():
        """build(), with one row of each kind the stall reasons read known after R1, each one that would change R1's
        facts if it were read."""
        db = build()
        r1b = ("R1b", "2026-09-15T00:00:00Z")
        add_run(db, r1b)
        remove_site(db, "S_b1", "2026-09-14T00:00:00Z", r1b)  # billing's only site: billing would be done
        add_ticket(db, "SRCH-9", "2026-09-05T00:00:00Z", "S_s2")  # search would be notified: no 2
        db.execute("UPDATE ticket SET status = 'Done' WHERE key = 'CHK-298'")  # checkout's: 3 would fire
        db.execute("INSERT INTO ticket_event VALUES ('R2', 'CHK-298', '2026-09-05T00:00:00Z', 'status', "
                   "'In Progress', 'Done', 'bob@acme.test', 0)")
        db.execute("INSERT INTO git_commit VALUES ('R2', 'acme/shop', 'f1', NULL, 99, 1, 0, '2026-09-10T00:00:00Z', "
                   "'ann@acme.test', 'ann@acme.test', 'back')")  # Ann is back: no 4
        db.execute("INSERT INTO commit_file VALUES ('R2', 'acme/shop', 'f1', 'cart/pricing.py', NULL, 1, 1)")
        db.executemany("INSERT INTO module_import VALUES ('R1b', 'acme/shop', 'tests/test_all.py', ?, ?, '', 1)",
                       enumerate(["billing.invoice", "cart.pricing", "pay.charge", "search.q", "shared.client"]))
        db.execute("INSERT INTO blocked_by VALUES ('R1b', 'S_c1', 'S_shared', 'acme/shop:shared/client.py:f', 1, "
                   "'resolved')")  # checkout would be blocked: 1
        return db

    def test_rows_known_after_the_moment_change_nothing_then(self):
        """R1's facts are the same with and without the planted rows. On Sep 20, after they're known, they differ."""
        clean, later = build(), self.planted()

        def facts(db, at):
            return {x.team: (x.since, x.facts) for x in A.block_stalls(db, CFG, at, "R2")}

        self.assertEqual(facts(later, K.R1[1]), facts(clean, K.R1[1]))
        self.assertNotEqual(facts(later, "2026-09-20T00:00:00Z"), facts(clean, "2026-09-20T00:00:00Z"))

    def test_no_table_the_queries_read_holds_a_row_known_after_the_moment(self):
        """The bar as written: 0 rows known after the stall, in every table the queries read, while the block under
        them holds such rows of each kind. A derived row's moment is its run's snapshot. A ticket map's is its
        ticket's creation, so a later run's map of an earlier ticket is still read: the one gap (decisions.md, second
        look at the registry and the auditor, Nov 10)."""
        db = A.point(self.planted(), K.R1[1])
        at = "(SELECT at FROM asof)"
        later_run = f"run_id NOT IN (SELECT run_id FROM main.run WHERE snapshot_at <= {at})"
        checks = {"call_site": ("site_now", f"known_at > {at}"),
                  "ticket": ("ticket", f"created_at > {at}"),
                  "ticket_event": ("ticket_event", f"at > {at}"),
                  "ticket_map": ("ticket_map", f"key IN (SELECT key FROM main.ticket WHERE created_at > {at})"),
                  "git_commit": ("git_commit", f"authored_at > {at}"),
                  "commit_file": ("commit_file", f"sha IN (SELECT sha FROM main.git_commit WHERE authored_at > {at})"),
                  **{t: (t, later_run) for t in A.DERIVED}}
        for table, (view, later) in checks.items():
            with self.subTest(table):
                self.assertGreater(db.execute(f"SELECT count(*) FROM main.{table} WHERE {later}").fetchone()[0], 0)
                self.assertEqual(db.execute(f"SELECT count(*) FROM {view} WHERE {later}").fetchone()[0], 0)


class PolicyV0(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = fixture()
        cls.stalls = {s.team: s for s in A.stalls(cls.db, CFG)}

    def test_stalled_is_04s_rule_blocked_or_not(self):
        """Sites left and no change for [rules] stall_days. Fulfillment is blocked and still stalled: reason #1 only
        fires for blocked teams. Payments changed 3 days ago, and search is done."""
        self.assertEqual({t: (s.days, s.left) for t, s in self.stalls.items()},
                         {"billing": (264, 1), "checkout": (19, 3), "fulfillment": (264, 1), "platform": (264, 1)})

    def test_v0_ranks_the_reasons_that_fire_by_number(self):
        v0 = A.POLICIES["stall-v0"]
        self.assertEqual({t: v0.rank(s) for t, s in self.stalls.items()},
                         {"billing": [2, 7], "checkout": [4, 5, 7, 8], "fulfillment": [1], "platform": [7]})

    def test_evidence_is_the_doc_queries_rows(self):
        facts = {t: s.facts for t, s in self.stalls.items()}
        self.assertEqual(facts["fulfillment"], {1: [("S_f1", "S_shared", "platform")]})
        self.assertEqual(facts["billing"], {2: [(1,)], 7: [("billing.invoice",)]})
        self.assertEqual(facts["checkout"], {4: [("cart/pricing.py", "p_ann", 91)],
                                             5: [("S_c2", "cart/pricing.py", 231)], 7: [("cart.pricing",)],
                                             8: [(4, 2.0)]})

    def test_facts_at_an_earlier_moment_never_read_a_later_ticket(self):
        """billing has no ticket, so 2 Never notified fires. A ticket made on Sep 20 is known at R2, not at R1."""
        db = build()
        db.execute("INSERT INTO ticket (run_id, key, tracker, type, effective_type, status, created_at, bulk_closed, "
                   "desc_empty, summary) VALUES ('R2', 'BIL-9', 'jira', 'Task', 'Task', 'Open', "
                   "'2026-09-20T00:00:00Z', 0, 0, 'fixture')")
        site = db.execute("SELECT call_site_id FROM call_site WHERE owner_team = 'billing'").fetchone()[0]
        db.execute("INSERT INTO ticket_map VALUES ('R2', 'BIL-9', 'call_site', ?, 'symbol')", (site,))
        teams = {at: {s.team: sorted(s.facts) for s in A.block_stalls(db, CFG, at, "R2")} for at in (K.R1[1], K.R2[1])}
        self.assertEqual((teams[K.R1[1]]["billing"], teams[K.R2[1]]["billing"]), ([2, 7], [7]))

    def test_evidence_never_reorders_v0_and_there_are_no_probabilities(self):
        s = self.stalls["billing"]._replace(facts={7: [("a",), ("b",), ("c",)], 2: [(1,)]})
        self.assertEqual(A.stall_v0(s), [2, 7])  # reason numbers only, by number

    def test_a_stall_keeps_its_task_while_nothing_changes(self):
        later = {s.team: s.task_id for s in A.stalls(fixture("2026-10-20T00:00:00Z"), CFG)}
        self.assertEqual({t: later[t] for t in self.stalls}, {t: s.task_id for t, s in self.stalls.items()})
        self.assertIn("payments", later)  # its Sep 28 change is 22 days old by then: a new stall

    def test_a_change_starts_a_new_stall(self):
        before = {s.team: s for s in A.stalls(fixture(K.R1[1]), CFG)}
        self.assertEqual(before["checkout"].since, "2026-01-10T00:00:00Z")  # :250's removal isn't known at R1
        self.assertNotEqual(before["checkout"].task_id, self.stalls["checkout"].task_id)
        self.assertEqual(before["billing"].task_id, self.stalls["billing"].task_id)

    def test_stall_days_comes_from_meridian_toml(self):
        cfg = {**CFG, "rules": {**CFG["rules"], "stall_days": 20}}
        self.assertEqual(sorted(s.team for s in A.stalls(self.db, cfg)), ["billing", "fulfillment", "platform"])

    def test_a_block_is_read_one_repo_and_symbol_at_a_time(self):
        db = K.build()
        db.execute("INSERT INTO call_site VALUES (" + ", ".join("?" * 18) + ")",
                   list(dict(K.site("S_o1", K.R1, "old/api.py", 3, "go", "billing"), repo="acme/other",
                             symbol="old_api").values()))
        got = [(s.repo, s.symbol, s.team) for s in A.block_stalls(db, CFG, K.R2[1], "R2")]
        self.assertEqual(got, [("acme/other", "old_api", "billing")]
                         + [(K.REPO, K.SYMBOL, t) for t in ("billing", "checkout", "fulfillment", "platform")])
        mine = [s for s in A.block_stalls(db, CFG, K.R2[1], "R2") if s.repo == K.REPO and s.team == "billing"]
        self.assertEqual(mine[0].facts, self.stalls["billing"].facts)  # the other repo's site doesn't leak in


class Elo(unittest.TestCase):
    def test_one_win_between_equals_moves_16_each_way(self):
        self.assertEqual(A.elo([match(0, "v0", "v1", "a")]), ({"v0": 1516.0, "v1": 1484.0}, {"v0": 1, "v1": 1}))

    def test_a_draw_between_equals_moves_nothing_and_a_replay_moves_half(self):
        self.assertEqual(A.elo([match(0, "v0", "v1", "draw")])[0], {"v0": 1500.0, "v1": 1500.0})
        self.assertEqual(A.elo([match(0, "v0", "v1", "b", mode="replay")])[0], {"v0": 1492.0, "v1": 1508.0})

    def test_two_matches_by_hand(self):
        r, _ = A.elo([match(0, "v0", "v1", "a"), match(1, "v1", "v0", "a")])
        e = 1 / (1 + 10 ** ((1516 - 1484) / 400))  # v1's expected score from 1484 against 1516: 0.4541
        self.assertAlmostEqual(r["v1"], 1484 + 32 * (1 - e), places=9)
        self.assertAlmostEqual(r["v0"], 1516 - 32 * (1 - e), places=9)
        self.assertAlmostEqual(r["v1"], 1501.4695, places=4)

    def test_k_drops_to_16_after_30_matches_each_side_by_its_own_count(self):
        log = [match(i, "v0", "v1", "draw") for i in range(30)]  # draws between equals move nothing, but count
        log.append(match(30, "v0", "v1", "a"))
        self.assertEqual(A.elo(log)[0], {"v0": 1508.0, "v1": 1492.0})
        log.append(match(31, "v2", "v0", "a", at="2026-10-02T00:00:00Z"))  # a newcomer still moves by 32
        r, n = A.elo(log)
        e = 1 / (1 + 10 ** ((1508 - 1500) / 400))
        self.assertAlmostEqual(r["v2"], 1500 + 32 * (1 - e), places=9)
        self.assertAlmostEqual(r["v0"], 1508 - 16 * (1 - e), places=9)
        self.assertEqual(n, {"v0": 32, "v1": 31, "v2": 1})

    def test_recomputed_from_the_log_twice_gives_the_same_ratings_exactly(self):
        """pass-bars.md §4 'Recompute'. The log in another order too: Elo sorts it by (decided_at, match_id)."""
        log = shadow({"stall-v0": 1500, "stall-v1": 1700, "stall-v2": 1300}, 600, seed=7)
        log += [dict(m, match_id="r" + m["match_id"], mode="replay", decided_at="2026-10-02T00:00:00Z")
                for m in log[:100]]
        shuffled = log[:]
        random.Random(1).shuffle(shuffled)
        self.assertEqual(A.elo(log), A.elo(log))
        self.assertEqual(A.elo(log), A.elo(shuffled))  # floats compared with ==: the same to the last bit

    def test_a_row_the_log_cant_hold_stops(self):
        for bad in (dict(result="x"), dict(mode="shadow"), dict(policy_b="v0")):
            with self.subTest(**bad), self.assertRaises(ValueError):
                A.elo([dict(match(0, "v0", "v1", "a"), **bad)])

    def test_settings_are_04s_unless_meridian_toml_has_an_auditor_section(self):
        self.assertEqual(A.settings({}), {"elo_start": 1500, "k": 32, "k_after": 16, "k_after_matches": 30,
                                          "replay_k": 0.5, "eps": 0.2, "eps_after": 0.1, "eps_after_matches": 30,
                                          "promote_matches": 30, "promote_elo": 50, "promote_luck": None,
                                          "promote_gain": 200, "block_days": 7,
                                          "ticket_days": 14, "explore_budget": 200, "reply_days": 14})
        self.assertEqual(A.settings({"auditor": {"k": 24}})["k"], 24)
        with self.assertRaises(SystemExit):
            A.settings({"auditor": {"K": 24}})


class Epsilon(unittest.TestCase):
    """pass-bars.md §4 'Seeded ε', and 04 §9's schedule."""

    def test_the_challenger_share_is_within_2_points_of_eps_over_10000_seeded_draws(self):
        for eps in (0.2, 0.1):
            picks = [A.serve("R2", f"task-{i}", "stall-v0", ["stall-v1", "stall-v2"], eps) for i in range(10_000)]
            challengers = sum(p != "stall-v0" for p in picks)
            self.assertAlmostEqual(challengers / len(picks), eps, delta=0.02, msg=eps)
            self.assertAlmostEqual(picks.count("stall-v1") / challengers, 0.5, delta=0.05, msg="a random challenger")

    def test_a_task_in_a_run_is_served_the_same_every_time_and_another_run_draws_again(self):
        picks = [A.serve("R2", f"t{i}", "stall-v0", ["stall-v1"], 0.5) for i in range(200)]
        self.assertEqual(picks, [A.serve("R2", f"t{i}", "stall-v0", ["stall-v1"], 0.5) for i in range(200)])
        self.assertNotEqual(picks, [A.serve("R3", f"t{i}", "stall-v0", ["stall-v1"], 0.5) for i in range(200)])

    def test_eps_is_02_until_the_job_has_30_live_matches_then_01(self):
        s = A.settings({})
        self.assertEqual([A.epsilon(n, s) for n in (0, 29, 30, 500)], [0.2, 0.2, 0.1, 0.1])

    def test_with_no_challenger_the_champion_is_served(self):
        self.assertEqual({A.serve("R2", f"t{i}", "stall-v0", [], 0.9) for i in range(100)}, {"stall-v0"})


class Promotion(unittest.TestCase):
    """pass-bars.md §4 'Promotion guard': at least 30 live matches, +50 Elo over the champion, a winning live record
    against v0, and a person for an LLM's policy. Blocks at 29 matches and at +49 Elo; replay never counts."""
    S = A.settings({})
    LUCK = A.settings({"auditor": {"promote_luck": 0.05}})

    def league(self, live=30, gap=50.0, vs_v0=(20.0, 30), author="rh", approved_by="", s=None):
        new = A.Policy("stall-v9", "stall", 9, author, A.stall_v0, "2026-01-01T00:00:00Z", approved_by)
        league = A.League(s or self.S, {"stall-v0": A.POLICIES["stall-v0"], "stall-v9": new})
        league.rating = {"stall-v0": 1500.0, "stall-v9": 1500.0 + gap}
        league.live = {"stall-v0": live, "stall-v9": live}
        league.h2h = {("stall-v9", "stall-v0"): vs_v0}
        return league

    def test_30_live_matches_and_plus_50_pass(self):
        self.assertEqual(self.league().guard("stall-v9"), [])

    def test_29_live_matches_block(self):
        self.assertEqual(self.league(live=29).guard("stall-v9"), ["29 live match(es), needs 30"])

    def test_plus_49_elo_blocks(self):
        self.assertEqual(self.league(gap=49.9).guard("stall-v9"), ["+49.9 Elo over stall-v0, needs +50"])

    def test_it_must_have_beaten_v0_in_live_matches(self):
        self.assertEqual(self.league(vs_v0=(15.0, 30)).guard("stall-v9"), ["hasn't beaten stall-v0: 15 of 30 live"])
        self.assertEqual(self.league(vs_v0=(15.5, 30)).guard("stall-v9"), [])

    def test_an_llms_policy_needs_a_person(self):
        self.assertEqual(self.league(author="llm:model-x").guard("stall-v9"),
                         ["an LLM wrote it and no person has approved it"])
        self.assertEqual(self.league(author="llm:model-x", approved_by="rh").guard("stall-v9"), [])

    def test_replay_matches_never_count_toward_the_30(self):
        league = self.league(live=0, gap=0, vs_v0=(0.0, 0))
        league.rating = {}
        for i in range(100):
            league.add(match(i, "stall-v9", "stall-v0", "a", mode="replay"))
        for i in range(100, 129):
            league.add(match(i, "stall-v9", "stall-v0", "a"))
        self.assertEqual(league.live["stall-v9"], 29)
        self.assertEqual(league.guard("stall-v9"), ["29 live match(es), needs 30"])
        league.add(match(129, "stall-v9", "stall-v0", "a"))
        self.assertEqual(league.guard("stall-v9"), [])

    def test_the_champion_is_replayed_from_the_log_after_each_batch(self):
        """v1 wins every live match against v0, three a day: its 30th match is on day 10, where it's promoted."""
        log = [match(i, "stall-v1", "stall-v0", "a", at=f"2026-10-{1 + i // 3:02d}T00:00:00Z") for i in range(45)]
        league = A.standings(log, self.S)
        self.assertEqual(league.champion, "stall-v1")
        self.assertEqual(league.promotions, [("2026-10-10T00:00:00Z", "stall-v0", "stall-v1")])
        again = A.standings(list(reversed(log)), self.S)
        self.assertEqual((again.champion, again.promotions, again.rating),
                         (league.champion, league.promotions, league.rating))

    def test_with_no_log_v0_is_champion(self):
        league = A.standings([], self.S)
        self.assertEqual((league.champion, league.promotions), ("stall-v0", []))

    def test_live_matches_keep_each_pairs_record_both_ways_round(self):
        league = A.League(self.S)
        league.add(match(0, "stall-v1", "stall-v0", "a"))
        league.add(match(1, "stall-v1", "stall-v0", "draw"))
        league.add(match(2, "stall-v0", "stall-v1", "a"))
        league.add(match(3, "stall-v1", "stall-v0", "a", mode="replay"))
        self.assertEqual(league.h2h, {("stall-v1", "stall-v0"): (1.5, 3), ("stall-v0", "stall-v1"): (1.5, 3)})

    def test_the_luck_test_blocks_a_winning_record_luck_could_give(self):
        """20 of 30 beats v0, but it's only 2.82 times likelier from a policy 200 Elo better than from one no better:
        one challenger needs 20 (1 / 5%)."""
        self.assertEqual(self.league(s=self.LUCK).guard("stall-v9"),
                         ["20 of 30 live against stall-v0 could be luck: likelihood ratio 2.82, needs 20"])
        self.assertEqual(self.league(vs_v0=(24.0, 30), s=self.LUCK).guard("stall-v9"), [])

    def test_the_luck_bar_is_split_over_the_challengers(self):
        self.assertEqual(self.league(vs_v0=(22.0, 30), s=self.LUCK).guard("stall-v9"), [])
        two = self.league(vs_v0=(22.0, 30), s=self.LUCK)
        two.policies["stall-v8"] = A.Policy("stall-v8", "stall", 8, "rh", A.stall_v0, "2026-01-01T00:00:00Z")
        self.assertEqual(two.guard("stall-v9"),
                         ["22 of 30 live against stall-v0 could be luck: likelihood ratio 28.2, needs 40"])

    def test_a_no_better_policy_reaches_the_luck_bar_at_most_5_percent_of_the_time(self):
        """Ville's inequality, checked: 1,000 coin-flip records of 600 matches, the ratio checked after every match."""
        rng, bar, hits = random.Random(1), math.log(20), 0
        for _ in range(1000):
            points = 0
            for n in range(1, 601):
                points += rng.random() < 0.5
                if A.log_likelihood_ratio(points, n, 200) >= bar:
                    hits += 1
                    break
        self.assertLessEqual(hits, 50)
        self.assertEqual(A.log_likelihood_ratio(0, 0, 200), 0.0)
        self.assertLess(A.log_likelihood_ratio(0.5, 1, 200), 0)  # a draw is evidence of no gain


class Verdicts(unittest.TestCase):
    """04 §9's verdict rules. Between the fixture's R1 and R2, checkout resumed on 2026-09-12 (S_c4 removed), search
    on 2026-09-10 and payments on 2026-09-20. As built, no rule fires for any of them; each test adds what makes one."""
    S = A.settings({})

    def setUp(self):
        self.db = K.build()

    def fired(self, team):
        x = r1_stalls(self.db)[team]
        t2 = A.resumed(self.db, x, K.R2[1])
        return t2, A.verdict(self.db, x, t2, K.R2[1], self.S)

    def test_the_fixture_resumes_three_stalls_and_no_rule_fires(self):
        self.assertEqual({t: self.fired(t) for t in ("checkout", "search", "payments")},
                         {"checkout": ("2026-09-12T00:00:00Z", {}), "search": ("2026-09-10T00:00:00Z", {}),
                          "payments": ("2026-09-20T00:00:00Z", {})})
        self.assertIsNone(A.resumed(self.db, r1_stalls(self.db)["billing"], K.R2[1]))

    def test_a_blocking_site_removed_up_to_7_days_before_is_an_upstream_block(self):
        self.db.execute("INSERT INTO blocked_by VALUES ('R2', 'S_c1', 'S_shared', 'acme/shop:shared/client.py:f', 1, "
                        "'resolved')")
        remove_site(self.db, "S_shared", "2026-09-05T00:00:00Z")
        self.assertEqual(self.fired("checkout")[1], {1: ("block_removed", [("S_shared", "2026-09-05T00:00:00Z")])})

    def test_8_days_before_is_too_early(self):
        self.db.execute("INSERT INTO blocked_by VALUES ('R2', 'S_c1', 'S_shared', 'acme/shop:shared/client.py:f', 1, "
                        "'resolved')")
        remove_site(self.db, "S_shared", "2026-09-04T00:00:00Z")
        self.assertEqual(self.fired("checkout")[1], {})

    def test_a_ticket_created_up_to_14_days_before_is_never_notified(self):
        add_ticket(self.db, "CHK-400", "2026-08-29T00:00:00Z", "S_c1")
        self.assertEqual(self.fired("checkout")[1], {2: ("ticket_created", [("CHK-400", "2026-08-29T00:00:00Z")])})

    def test_15_days_before_is_too_early(self):
        add_ticket(self.db, "CHK-400", "2026-08-28T00:00:00Z", "S_c1")
        self.assertEqual(self.fired("checkout")[1], {})

    def test_a_ticket_reopened_during_the_stall_is_a_ticket_mismatch(self):
        self.db.execute("INSERT INTO ticket_event VALUES ('R2', 'PAY-10', '2026-09-18T00:00:00Z', 'status', 'Closed', "
                        "'Reopened', 'pia@acme.test', 0)")
        self.assertEqual(self.fired("payments")[1], {3: ("ticket_reopened", [("PAY-10", "2026-09-18T00:00:00Z")])})

    def test_a_change_by_someone_other_than_the_files_top_author_is_an_orphaned_owner(self):
        resumed_by(self.db, "S_c4", "sam@acme.test")
        _, fired = self.fired("checkout")
        self.assertEqual([(n, rule) for n, (rule, _) in fired.items()], [(4, "new_author")])

    def test_the_files_top_author_resuming_isnt(self):
        top = self.db.execute("SELECT a.email FROM call_site s JOIN file_owner f ON f.path = s.path "
                              "JOIN person_alias a ON a.person_id = f.top_person_id "
                              "WHERE s.call_site_id = 'S_c4'").fetchone()[0]
        resumed_by(self.db, "S_c4", top)
        self.assertEqual(self.fired("checkout")[1], {})


class StallJob(unittest.TestCase):
    """One run of the stall job at R2, after R1. checkout's stall resumed with a new author: 4 Orphaned owner."""
    S = A.settings({})

    def setUp(self):
        self.db = build()
        resumed_by(self.db, "S_c4", "sam@acme.test")
        self.policies = {"stall-v0": early("stall-v0"), "stall-v1": early("stall-v1"),
                         "stall-v8": A.Policy("stall-v8", "stall", 8, "rh", stall_v8, "2026-01-01T00:00:00Z")}

    def run_job(self, log=(), policies=None):
        return A.stall_job(self.db, CFG, self.S, "R2", K.R2[1], K.R1[1], list(log), policies or self.policies)

    def test_one_verdict_makes_a_live_match_for_every_pair_of_policies(self):
        job = self.run_job()
        [o] = [o for o in job.outcomes if o.reason]
        self.assertEqual((o.stall.team, o.reason, o.rule), ("checkout", 4, "new_author"))
        # v0 and v1 both put 4 first; v8 puts it last
        self.assertEqual([(m["policy_a"], m["policy_b"], m["result"]) for m in job.matches],
                         [("stall-v0", "stall-v1", "draw"), ("stall-v0", "stall-v8", "a"),
                          ("stall-v1", "stall-v8", "a")])
        m = job.matches[0]
        self.assertEqual((m["task_id"], m["mode"], m["verdict_source"], m["decided_by"], m["decided_at"]),
                         (o.stall.task_id, "live", "rule", "new_author", K.R2[1]))
        self.assertEqual(m["match_id"], A.h(o.stall.task_id, "live", "stall-v0", "stall-v1", K.R2[1]))

    def test_the_others_wait_for_a_person(self):
        job = self.run_job()
        self.assertEqual(sorted((o.stall.team, o.resumed, o.reason) for o in job.outcomes),
                         [("checkout", "2026-09-12T00:00:00Z", 4), ("payments", "2026-09-20T00:00:00Z", None),
                          ("search", "2026-09-10T00:00:00Z", None)])

    def test_two_reasons_wait_for_a_person_too(self):
        add_ticket(self.db, "CHK-400", "2026-09-05T00:00:00Z", "S_c1")
        job = self.run_job()
        [o] = [o for o in job.outcomes if o.stall.team == "checkout"]
        self.assertEqual((sorted(o.fired), o.reason, o.matches, job.matches), ([2, 4], None, [], []))

    def test_a_ticket_made_during_the_stall_is_scored_against_the_ranking_served(self):
        """search was served with 2 Never notified first. A ticket made after R1 and before it resumed is the
        verdict; reading it into search's R1 facts would have turned 2 off and made every match a draw."""
        add_ticket(self.db, "SRCH-1", "2026-09-05T00:00:00Z", "S_s2")  # search's site left at R1
        [o] = [o for o in self.run_job().outcomes if o.stall.team == "search"]
        self.assertEqual((o.reason, sorted(o.stall.facts)), (2, [2, 7]))
        self.assertIn(("stall-v0", "stall-v8", "a"), [(m["policy_a"], m["policy_b"], m["result"]) for m in o.matches])

    def test_eps_drops_to_01_at_the_jobs_30th_live_match(self):
        for n, eps in ((29, 0.2), (30, 0.1)):
            job = self.run_job(log=[match(i, "stall-v0", "stall-v1", "draw", at=K.R1[1]) for i in range(n)])
            self.assertEqual((job.live, job.eps), (n, eps))

    def test_a_task_is_decided_once(self):
        first = self.run_job()
        again = self.run_job(log=first.matches)
        self.assertEqual(again.matches, [])
        self.assertNotIn("checkout", {o.stall.team for o in again.outcomes})

    def test_no_live_match_for_policies_written_after_the_stall_was_served(self):
        job = self.run_job(policies=A.POLICIES)  # written 2026-11-07, after R1
        [o] = [o for o in job.outcomes if o.reason]
        self.assertEqual((o.reason, o.matches, job.matches), (4, [], []))

    def test_each_stall_now_is_served_by_the_seeded_pick(self):
        job = self.run_job()
        self.assertEqual(len(job.open), 4)
        for x in job.open:
            self.assertEqual(job.served[x.task_id], A.serve("R2", x.task_id, "stall-v0", ["stall-v1", "stall-v8"], 0.2))

    def test_the_rows_written_load_into_schema_sql(self):
        job = self.run_job()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(A.write_tracking(Path(d), K.R2[1], self.policies, [], [], job.matches), (3, 3))
            rows = {t: [json.loads(line) for line in (Path(d) / f"{t}.jsonl").read_text().splitlines()]
                    for t in ("policy", "match")}
            self.assertNotIn(b"\r", (Path(d) / "match.jsonl").read_bytes())  # the same bytes on Windows
        db = K.build()
        db.execute("PRAGMA foreign_keys = ON")
        for table, data in rows.items():
            cols = list(data[0])
            db.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [[r[c] for c in cols] for r in data])
        self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(db.execute("SELECT count(*) FROM match").fetchone()[0], 3)
        self.assertEqual([r["match_id"] for r in rows["match"]], sorted(r["match_id"] for r in rows["match"]))

    def test_nothing_from_after_the_snapshot_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(A.write_tracking(Path(d), K.R2[1], A.POLICIES, [], [], []), (0, 0))


class Replay(unittest.TestCase):
    """04 §9's replay: past stalls, rows known by then only, every policy in the code, half K, never toward
    promotion. checkout's stall at R1 resumed on Sep 12 with someone new: 4 Orphaned owner."""
    S = A.settings({})

    def setUp(self):
        self.db = build()
        resumed_by(self.db, "S_c4", "sam@acme.test")
        self.v8 = A.Policy("stall-v8", "stall", 8, "rh", stall_v8, "2026-11-08T00:00:00Z")
        self.policies = {**A.POLICIES, "stall-v8": self.v8}

    def replay(self, log=(), policies=None, at=K.R2[1], run_id="R2"):
        return A.replay_job(self.db, CFG, self.S, run_id, at, list(log), policies or self.policies)

    def test_policies_written_after_the_runs_are_tried_on_them(self):
        """The policies date from November: no live match at R2, but replay matches on checkout's stall."""
        self.assertEqual(A.stall_job(self.db, CFG, self.S, "R2", K.R2[1], K.R1[1], [], self.policies).matches, [])
        rep = self.replay()
        self.assertEqual(rep.pairs, [(K.R1[1], K.R2[1])])
        self.assertEqual([(m["policy_a"], m["policy_b"], m["result"]) for m in rep.matches],
                         [("stall-v0", "stall-v1", "draw"), ("stall-v0", "stall-v8", "a"),
                          ("stall-v1", "stall-v8", "a")])  # v0 and v1 put 4 first, v8 last
        [o] = [o for o in rep.outcomes[K.R1[1], K.R2[1]] if o.reason]
        m = rep.matches[0]
        self.assertEqual((m["mode"], m["verdict_source"], m["decided_by"], m["decided_at"], m["task_id"]),
                         ("replay", "rule", "new_author", K.R2[1], o.stall.task_id))
        self.assertEqual(m["match_id"], A.h(o.stall.task_id, "replay", "stall-v0", "stall-v1", K.R2[1]))

    def test_a_replay_match_moves_elo_half_as_far_and_never_toward_promotion(self):
        rep = self.replay()
        self.assertEqual(A.elo(rep.matches), A.elo([dict(m, mode="live") for m in rep.matches], k=16, k_after=8))
        league = A.standings(rep.matches, self.S, self.policies)
        self.assertEqual((league.live, league.champion), ({}, "stall-v0"))
        self.assertEqual(league.guard("stall-v1")[0], "0 live match(es), needs 30")

    def test_a_pair_plays_a_stall_once(self):
        """v0 and v1, written before R1, play checkout's stall live. v8, written later, plays each of them in replay,
        and a second replay adds nothing."""
        policies = {"stall-v0": early("stall-v0"), "stall-v1": early("stall-v1"), "stall-v8": self.v8}
        live = A.stall_job(self.db, CFG, self.S, "R2", K.R2[1], K.R1[1], [], policies).matches
        self.assertEqual([(m["policy_a"], m["policy_b"], m["mode"]) for m in live], [("stall-v0", "stall-v1", "live")])
        rep = self.replay(live, policies)
        self.assertEqual([(m["policy_a"], m["policy_b"], m["mode"]) for m in rep.matches],
                         [("stall-v0", "stall-v8", "replay"), ("stall-v1", "stall-v8", "replay")])
        self.assertEqual(self.replay(live + rep.matches, policies).matches, [])

    def test_a_verdict_on_a_reason_unknown_when_it_was_served_makes_no_match(self):
        """K.build()'s R1 has no file_owner: 4 Orphaned owner was unknown when checkout's stall was served, so no
        policy ranked it, and a draw would only be noise."""
        db = K.build()
        resumed_by(db, "S_c4", "sam@acme.test")
        rep = A.replay_job(db, CFG, self.S, "R2", K.R2[1], [], self.policies)
        [o] = [o for o in rep.outcomes[K.R1[1], K.R2[1]] if o.reason]
        self.assertEqual((o.reason, o.unknown, o.matches, rep.matches), (4, (1, 4, 7, 8), [], []))
        self.assertIn("4 Orphaned owner: unknown when it was served, so no policy ranked it: no match",
                      A.describe_outcome(o))

    def test_every_pair_of_runs_is_replayed_oldest_first(self):
        """A third run, R3 on Nov 1, knows billing removed its only site on Oct 20: billing's stall, open at R1 and
        R2, ended between R2 and R3."""
        r3 = ("R3", "2026-11-01T00:00:00Z")
        add_run(self.db, r3)
        remove_site(self.db, "S_b1", "2026-10-20T00:00:00Z", r3)
        rep = self.replay(at=r3[1], run_id="R3")
        self.assertEqual(rep.pairs, [(K.R1[1], K.R2[1]), (K.R2[1], r3[1])])
        self.assertEqual([(o.stall.team, o.resumed) for o in rep.outcomes[K.R2[1], r3[1]]],
                         [("billing", "2026-10-20T00:00:00Z")])
        self.assertEqual(len(rep.matches), 3)  # checkout's, from the first pair

    def test_a_replay_match_is_dated_when_its_played(self):
        """At R3, not at the first pair's R2: dated in the past, it would land before live matches decided since and
        change the ratings and promotions they gave. It goes through --write like a live row."""
        r3 = ("R3", "2026-11-10T00:00:00Z")  # after the policies were written, so --write keeps their rows
        add_run(self.db, r3)
        live = [match(1, "stall-v0", "stall-v1", "a", at=K.R2[1])]
        rep = self.replay(live, at=r3[1], run_id="R3")
        self.assertEqual((len(rep.matches), {m["decided_at"] for m in rep.matches}), (3, {r3[1]}))
        history = [m for m in live + rep.matches if m["decided_at"] < r3[1]]
        self.assertEqual(A.standings(history, self.S, self.policies).rating,
                         A.standings(live, self.S, self.policies).rating)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(A.write_tracking(Path(d), r3[1], self.policies, [], live, rep.matches), (4, 4))
            rows = [json.loads(line) for line in (Path(d) / "match.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sorted((m["mode"], m["decided_at"]) for m in rows),
                         [("live", K.R2[1])] + [("replay", r3[1])] * 3)


class Explore(unittest.TestCase):
    """The explore job (04 §9): v0 nominates unfiled legacy code; team-draft interleaving; more accepts wins."""
    S = A.settings({})

    def legacy(self):
        """The fixture with two more deprecated functions in its call graph: cart/totals.py total, called 3 times,
        and cart/pricing.py price, once."""
        db = K.build()
        db.executemany("INSERT INTO deprecation VALUES ('R2', 'acme/shop', ?, ?, ?, ?, 'DeprecationWarning')",
                       [("cart/totals.py", 50, "total", "cart.totals.total"),
                        ("cart/pricing.py", 200, "price", "cart.pricing.price")])
        db.executemany("INSERT INTO call_edge VALUES ('R2', 'acme/shop', 'web/report.py', 'summary', ?, 4, "
                       "'cart/totals.py', 'acme/shop', 'total', ?)", [(4, "e" * 40), (9, "e" * 40)])
        return db

    def test_the_symbol_meridian_tracks_is_filed_so_v0_nominates_nothing_on_the_fixture(self):
        """KNOWLEDGE-BASE.md's radar finds verify_token itself, and its call sites resolve to it: it's tracked."""
        self.assertEqual(A.nominations(K.build(), K.R2[1]), ([], 1))

    def test_v0_nominates_deprecated_functions_with_callers_the_most_called_first(self):
        job = A.explore_job(self.legacy(), CFG, self.S, "R2", K.R2[1], [])
        [task] = job.tasks
        self.assertEqual((task.task_id, task.repo, task.shown, job.filed),
                         (A.h("explore", K.REPO, K.R2[1]), K.REPO, ["explore-v0"], 1))
        self.assertEqual([(x.qualname, x.callers, p) for x, p in task.served],
                         [("cart.totals.total", 3, "explore-v0"), ("cart.pricing.price", 1, "explore-v0")])

    def test_at_most_explore_budget_a_repo_and_run(self):
        job = A.explore_job(self.legacy(), CFG, {**self.S, "explore_budget": 1}, "R2", K.R2[1], [])
        self.assertEqual([x.scope for x, _ in job.tasks[0].served], ["total"])

    def test_a_deprecation_known_later_isnt_nominated_earlier(self):
        """The radar's tables are R2's: as of R1 nothing of them is known."""
        self.assertEqual(A.nominations(self.legacy(), K.R1[1]), ([], 0))

    def test_with_a_challenger_the_radar_interleaves_both_lists(self):
        least = A.Policy("explore-v1", "explore", 1, "rh", lambda found: sorted(found, key=lambda x: x.callers),
                         "2026-11-09T00:00:00Z")
        job = A.explore_job(self.legacy(), CFG, self.S, "R2", K.R2[1], [], {**A.POLICIES, "explore-v1": least})
        [task] = job.tasks
        self.assertEqual(task.shown, ["explore-v0", "explore-v1"])
        self.assertEqual({x.scope: p for x, p in task.served}, {"total": "explore-v0", "price": "explore-v1"})

    def test_team_draft_credits_each_pick_keeps_each_order_and_stays_even(self):
        a, b = list("ABCDEFGH"), list("CAHBGDFE")
        starts = set()
        for i in range(200):
            seed = A.h("R2", f"t{i}")
            shown = A.team_draft(seed, {"explore-v0": a, "explore-v1": b}, 8)
            self.assertEqual(shown, A.team_draft(seed, {"explore-v1": b, "explore-v0": a}, 8))  # seeded
            self.assertEqual(sorted(x for x, _ in shown), a)  # each item once
            for p, ranking in (("explore-v0", a), ("explore-v1", b)):
                mine = [x for x, q in shown if q == p]
                self.assertEqual(mine, sorted(mine, key=ranking.index))  # each side picks in its own order
            for k in range(1, len(shown) + 1):
                n = Counter(q for _, q in shown[:k])
                self.assertLessEqual(abs(n["explore-v0"] - n["explore-v1"]), 1)
            starts.add(shown[0][1])
        self.assertEqual(starts, {"explore-v0", "explore-v1"})  # the coin picks who starts
        self.assertEqual(len(A.team_draft(A.h("x"), {"explore-v0": a, "explore-v1": b}, 3)), 3)

    def test_more_accepts_wins_and_no_reply_within_14_days_is_no_match(self):
        def nom(scope):
            return A.Nomination(K.REPO, f"{scope}.py", scope, scope, "DeprecationWarning", 1)

        def replies(*rows):
            return {(K.REPO, f"{scope}.py", scope): (answer, at) for scope, answer, at in rows}

        task = A.ExploreTask("t1", K.REPO, ["explore-v0", "explore-v1"],
                             [(nom("a"), "explore-v0"), (nom("b"), "explore-v1"), (nom("c"), "explore-v0")])
        offered, soon, late = K.R2[1], "2026-10-03T00:00:00Z", "2026-10-16T00:00:00Z"
        m = A.explore_match(task, replies(("a", "accept", soon), ("b", "accept", soon),
                                          ("c", "accept", "2026-10-15T00:00:00Z")), offered, self.S)
        self.assertEqual((m["policy_a"], m["policy_b"], m["result"], m["mode"], m["decided_at"]),
                         ("explore-v0", "explore-v1", "a", "live", "2026-10-15T00:00:00Z"))
        self.assertEqual(m["match_id"], A.h("t1", "live", "explore-v0", "explore-v1", "2026-10-15T00:00:00Z"))
        self.assertEqual(A.explore_match(task, replies(("b", "accept", soon)), offered, self.S)["result"], "b")
        self.assertEqual(A.explore_match(task, replies(("a", "reject", soon)), offered, self.S)["result"], "draw")
        self.assertIsNone(A.explore_match(task, {}, offered, self.S))
        self.assertIsNone(A.explore_match(task, replies(("a", "accept", late)), offered, self.S))  # day 15
        self.assertIsNone(A.explore_match(task._replace(shown=["explore-v0"]), replies(("a", "accept", soon)),
                                          offered, self.S))


class RanksRight(unittest.TestCase):
    """pass-bars.md §4 'Ranks right': 3 stall policies 200 Elo apart (the better one wins 76% of head-to-heads), all
    ranking every stall in shadow, K 32 then 16. After 200 matches, Elo orders all three right in 20 of 20 seeds."""
    SKILLS = {"stall-v0": 1500, "stall-v1": 1700, "stall-v2": 1300}

    def test_the_simulation_wins_76_percent_at_200_apart(self):
        log = shadow({"hi": 1700, "lo": 1500}, 20_000, seed=0)
        share = sum(m["result"] == "a" for m in log) / len(log)  # "hi" sorts first: it's policy_a
        self.assertAlmostEqual(share, 1 / (1 + 10 ** (-200 / 400)), delta=0.01)

    def test_elo_orders_all_three_right_in_20_of_20_seeds(self):
        wrong = [seed for seed in range(1, 21)
                 if order(A.elo(shadow(self.SKILLS, 200, seed))[0]) != ["stall-v1", "stall-v0", "stall-v2"]]
        self.assertEqual(wrong, [])


class Simulations(unittest.TestCase):
    """meridian/auditor_sim.py, the code behind decisions 9–11, on a few seeds (the decisions run 2,000)."""

    def test_in_the_stall_job_eps_never_moves_promotion(self):
        for seed in range(1, 21):
            a = S.eps_run(seed, S.settings(**S.EPS["0.2 then 0.1"]))
            b = S.eps_run(seed, S.settings(**S.EPS["0.1"]))
            self.assertEqual(a[:2], b[:2], seed)

    def test_v1_is_promoted_after_its_30th_live_match(self):
        firsts = [S.eps_run(seed, S.settings())[0] for seed in range(1, 21)]
        self.assertTrue(all(f is None or f >= 45 for f in firsts), firsts)  # 15 stalls give each policy 30
        self.assertGreater(sum(f is not None for f in firsts), 10)

    def test_replay_rows_come_first_and_a_wrong_verdict_is_a_coin_flip(self):
        replay, live = S.replay_rows(1, 0.3)
        self.assertEqual(([m["mode"] for m in replay], [m["mode"] for m in live]), (["replay"] * 100, ["live"] * 60))
        flips = [m for seed in range(1, 41) for m in S.replay_rows(seed, 1.0)[0] if m["policy_a"] == "stall-v1"]
        self.assertAlmostEqual(sum(m["result"] == "a" for m in flips) / len(flips), 0.5, delta=0.08)

    def test_each_decision_runs_and_picks_one_of_its_options(self):
        for n, options in ((9, S.EPS), (10, S.K), (11, S.REPLAY)):
            decide, pick, _ = S.DECIDE[n]
            self.assertIn(pick(decide(range(1, 6))), options, n)
        res = S.decide_12(range(1, 4), stalls=30)
        self.assertEqual(set(res), set(S.GUARDS))
        self.assertIn(S.pick_12(res), [*S.GUARDS, None])

    def test_decisions_9_to_11_run_under_04s_guard_whatever_the_auditors_default(self):
        self.assertEqual({k: S.settings()[k] for k in S.GUARD_04}, {"promote_matches": 30, "promote_elo": 50,
                                                                    "promote_luck": None})

    def test_decision_12s_rule(self):
        r = lambda same, best, at, worst=0.0: {"same_201": same, "same_600": same, "best_promoted": best,
                                               "best_at": at, "worst_first": worst}
        res = {"30, +50": r(0.96, 1.0, 45), "90, +150": r(0.04, 0.97, 100),
               "30, +50, luck test G 200": r(0.02, 1.0, 99), "30, +50, luck test G 100": r(0.01, 1.0, 90)}
        self.assertEqual(S.pick_12(res), "30, +50, luck test G 100")    # the fastest of those in
        res["90, +150"] = r(0.04, 0.97, 93)
        self.assertEqual(S.pick_12(res), "90, +150")                    # within one stall: bigger numbers first
        self.assertEqual([S.out_12(r(0.051, 1.0, 50)), S.out_12(r(0.05, 0.94, 50)),
                          S.out_12(r(0.05, 1.0, 50, worst=0.001)), S.out_12(r(0.05, 0.95, 50))], [1, 2, 2, None])
        self.assertIsNone(S.pick_12({"30, +50": r(0.96, 1.0, 45)}))     # every option out: 04 §9's guard stays

    def test_the_command_prints_each_decisions_table(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(S.main(["--decide", "9", "10", "--seeds", "3", "--no-log"]), 0)
        self.assertIn("decision 9, seeds 1–3", out.getvalue())
        self.assertIn("| 24 flat |", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(S.main(["--decide", "12", "--seeds", "2", "--no-log"]), 0)
        self.assertIn("decision 12, seeds 1–2", out.getvalue())
        self.assertIn("| 30, +50, luck test G 200 |", out.getvalue())
        self.assertIn("Not part of the rule, same skill: promoted by luck within 3,000 matches: 30, +50 ",
                      out.getvalue())


class Command(unittest.TestCase):
    def run_on(self, *argv, matches=(), src=None):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "block.sqlite"
            src, dst = src or K.build(), sqlite3.connect(path)
            src.executemany("INSERT INTO match VALUES (" + ", ".join("?" * 9) + ")",
                            [[m[c] for c in A.MATCH_COLUMNS] for m in matches])
            src.commit()
            src.backup(dst)
            dst.close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = A.main(["--run-id", "R2", "--db", str(path), *argv])
            return code, out.getvalue()

    def test_every_stall_with_v0s_reasons_and_no_live_matches_said_so(self):
        code, out = self.run_on()
        self.assertEqual(code, 0)
        self.assertIn("run R2 as of 2026-10-01T00:00:00Z: 4 stalled team(s) in 1 tracked repo and symbol pair(s)", out)
        self.assertIn("acme/shop verify_token · fulfillment: 264 days, 1 left · stall-v0: 1 Upstream block (1)", out)
        self.assertIn("checkout: 19 days, 3 left · stall-v0: 4 Orphaned owner (1), 5 Semantics unclear (1), "
                      "7 Test gap (1), 8 Competing load (1)", out)
        self.assertIn("Elo from 0 match(es) in the log, 0 live: no live verdicts yet", out)
        self.assertRegex(out, r"stall-v0 +1500\.0  0 match\(es\)")
        self.assertIn("stall job: champion stall-v0, ε 0.2 (0 live match(es) so far)", out)
        self.assertRegex(out, r"fulfillment: .* · served: stall-v[01]\n")
        self.assertIn("stall-v1 stays a challenger: 0 live match(es), needs 30; +0.0 Elo over stall-v0, needs +50; "
                      "hasn't beaten stall-v0: 0 of 0 live", out)
        self.assertIn("since the last run (2026-09-01T00:00:00Z): 3 stall(s) ended, 0 new match(es)", out)
        self.assertIn("checkout, stalled since 2026-01-10T00:00:00Z: resumed 2026-09-12T00:00:00Z · no rule fired: "
                      "a person picks", out)

    def test_unknown_reasons_replay_and_explore_are_said(self):
        _, out = self.run_on()
        self.assertIn("as of 2026-09-01T00:00:00Z, reasons 1 Upstream block, 4 Orphaned owner, 7 Test gap, "
                      "8 Competing load are unknown: no run by then wrote blocked_by, file_owner, module_import, "
                      "person_alias, team_map", out)
        self.assertIn("replay over 1 pair(s) of runs, every policy in the code: 3 stall(s) ended, 0 new replay "
                      "match(es), each at half K and never toward promotion", out)
        self.assertIn("explore job: champion explore-v0, 0 unfiled legacy candidate(s) as of 2026-10-01T00:00:00Z "
                      "(deprecation-marked, with callers), 1 more already tracked, so filed; 0 shown", out)
        self.assertIn("explore matches need accept or reject replies on the radar, and nothing records them yet "
                      "(no users): none", out)

    def test_a_replay_match_is_reported_and_rated(self):
        db = build()
        resumed_by(db, "S_c4", "sam@acme.test")
        _, out = self.run_on(src=db)
        self.assertIn("since the last run (2026-09-01T00:00:00Z): 3 stall(s) ended, 0 new match(es)", out)
        self.assertIn("1 new replay match(es)", out)
        self.assertIn("checkout, stalled since 2026-01-10T00:00:00Z: resumed 2026-09-12T00:00:00Z · new_author → "
                      "4 Orphaned owner: 1 match(es)", out)
        self.assertIn("Elo from 0 match(es) in the log and 1 new, 0 live: no live verdicts yet", out)
        self.assertRegex(out, r"stall-v0 +1500\.0  1 match\(es\)")

    def test_write_leaves_out_policies_newer_than_the_block(self):
        with tempfile.TemporaryDirectory() as d:
            _, out = self.run_on("--write", "--out", d)
            self.assertEqual([(Path(d) / f).read_text() for f in ("policy.jsonl", "match.jsonl")], ["", ""])
        self.assertIn("policy.jsonl (0 row(s)) and match.jsonl (0): explore-v0, stall-v0, stall-v1 came after this "
                      "block's snapshot, so they're left out", out)

    def test_elo_reads_only_matches_decided_by_then(self):
        log = [match(0, "stall-v0", "stall-v1", "a", at="2026-09-20T00:00:00Z"),
               match(1, "stall-v0", "stall-v1", "b", at="2026-10-05T00:00:00Z")]  # after R2's snapshot
        _, out = self.run_on(matches=log)
        self.assertIn("Elo from 1 match(es) in the log, 1 live\n", out)
        self.assertRegex(out, r"stall-v0 +1516\.0  1 match\(es\)")
        self.assertRegex(out, r"stall-v1 +1484\.0  1 match\(es\)")

    def test_no_block_stops(self):
        with self.assertRaises(SystemExit):
            A.main(["--run-id", "nope", "--db", str(Path(tempfile.gettempdir()) / "no-such-block.sqlite")])


if __name__ == "__main__":
    unittest.main()
