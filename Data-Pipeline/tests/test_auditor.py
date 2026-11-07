"""The auditor (04 §9): Elo, against hand calculations and Model-Pipeline/packets/rochan-tracking/pass-bars.md,
section 4.

    python -m unittest tests.test_auditor -v      # from Data-Pipeline/

Elo is checked by hand, and on simulated policies of known skill that all rank every stall in shadow, as the stall
job runs.
"""
import itertools
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian import auditor as A


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


if __name__ == "__main__":
    unittest.main()
