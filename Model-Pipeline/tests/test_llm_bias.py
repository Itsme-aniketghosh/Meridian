"""llm_bias.py (Chaitali): PASS-BARS §6.4. Fallbacks are counted per text from logged calls, never from templates
with no LLM attempt; slices under 30 are listed, not judged; the rule is bias.py's."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from model import llm_bias

B = {"slices": [], "min_n": 30, "max_gap": 0.10, "power_gap": 0.15, "pool_below": 30, "baseline": "x",
     "accepted": []}
DIFFS = pd.read_csv(llm_bias.DIFFS)


def calls(rows):
    """llm_calls rows from (agent, facts_hash, repo, team, task, source) tuples."""
    return pd.DataFrame(rows, columns=list(llm_bias.CALL_KEYS))


def team_texts(team, n, fell_back, start=0):
    """n texts for one team, each one attempt; fell_back of them end on the template."""
    return [("writer", f"{team}{i}", "acme/shop", team, "rewrite", "template" if i < fell_back else "llm")
            for i in range(start, start + n)]


class Texts(unittest.TestCase):
    def test_one_text_per_facts_hash_and_a_later_pass_is_no_fallback(self):
        t = llm_bias.texts(calls([("writer", "h1", "r", "a", "rewrite", None),       # small model: gate miss
                                  ("writer", "h1", "r", "a", "rewrite", "llm"),      # mid model: passes
                                  ("writer", "h2", "r", "a", "rewrite", None),
                                  ("writer", "h2", "r", "a", "rewrite", "template")]))
        self.assertEqual(dict(zip(t["facts_hash"], t["fell_back"])), {"h1": False, "h2": True})

    def test_jev_is_no_text(self):
        t = llm_bias.texts(calls([("jev", "h1", "r", "a", "rewrite", None)]))
        self.assertEqual(len(t), 0)

    def test_the_llm_off_is_no_fallback(self):
        rep = llm_bias.build(None, DIFFS, B)
        self.assertEqual(rep["fallback"]["texts"], 0)
        self.assertIn("no LLM texts", rep["fallback"]["note"])
        self.assertEqual([f for f in rep["flags"] if f["rate"] == "fallback"], [])

    def test_a_missing_column_is_refused(self):
        with self.assertRaises(ValueError):
            llm_bias.texts(pd.DataFrame({"agent": ["writer"]}))


class Rule(unittest.TestCase):
    def test_a_team_that_always_falls_back_is_flagged(self):
        rows = team_texts("payments", 40, 40) + team_texts("checkout", 200, 10)
        rep = llm_bias.build(calls(rows), DIFFS, B)
        team = rep["fallback"]["by"]["team"]["groups"]
        self.assertTrue(team["payments"]["flagged"])
        self.assertFalse(team["checkout"]["flagged"])
        self.assertIn({"rate": "fallback", "slice": "team=payments"},
                      [{"rate": f["rate"], "slice": f["slice"]} for f in rep["flags"]])

    def test_29_texts_are_listed_not_judged(self):
        rows = team_texts("payments", 29, 29) + team_texts("checkout", 200, 10)
        g = llm_bias.build(calls(rows), DIFFS, B)["fallback"]["by"]["team"]["groups"]["payments"]
        self.assertEqual((g["n"], g["judged"], g["flagged"]), (29, False, False))

    def test_a_fair_team_is_not_flagged(self):
        rows = team_texts("payments", 100, 5) + team_texts("checkout", 100, 5)
        team = llm_bias.build(calls(rows), DIFFS, B)["fallback"]["by"]["team"]["groups"]
        self.assertFalse(any(g["flagged"] for g in team.values()))

    def test_match_is_worse_lower(self):
        d = pd.DataFrame({"task": ["url", "url"], "repo": ["a", "b"], "matched": [90, 10], "lines": [100, 100],
                          "style": ["x", "x"], "source": ["s", "s"]})
        repo = llm_bias.build(None, d, B)["match"]["by"]["repo"]["groups"]
        self.assertTrue(repo["b"]["flagged"])
        self.assertFalse(repo["a"]["flagged"])


class Ship(unittest.TestCase):
    def test_the_bar_judges_the_shipped_model_and_lists_the_others(self):
        good = calls([("writer", f"h{i}", "r", "a", "rewrite", "llm") for i in range(40)]).assign(model="flash")
        bad = calls([("writer", f"h{i}", "r", "a", "rewrite", "template") for i in range(40)]).assign(model="gemma")
        rep = llm_bias.build(pd.concat([good, bad]), DIFFS, B, ship="flash")
        self.assertEqual((rep["ship"], rep["fallback"]["texts"]), ("flash", 40))
        self.assertEqual(rep["fallback"]["by"]["task"]["overall"]["k"], 0)
        self.assertEqual(rep["others"]["gemma"]["by"]["task"]["overall"]["k"], 40)

    def test_a_shipped_model_with_no_calls_is_refused(self):
        good = calls([("writer", "h", "r", "a", "rewrite", "llm")]).assign(model="flash")
        with self.assertRaises(ValueError):
            llm_bias.build(good, DIFFS, B, ship="pro")


class Report(unittest.TestCase):
    def test_the_published_counts(self):
        rep = llm_bias.build(None, DIFFS, B)
        m = rep["match"]["by"]["repo"]
        self.assertEqual((m["overall"]["k"], m["overall"]["n"]), (254 + 612, 267 + 1236))
        self.assertFalse(m["groups"]["debug-toolbar"]["judged"])  # 29 lines
        self.assertEqual(sorted(f["slice"] for f in rep["flags"]), ["repo=(other)", "repo=oscar"])
        self.assertIn("not judged", rep["match"]["team"])

    def test_accepted_flags_pass(self):
        acc = ["match:repo=oscar", "match:repo=(other)"]
        self.assertFalse(llm_bias.build(None, DIFFS, B)["pass"])
        rep = llm_bias.build(None, DIFFS, B, acc)
        self.assertEqual((rep["pass"], rep["open_flags"]), (True, 0))

    def test_every_slice_has_its_n(self):
        rep = llm_bias.build(calls(team_texts("payments", 40, 3)), DIFFS, B)
        for r in ("fallback", "match"):
            for dim, s in rep[r]["by"].items():
                for g, e in s["groups"].items():
                    with self.subTest(rate=r, slice=f"{dim}={g}"):
                        self.assertIn("n", e)
                        self.assertIn("judged", e)

    def test_the_command_writes_the_same_bytes_twice(self):
        with tempfile.TemporaryDirectory() as d:
            texts = []
            for _ in range(2):
                llm_bias.main(["--no-calls", "--no-charts", "--out", d])
                texts.append((Path(d) / "llm_bias.json").read_text(encoding="utf-8"))
        self.assertEqual(texts[0], texts[1])
        self.assertTrue(json.loads(texts[0])["pass"])  # the repo's [llm_bias] accepted


if __name__ == "__main__":
    unittest.main()
