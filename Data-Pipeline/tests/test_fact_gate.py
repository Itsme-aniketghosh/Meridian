"""N: the fact gate. The 200 + 200 pass bar (05-test), and the rules it rests on.

    python -m unittest tests.test_fact_gate -v      # from Data-Pipeline/
    python -m tests.fact_gate_corpus                # decisions 1 and 2, every option
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian.fact_gate import check, fallback_rate, gate
from tests import fact_gate_corpus as corpus

TEAMS = corpus.TEAMS
CHK_311 = {"facts": {"key": "CHK-311", "team": "checkout",
                     "sites": [{"path": "cart/pricing.py", "line": 214, "bug_fixes": 4, "in_except": 0},
                               {"path": "cart/pricing.py", "line": 231, "bug_fixes": 4, "in_except": 1}]},
           "source": {"key": "ticket:CHK-311", "sites": "call_site:S_c1,S_c2"}}


def ok(text, facts=CHK_311, **kw):
    return check(text, facts, teams=TEAMS, people=corpus.PEOPLE, **kw).passed


class PassBar(unittest.TestCase):
    """05-test: 200 injected errors, 100% caught; 200 clean texts, at most 5% rejected."""

    @classmethod
    def setUpClass(cls):
        cls.clean, cls.injected = corpus.build()
        cls.score = corpus.score(cls.clean, cls.injected)

    def test_sizes(self):
        self.assertEqual((len(self.clean), len(self.injected)), (200, 200))
        self.assertEqual(len({t["text"] for t in self.clean}), 200)
        self.assertEqual({k: sum(t["kind"] == k for t in self.injected) for k in corpus.KINDS},
                         {k: 40 for k in corpus.KINDS})

    def test_every_injected_error_is_caught(self):
        self.assertEqual(self.score["caught"], 200, self.score["by_kind"])

    def test_at_most_5_percent_of_clean_texts_rejected(self):
        self.assertLessEqual(self.score["false_rejects"], 10, [t["text"] for t in self.score["rejected"]])

    def test_corpus_is_the_same_every_build(self):
        again, _ = corpus.build()
        self.assertEqual([t["text"] for t in again], [t["text"] for t in self.clean])


class Tokens(unittest.TestCase):
    def test_real_facts_pass(self):
        self.assertTrue(ok("CHK-311: checkout calls verify_token at cart/pricing.py:214 and :231, 4 bug fixes."))

    def test_line_after_a_path_belongs_to_that_path(self):
        self.assertTrue(ok("cart/pricing.py:214 and :231"))
        self.assertFalse(ok("cart/pricing.py:214 and :232"))

    def test_numbers_inside_paths_and_keys_are_not_read_as_numbers(self):
        # 214, 231 and 311 are only facts as a line and a key, never as counts
        self.assertTrue(ok("cart/pricing.py:214, CHK-311"))
        self.assertFalse(ok("It has 214 bug fixes."))

    def test_a_tail_path_must_be_unique(self):
        facts = {"a": "cart/util.py:3", "b": "pay/util.py:3"}
        self.assertTrue(ok("cart/util.py:3", facts))
        self.assertFalse(ok("util.py:3", facts))  # which util.py?
        self.assertTrue(ok("pricing.py:214"))

    def test_a_real_path_with_another_sites_line_fails(self):
        facts = {"s0": {"path": "a/x.py", "line": 1}, "s1": {"path": "a/y.py", "line": 2}}
        self.assertFalse(ok("a/x.py:2", facts))

    def test_ticket_keys_are_exact(self):
        self.assertFalse(ok("CHK-312"))
        self.assertFalse(ok("chk-311 and CHK-3110"))

    def test_sha_prefix(self):
        facts = {"sha": corpus.SHA}
        self.assertTrue(ok(f"commit {corpus.SHA[:7]}", facts))
        self.assertFalse(ok("commit 3f9a2c1e", facts))

    def test_dates(self):
        facts = {"last": "2026-07-01T09:00:00Z"}
        for good in ("2026-07-01", "Jul 1", "July 1, 2026", "in 2026"):
            self.assertTrue(ok(good, facts), good)
        for bad in ("2026-07-02", "Jul 2", "July 1, 2025"):
            self.assertFalse(ok(bad, facts), bad)

    def test_team_must_be_a_fact(self):
        self.assertTrue(ok("Checkout owns it."))
        self.assertFalse(ok("payments owns it."))

    def test_people_never_pass(self):
        facts = {"author": "Ann", "email": "ann@corp.example"}  # even if someone put them in the facts
        for text in ("Ask Ann.", "Mail ann@corp.example.", "cc @ann"):
            self.assertFalse(ok(text, facts), text)
            self.assertFalse(ok(text, facts, kinds="numbers"), text)


class Numbers(unittest.TestCase):
    F = {"rows": 1526, "share": 0.712}

    def test_exact(self):
        self.assertTrue(ok("1526 rows", self.F, numbers="exact"))
        self.assertFalse(ok("1,526 rows", self.F, numbers="exact"))

    def test_format(self):
        self.assertTrue(ok("1,526 rows", self.F, numbers="format"))
        self.assertTrue(ok("71.2%", self.F, numbers="format"))
        self.assertFalse(ok("71%", self.F, numbers="format"))

    def test_round(self):
        for good in ("71%", "71.2%", "0.71", "1,526"):
            self.assertTrue(ok(good, self.F, numbers="round"), good)
        for bad in ("72%", "70.9%", "1,527", "0.72"):
            self.assertFalse(ok(bad, self.F, numbers="round"), bad)

    def test_a_bare_whole_number_is_never_rounded(self):
        # 0.712 rounds to 1, but "1 call" must be a fact of its own
        self.assertFalse(ok("1 call", self.F, numbers="round"))
        self.assertFalse(ok("0 calls", self.F, numbers="round"))


class Gate(unittest.TestCase):
    def test_a_pass_ships_the_llm_text(self):
        text, source, result = gate("CHK-311: 4 bug fixes.", CHK_311, "TEMPLATE", teams=TEAMS)
        self.assertEqual((text, source, result.passed), ("CHK-311: 4 bug fixes.", "llm", True))

    def test_a_miss_ships_the_whole_template_never_a_stripped_text(self):
        text, source, result = gate("CHK-311 has 5 bug fixes. Keep the handling.", CHK_311,
                                    lambda f: f"{f['facts']['key']}: see the call sites.", teams=TEAMS)
        self.assertEqual((text, source), ("CHK-311: see the call sites.", "template"))
        self.assertEqual([(m.kind, m.token) for m in result.misses], [("number", "5")])

    def test_no_text_ships_the_template(self):
        self.assertEqual(gate(None, CHK_311, "T")[:2], ("T", "template"))

    def test_contract_5_object_or_the_inner_facts(self):
        self.assertEqual(ok("CHK-311 at cart/pricing.py:214"), ok("CHK-311 at cart/pricing.py:214", CHK_311["facts"]))

    def test_same_input_same_result(self):
        t = "CHK-311 has 5 bug fixes at cart/pricing.py:215, ask payments."
        self.assertEqual(check(t, CHK_311, teams=TEAMS), check(t, CHK_311, teams=TEAMS))

    def test_fallback_rate(self):
        self.assertEqual(fallback_rate(["llm", "template", "llm", "llm"]), {"texts": 4, "fallbacks": 1, "rate": 0.25})
        self.assertEqual(fallback_rate([]), {"texts": 0, "fallbacks": 0, "rate": 0.0})

    def test_unknown_settings_stop(self):
        with self.assertRaises(ValueError):
            check("x", {}, kinds="everything")
        with self.assertRaises(ValueError):
            check("x", {}, numbers="fuzzy")


if __name__ == "__main__":
    unittest.main()
