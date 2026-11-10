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
        for text in ("Ask Ann.", "Mail ann@corp.example.", "cc @ann"):
            self.assertFalse(ok(text), text)
            self.assertFalse(ok(text, kinds="numbers"), text)

    def test_facts_that_name_a_person_are_refused(self):
        for facts in ({"author": "Ann"}, {"email": "ann@corp.example"}, {"note": "asked Bob Lee"}):
            with self.assertRaises(ValueError):
                ok("x", facts)

    def test_teams_and_people_are_required(self):
        # left out, a swapped team or a name would pass unseen (Contract 5)
        with self.assertRaises(TypeError):
            check("payments owns it.", CHK_311)
        with self.assertRaises(TypeError):
            gate("payments owns it.", CHK_311, "T")


class Bypasses(unittest.TestCase):
    """The review's probes on PR #28: each one passed before the fix."""
    F = {"key": "CHK-311", "team": "checkout", "bug_fixes": 4, "share": 0.596, "reach": 0.712,
         "site": {"path": "cart/pricing.py", "line": 214}, "sha": corpus.SHA}

    def test_a_percent_is_a_share_never_a_count(self):
        for bad in ("4%", "400%", "1%", "0.7%"):
            self.assertFalse(ok(f"checkout is {bad} done", self.F), bad)
        for good in ("59.6%", "60%", "71%"):
            self.assertTrue(ok(f"checkout is {good} done", self.F), good)

    def test_counts_in_words(self):
        self.assertTrue(ok("four bug fixes", self.F))
        self.assertFalse(ok("five bug fixes", self.F))
        self.assertFalse(ok("Two calls", self.F))
        self.assertTrue(ok("no one has touched it", self.F))  # "one" is a pronoun, not checked

    def test_numbers_with_units_and_signs(self):
        for bad in ("stalled for 14d", "3x the calls", "5k rows", "500ms", "-4 bug fixes"):
            self.assertFalse(ok(bad, self.F), bad)
        self.assertTrue(ok("4x the calls", self.F))
        self.assertFalse(ok("2-10 commits", self.F))  # both ends of a range are read

    def test_sha_any_case(self):
        for bad in ("commit DEADBEEF12", "commit deadbee"):
            self.assertFalse(ok(bad, self.F), bad)
        self.assertTrue(ok(f"commit {corpus.SHA[:7].upper()}", self.F))

    def test_more_paths(self):
        for bad in ("see cart/discount.rs", "see cart/tax.tsx", "see app/models/user.erb", "see /cart/discount.py"):
            self.assertFalse(ok(bad, self.F), bad)
        for good in ("see ./cart/pricing.py:214", "see /cart/pricing.py"):
            self.assertTrue(ok(good, self.F), good)

    def test_team_plural_and_singular(self):
        self.assertFalse(ok("the payment team owns it", self.F))
        self.assertTrue(ok("the checkouts are done", self.F))


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


class TeamRule(unittest.TestCase):
    """Decision 11: a team name right after an article is a word, unless team, 's, owns or own follows."""

    def test_a_team_name_after_an_article_is_a_word(self):
        for text in ("Keep the same error handling across the platform.", "Run a search first.",
                     "Use this checkout flow."):
            self.assertTrue(ok(text), text)

    def test_still_a_team_when_it_names_one(self):
        for text in ("Ask the payments team.", "the platform's client", "the search team owns it",
                     "Done: platform, 2 of 2.", "Ask payments to review.", "payments and billing",
                     "Platform owns it.", "the platform’s client", "the payments and checkout teams"):
            self.assertFalse(ok(text), text)

    def test_strict_checks_every_team_name(self):
        self.assertFalse(ok("across the platform", team_rule="strict"))

    def test_known_limit_article_hides_a_swapped_team_in_other_words(self):
        # written down in decisions.md: "ask the payments folks" names a team but reads as a word
        self.assertTrue(ok("Ask the payments folks."))

    def test_every_seed_meets_decision_11s_bar(self):
        # 100% caught on every seed 0-9, mean false rejects at most 2.5%, no seed over 5%
        rejects = []
        for seed in range(10):
            clean, injected = corpus.build(seed)
            s = corpus.score(clean, injected)
            self.assertEqual(s["caught"], 200, (seed, s["by_kind"]))
            self.assertLessEqual(s["false_rejects"], 10, seed)
            rejects.append(s["false_rejects"])
        self.assertLessEqual(sum(rejects), 50, rejects)


class RealBlockNames(unittest.TestCase):
    """Names from the real block (review of PR #48): teams that are words, symbols that look like teams."""
    T = ["test", "tests", "urls", "checkout", "billing"]

    def ok(self, text, facts):
        return check(text, facts, teams=self.T, people=[]).passed

    def test_an_exact_team_name_wins_over_another_teams_plural(self):
        self.assertTrue(self.ok("Tests, 3 call sites remaining.", {"team": "tests", "left": 3}))

    def test_text_quoting_a_fact_word_for_word_is_known(self):
        self.assertTrue(self.ok("Migrate off url.", {"symbol": "url"}))
        self.assertTrue(self.ok("- Test gap (1 piece of evidence)", {"reason": "Test gap", "n": 1}))
        self.assertTrue(self.ok("billing migrates checkout.verify", {"team": "billing", "symbol": "checkout.verify"}))
        self.assertFalse(self.ok("Ask checkout.", {"symbol": "checkout.verify"}))  # the bare team is still a team

    def test_a_fact_path_with_any_extension_is_a_path(self):
        f = {"sites": "tests/i18n/commands/code.sample:1, :4"}
        self.assertTrue(self.ok("tests/i18n/commands/code.sample:1, :4", {"s": [
            {"path": "tests/i18n/commands/code.sample", "line": 1},
            {"path": "tests/i18n/commands/code.sample", "line": 4}]}))
        self.assertTrue(self.ok("tests/i18n/commands/code.sample:1", f))
        self.assertFalse(self.ok("tests/i18n/commands/code.sample:5", f))


class Gate(unittest.TestCase):
    def test_a_pass_ships_the_llm_text(self):
        text, source, result = gate("CHK-311: 4 bug fixes.", CHK_311, "TEMPLATE", teams=TEAMS, people=corpus.PEOPLE)
        self.assertEqual((text, source, result.passed), ("CHK-311: 4 bug fixes.", "llm", True))

    def test_a_miss_ships_the_whole_template_never_a_stripped_text(self):
        text, source, result = gate("CHK-311 has 5 bug fixes. Keep the handling.", CHK_311,
                                    lambda f: f"{f['facts']['key']}: see the call sites.",
                                    teams=TEAMS, people=corpus.PEOPLE)
        self.assertEqual((text, source), ("CHK-311: see the call sites.", "template"))
        self.assertEqual([(m.kind, m.token) for m in result.misses], [("number", "5")])

    def test_no_text_ships_the_template(self):
        self.assertEqual(gate(None, CHK_311, "T", teams=TEAMS, people=())[:2], ("T", "template"))

    def test_contract_5_object_or_the_inner_facts(self):
        self.assertEqual(ok("CHK-311 at cart/pricing.py:214"), ok("CHK-311 at cart/pricing.py:214", CHK_311["facts"]))

    def test_same_input_same_result(self):
        t = "CHK-311 has 5 bug fixes at cart/pricing.py:215, ask payments."
        self.assertEqual(ok(t), ok(t))
        self.assertEqual(check(t, CHK_311, teams=TEAMS, people=corpus.PEOPLE).misses,
                         check(t, CHK_311, teams=TEAMS, people=corpus.PEOPLE).misses)

    def test_fallback_rate(self):
        self.assertEqual(fallback_rate(["llm", "template", "llm", "llm"]), {"texts": 4, "fallbacks": 1, "rate": 0.25})
        self.assertEqual(fallback_rate([]), {"texts": 0, "fallbacks": 0, "rate": 0.0})

    def test_unknown_settings_stop(self):
        with self.assertRaises(ValueError):
            check("x", {}, teams=(), people=(), kinds="everything")
        with self.assertRaises(ValueError):
            check("x", {}, teams=(), people=(), numbers="fuzzy")
        with self.assertRaises(ValueError):
            check("x", {}, teams=(), people=(), team_rule="loose")


if __name__ == "__main__":
    unittest.main()
