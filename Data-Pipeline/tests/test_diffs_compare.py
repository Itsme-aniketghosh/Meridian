"""The diff drafter's model comparison (meridian/diffs_compare.py, GAPS.md gap 2): its table and the pick rule.

    python -m unittest tests.test_diffs_compare -v      # from Data-Pipeline/
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian import diffs_compare as C  # noqa: E402


def call(fh, source, hit=False, usd=0.001, ms=900.0):
    return {"facts_hash": fh, "source": source, "cache_hit": hit, "cost_usd": 0.0 if hit else usd,
            "latency_ms": ms}


class Table(unittest.TestCase):
    def test_a_row_counts_lines_drafts_matches_and_paid_cost(self):
        calls = [call("a", "llm"), call("b", None), call("c", "llm", hit=True)]
        r = C.row("gemini-flash", calls, matched=1)
        self.assertEqual((r["tried"], r["drafted"], r["matched"], r["pass_rate"]), (3, 2, 1, 33.3))
        self.assertEqual((r["paid_calls"], r["usd"], r["usd_per_match"]), (2, 0.002, 0.002))

    def test_the_cheapest_within_2_points_of_the_best_wins(self):
        t = {"gemini-pro": {"pass_rate": 75.0, "usd_per_match": 0.010},
             "gemini-flash": {"pass_rate": 74.0, "usd_per_match": 0.002},     # 1 point behind, cheaper: wins
             "gemma-4": {"pass_rate": 50.0, "usd_per_match": 0.0001}}         # cheapest, but too far behind
        self.assertEqual(C.pick(t), "gemini-flash")

    def test_ties_go_to_the_open_weights_model_and_nothing_matched_never_wins(self):
        t = {"gemini-flash": {"pass_rate": 50.0, "usd_per_match": 0.002},
             "llama-4-scout": {"pass_rate": 50.0, "usd_per_match": 0.002}}
        self.assertEqual(C.pick(t), "llama-4-scout")
        t = {"gemini-flash": {"pass_rate": 0.0, "usd_per_match": None},
             "gemma-4": {"pass_rate": 0.0, "usd_per_match": None}}
        self.assertEqual(C.pick(t), "gemma-4")                              # all tied at nothing: open weights
        self.assertIsNone(C.pick({}))
        self.assertEqual(C.pick({"llama-4-scout": {"error": "HTTP 404"},
                                 "gemini-flash": {"pass_rate": 10.0, "usd_per_match": 0.01}}), "gemini-flash")


if __name__ == "__main__":
    unittest.main()
