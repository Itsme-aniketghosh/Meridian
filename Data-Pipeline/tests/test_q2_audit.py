"""labels/q2_audit.py (GAPS.md gap 6, decision 11): nothing naming a person is sent, the cache makes a rerun free,
and the summary follows the rule written before the run. A fake LLM, never a real one."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from meridian.llm_cache import Cache

HERE = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("q2_audit", HERE / "labels" / "q2_audit.py")
A = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(A)


class Fake:
    def __init__(self, verdict="no"):
        self.verdict, self.calls = verdict, 0

    def __call__(self, model, prompt):
        self.calls += 1
        assert prompt.startswith(A.SYSTEM)
        return {"reply": json.dumps({"verdict": self.verdict, "reason": "r"}), "tokens_in": 1000, "tokens_out": 50,
                "cost_usd": 0.005}


def pair(sha, bucket="Q2", fix="f1"):
    return {"bucket": bucket, "sha": sha, "fix_sha": fix, "path": "a.scala",
            "facts": {"ticket": "SPARK-1", "path": "a.scala", "fix_message": sha + fix, "fix_diff": "",
                      "blamed_message": "", "blamed_diff": ""}}


class Scrub(unittest.TestCase):
    def test_lines_naming_a_person_emails_and_handles_go(self):
        text = ("[SPARK-1] Fix it\n\ncc @cloud-fan for review\nCloses #31443 from someone/improve.\n\n"
                "Authored-by: A Person <a@databricks.com>\nSigned-off-by: B <b@apache.org>\nmail c.d@x.org")
        out = A.scrub(text)
        for gone in ("Person", "databricks", "apache.org", "someone", "cloud-fan", "c.d@x.org"):
            self.assertNotIn(gone, out)
        self.assertIn("Closes #31443", out)
        self.assertIn("[SPARK-1] Fix it", out)

    def test_long_diffs_are_capped(self):
        self.assertIn("more lines", A.capped("\n".join(["x"] * 300)))
        self.assertIn("more characters", A.capped("y" * 9000))


class Parse(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(A.parse('{"verdict": "no", "reason": "moved"}'), ("no", "moved"))
        self.assertEqual(A.parse('Sure: {"verdict": "yes", "reason": "x"}')[0], "yes")
        self.assertEqual(A.parse("I think so")[0], "unclear")
        self.assertEqual(A.parse('{"verdict": "maybe"}')[0], "unclear")


class Audit(unittest.TestCase):
    def test_a_warm_cache_makes_0_calls(self):
        with tempfile.TemporaryDirectory() as d:
            llm, cache = Fake(), Cache(d)
            pairs = [pair("a"), pair("b")]
            first = A.audit(pairs, llm, "m", cache)
            again = A.audit(pairs, Fake(), "m", cache, cache_only=True)
        self.assertEqual(llm.calls, 2)
        self.assertEqual([a["verdict"] for a in first], [a["verdict"] for a in again])
        self.assertEqual(sum(a["cost_usd"] for a in again), 0.0)

    def test_a_miss_with_no_llm_stops(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(LookupError):
            A.audit([pair("a")], None, "m", Cache(d))

    def test_a_commit_holds_if_any_fix_says_yes(self):
        v = A.per_commit([{"sha": "a", "verdict": "no"}, {"sha": "a", "verdict": "yes"},
                          {"sha": "b", "verdict": "unclear"}, {"sha": "b", "verdict": "no"}])
        self.assertEqual(v, {"a": "yes", "b": "no"})


def answers(q2_no, q2_yes, q4_no, q4_yes):
    rows, i = [], 0
    for bucket, no, yes in (("Q2", q2_no, q2_yes), ("Q4", q4_no, q4_yes)):
        for verdict, n in (("no", no), ("yes", yes)):
            for _ in range(n):
                rows.append({"bucket": bucket, "sha": f"s{i}", "fix_sha": "f", "verdict": verdict, "reason": "",
                             "cost_usd": 0.0})
                i += 1
    return rows


class Rule(unittest.TestCase):
    def hand(self, ans, agree):
        """20 hand verdicts on Q2 commits: the first `agree` match the model, the rest don't."""
        q2 = [a for a in ans if a["bucket"] == "Q2"][:20]
        flip = {"yes": "no", "no": "yes"}
        return [{"sha": a["sha"], "human_verdict": a["verdict"] if i < agree else flip[a["verdict"]]}
                for i, a in enumerate(q2)]

    def test_the_model_is_used_at_15_of_20_not_14(self):
        ans = answers(30, 21, 10, 40)
        self.assertTrue(A.summary(ans, self.hand(ans, 15), "m")["model_used"])
        self.assertFalse(A.summary(ans, self.hand(ans, 14), "m")["model_used"])

    def test_noisier_only_when_the_intervals_separate(self):
        ans = answers(35, 16, 10, 40)
        r = A.summary(ans, self.hand(ans, 20), "m")
        self.assertTrue(r["q2_noisier_than_q4"])
        ans = answers(15, 36, 12, 38)
        self.assertFalse(A.summary(ans, self.hand(ans, 20), "m")["q2_noisier_than_q4"])

    def test_no_hand_check_means_the_model_isnt_used(self):
        r = A.summary(answers(30, 21, 10, 40), [], "m")
        self.assertFalse(r["model_used"])
        self.assertEqual(r["hand"]["judged"], 0)

    def test_every_share_has_its_n_and_interval(self):
        r = A.summary(answers(30, 21, 10, 40), [], "m")
        self.assertEqual((r["Q2"]["no"], r["Q2"]["n"]), (30, 51))
        self.assertEqual(len(r["Q2"]["ci"]), 2)


if __name__ == "__main__":
    unittest.main()
