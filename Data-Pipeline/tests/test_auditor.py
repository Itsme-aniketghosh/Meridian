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
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import auditor as A
from meridian import auditor_sim as S
from meridian.common import CONFIG, load_config

CFG = load_config(CONFIG)


def fixture(at=K.R2[1]):
    return A.point(K.build(), at, K.REPO, K.SYMBOL, "R2")


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


def remove_site(db, site, on):
    """A new version of `site`: removed on `on`, known at R2."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(call_site)")]
    row = dict(zip(cols, db.execute("SELECT * FROM call_site WHERE call_site_id = ? ORDER BY known_at DESC",
                                    (site,)).fetchone()))
    row.update(run_id="R2", status="removed", effective_date=on, known_at=K.R2[1])
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
        db = K.build()
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
                                          "promote_matches": 30, "promote_elo": 50, "block_days": 7,
                                          "ticket_days": 14})
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

    def league(self, live=30, gap=50.0, vs_v0=(20.0, 30), author="rh", approved_by=""):
        new = A.Policy("stall-v9", "stall", 9, author, A.stall_v0, "2026-01-01T00:00:00Z", approved_by)
        league = A.League(self.S, {"stall-v0": A.POLICIES["stall-v0"], "stall-v9": new})
        league.rating = {"stall-v0": 1500.0, "stall-v9": 1500.0 + gap}
        league.live = {"stall-v0": live, "stall-v9": live}
        league.vs_v0 = {"stall-v9": vs_v0}
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
        self.db = K.build()
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

    def test_the_command_prints_each_decisions_table(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(S.main(["--decide", "9", "10", "--seeds", "3", "--no-log"]), 0)
        self.assertIn("decision 9, seeds 1–3", out.getvalue())
        self.assertIn("| 24 flat |", out.getvalue())


class Command(unittest.TestCase):
    def run_on(self, *argv, matches=()):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "block.sqlite"
            src, dst = K.build(), sqlite3.connect(path)
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

    def test_write_leaves_out_policies_newer_than_the_block(self):
        with tempfile.TemporaryDirectory() as d:
            _, out = self.run_on("--write", "--out", d)
            self.assertEqual([(Path(d) / f).read_text() for f in ("policy.jsonl", "match.jsonl")], ["", ""])
        self.assertIn("policy.jsonl (0 row(s)) and match.jsonl (0): stall-v0, stall-v1 came after this block's "
                      "snapshot, so they're left out", out)

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
