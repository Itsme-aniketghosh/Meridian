"""meridian/orchestrator.py: identify, push, finish on closed lists; anything off them is the rules plan; it ends.

No test calls a real LLM: test_agents' Fake answers from the stall's facts.
"""
import json
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from meridian import agents as G
from meridian import orchestrator as O
from meridian.llm_cache import Cache
from tests.test_agents import AT, CFG, K, Fake


def answer(problems, steps, reason="The team is waiting on work outside its control."):
    return lambda model, facts: json.dumps({"problems": problems(facts), "steps": steps, "reason": reason})


def first_reason(facts):
    return [r["reason"] for r in facts["reasons"]][:1]


def go(llm=None, cache=None, workers=1):
    """The orchestrator's run on the fixture, writer LLM off (templates): (plans, written kinds by team)."""
    db, plans = K.build(), []
    teams, people = G.names(db)
    orch = O.Orchestrator(llm=llm, cache=cache, teams=teams, people=people)
    result = G.run(db, CFG, "R2", AT, plan=lambda todo: orch.select(todo, plans, workers), workers=workers)
    by_team = {}
    for w in result.written:
        by_team.setdefault(w.task.owner_team, []).append(w.task.kind)
    return plans, by_team


class RulesPlan(unittest.TestCase):
    def test_llm_off_runs_every_available_step_for_stalled_teams_only(self):
        plans, by_team = go()
        self.assertEqual({p.team for p in plans}, {"billing", "checkout", "fulfillment", "platform"})
        self.assertTrue(all(p.route == "rules" and p.why == "llm off" for p in plans))
        self.assertNotIn("payments", by_team)              # not stalled: the orchestrator doesn't touch it
        self.assertEqual(sorted(by_team["billing"]), ["consumer", "stall_reason", "ticket_draft"])
        checkout = next(p for p in plans if p.team == "checkout")
        self.assertEqual(checkout.steps, ("stall_reason", "ticket_draft", "consumer", "rewrite"))  # MENU order
        self.assertEqual(checkout.problems, ("Orphaned owner", "Semantics unclear", "Test gap", "Competing load"))


class ModelPlan(unittest.TestCase):
    def test_a_good_plan_narrows_the_work_and_is_cached(self):
        with tempfile.TemporaryDirectory() as d:
            llm = Fake(answer(first_reason, ["stall_reason"]))
            plans, by_team = go(llm, Cache(d))
            self.assertEqual(len(llm.calls), 4)            # one planning call per stall, never more
            self.assertTrue(all(p.route == "orchestrator" and p.steps == ("stall_reason",) for p in plans))
            self.assertEqual({k for kinds in by_team.values() for k in kinds}, {"stall_reason"})
            again = Fake(answer(first_reason, ["stall_reason"]))
            replayed, _ = go(again, Cache(d))
            self.assertEqual(again.calls, [])              # warm cache: 0 calls, the same plans
            self.assertEqual(plans, replayed)

    def test_anything_off_the_lists_is_the_rules_plan(self):
        cases = {
            "a problem not among the stall's reasons": answer(lambda f: ["Made up"], ["stall_reason"]),
            "a problem twice": answer(lambda f: first_reason(f) * 2, ["stall_reason"]),
            "a step twice": answer(first_reason, ["stall_reason", "stall_reason"]),
            "a step off the menu": answer(first_reason, ["stall_reason", "deploy"]),
            "not starting with stall_reason": answer(first_reason, ["consumer", "stall_reason"]),
            "fifty steps": answer(first_reason, ["stall_reason"] + ["consumer"] * 49),
            "a number in the reason": answer(first_reason, ["stall_reason"], "Seven teams are waiting."),
            "not JSON": lambda model, facts: "sure, here is a plan",
        }
        for name, fn in cases.items():
            with self.subTest(name):
                plans, _ = go(Fake(fn))
                self.assertTrue(all(p.route == "rules" and p.why.startswith("refused") for p in plans), plans)
                self.assertTrue(all(len(p.steps) <= len(O.MENU) for p in plans))

    def test_a_step_the_team_has_no_task_for_is_refused(self):
        plans, _ = go(Fake(answer(first_reason, ["stall_reason", "rewrite"])))   # billing has no rewrite task
        billing = next(p for p in plans if p.team == "billing")
        self.assertEqual((billing.route, billing.why), ("rules", "refused: steps outside the menu or not available"))

    def test_workers_plan_the_same(self):
        llm = answer(first_reason, ["stall_reason", "consumer"])
        self.assertEqual(go(Fake(llm)), go(Fake(llm), workers=4))


class Walkers(unittest.TestCase):
    def written(self, walk):
        db, plans = K.build(), []
        teams, people = G.names(db)
        orch = O.Orchestrator(teams=teams, people=people)
        result = G.run(db, CFG, "R2", AT, plan=lambda todo: orch.select(todo, plans, walk=walk(db)))
        return plans, [w for w in result.written if w.task.kind == "walk"]

    def test_a_walk_is_one_more_task_for_that_stall_and_its_template_passes_the_gate(self):
        found = {"facts": {"team": "billing", "walked": ["billing"], "path": [], "found": {}},
                 "source": {"team": "stall:x"}}
        plans, walks = self.written(lambda db: lambda stall, mine: found if stall.owner_team == "billing" else None)
        self.assertEqual([p.team for p in plans if p.walk], ["billing"])
        self.assertEqual([(w.task.owner_team, w.source, w.template_misses) for w in walks],
                         [("billing", "template", ())])

    def test_the_context_builder_fails_and_the_walkers_find_the_cause_upstream(self):
        def down(facts):
            raise RuntimeError("context builder down")
        with mock.patch.object(O.context, "cut", down):
            plans, walks = self.written(lambda db: O.graph_walk(db, CFG, "R2", AT, {}))
        self.assertEqual(sum(p.walk for p in plans), len(plans))          # every stall's tool failed: all walked
        fulfillment = next(w for w in walks if w.task.owner_team == "fulfillment")
        self.assertEqual(fulfillment.task.facts["facts"]["found"]["platform"], {"Test gap": [["shared.client"]]})
        self.assertIn("platform: Test gap", fulfillment.text)
        self.assertTrue(all(w.template_misses == () for w in walks))      # the gate passes every walk template


class Bounds(unittest.TestCase):
    def test_a_plan_can_never_hold_more_than_the_menu(self):
        with self.assertRaises(AssertionError):
            O.Plan("t", (), ("stall_reason", "stall_reason"), False, "rules", "x")
        with self.assertRaises(AssertionError):
            O.Plan("t", (), O.MENU + ("extra",), False, "rules", "x")

    def test_no_reason_fired_means_walk_by_code_and_no_problems(self):
        stall = next(t for t in G.tasks(K.build(), CFG, "R2", AT) if t.kind == "stall_reason")
        empty = replace(stall, facts={**stall.facts, "facts": {**stall.facts["facts"], "reasons": []}})
        self.assertTrue(O.rules_plan(empty, ["stall_reason"]).walk)
        o = O.Orchestrator(llm=Fake(answer(lambda f: [], ["stall_reason"])))
        p = o.plan(empty, {"stall_reason"})
        self.assertEqual((p.route, p.problems, p.walk), ("orchestrator", (), True))   # the model can't turn it off
        o = O.Orchestrator(llm=Fake(answer(lambda f: ["Test gap"], ["stall_reason"])))
        self.assertTrue(o.plan(empty, {"stall_reason"}).why.startswith("refused"))       # nor invent a problem


if __name__ == "__main__":
    unittest.main()
