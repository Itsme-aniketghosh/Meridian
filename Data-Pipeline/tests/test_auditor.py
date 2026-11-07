"""The auditor (04 §9): policy v0 and Elo, against hand calculations and Model-Pipeline/packets/rochan-tracking/
pass-bars.md, section 4.

    python -m unittest tests.test_auditor -v      # from Data-Pipeline/

Policy v0 runs on KNOWLEDGE-BASE.md's fixture (tests/test_kb.py), whose stall reasons the doc already proves. Elo is
checked by hand, and on simulated policies of known skill that all rank every stall in shadow, as the stall job runs.
"""
import contextlib
import io
import itertools
import math
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import auditor as A
from meridian.common import CONFIG, load_config

CFG = load_config(CONFIG)


def fixture(at=K.R2[1]):
    return A.point(K.build(), at, K.REPO, K.SYMBOL, "R2")


def match(i, a, b, result, mode="live", at="2026-10-01T00:00:00Z"):
    return dict(match_id=f"m{i:05d}", task_id=f"t{i}", policy_a=a, policy_b=b, result=result, mode=mode,
                verdict_source="rule", decided_by="fixture", decided_at=at)


def shadow(skills, n, seed):
    """The first n matches of simulated stalls in which every policy ranks every stall in shadow (04 §9). Each stall
    draws one performance per policy, its skill plus Gumbel noise of scale 400 / ln 10. Then a policy beats another
    1 / (1 + 10^(-gap / 400)) of the time, the curve Elo assumes, and one stall's results agree with each other."""
    rng, scale, out = random.Random(seed), 400 / math.log(10), []
    while len(out) < n:
        perf = {p: s - scale * math.log(-math.log(rng.random() or 1e-12)) for p, s in sorted(skills.items())}
        for a, b in itertools.combinations(sorted(skills), 2):
            out.append(match(len(out), a, b, "a" if perf[a] > perf[b] else "b"))
    return out[:n]


def order(ratings):
    return sorted(ratings, key=lambda p: -ratings[p])


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
                                          "replay_k": 0.5})
        self.assertEqual(A.settings({"auditor": {"k": 24}})["k"], 24)
        with self.assertRaises(SystemExit):
            A.settings({"auditor": {"K": 24}})


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
