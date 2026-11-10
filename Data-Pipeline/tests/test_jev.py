"""Jev, the router (meridian/jev.py), on KNOWLEDGE-BASE.md's fixture (tests/test_kb.py).

    python -m unittest tests.test_jev -v      # from Data-Pipeline/

No test calls a real LLM: `Fake` plays Jev. The packet's list (rachna-models): same problem twice, same pick; Jev down,
rules pick; a pick outside the table is refused; over the spend cap, rules pick; a warm cache makes 0 calls. Plus the
climb, the top-model budget, the gate on Jev's reason, and the hand-off (packets/README.md, Nov 9): Jev's answers go
through fact_gate.check and model.tracking.log_llm_call(..., facts_hash=) with the LLM off.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import agents as G
from meridian import jev as J
from meridian.common import CONFIG, load_config
from meridian.llm_cache import Cache

CFG = load_config(CONFIG)
AT = K.R2[1]


def fixture():
    db = K.build()
    return G.tasks(db, CFG, "R2", AT), *G.names(db)


class Fake:
    """Jev as a function of (task kind, facts), counting its calls."""

    def __init__(self, answer, cost=0.001):
        self.answer, self.cost, self.calls = answer, cost, 0

    def __call__(self, model, prompt):
        kind = prompt.split("Task kind: ", 1)[1].split("\n", 1)[0]
        facts = json.loads(prompt.split("Facts:\n", 1)[1])
        self.calls += 1
        reply = self.answer(kind, facts)
        return {"reply": reply if isinstance(reply, str) else json.dumps(reply), "tokens_in": 600,
                "tokens_out": 30, "cost_usd": self.cost}


def mid(kind, facts):
    return {"agent": J.TASK_AGENT[kind], "model": G.MID, "reason": "Several facts to keep straight."}


class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks, cls.teams, cls.people = fixture()

    def test_the_fixture_has_every_writer_task(self):
        self.assertEqual({t.kind for t in self.tasks}, {"rewrite", "ticket_draft", "stall_reason", "consumer"})

    def test_jev_down_rules_pick_the_smallest_model(self):
        router = J.Router(teams=self.teams, people=self.people)
        for t in self.tasks:
            p = router.pick(t)
            self.assertEqual((p.agent, p.rung, p.model, p.route, p.why), ("writer", 0, G.SMALL, "rules", "jev off"))

    def test_every_pick_is_in_the_table(self):
        router = J.Router(llm=Fake(mid), teams=self.teams, people=self.people)
        for t in self.tasks:
            p = router.pick(t)
            self.assertIn(p.model, J.TABLE[p.agent]["ladder"])
            self.assertEqual(p.agent, J.TASK_AGENT[t.kind])

    def test_an_agent_with_no_ladder_gets_no_model(self):
        """Until the diff drafter's ladder is in, Jev routes nothing to it: no model, so the codemod or no diff."""
        with mock.patch.dict(J.TABLE, {"diff_drafter": {"ladder": (), "retry_on": ("no_apply",)}}):
            p = J.rules_pick("diff")
            self.assertEqual((p.agent, p.model, p.route), ("diff_drafter", None, "rules"))
            self.assertIsNone(J.Router().next(p, "no_apply"))


class WithJev(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks, cls.teams, cls.people = fixture()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Cache(self.tmp.name)

    def router(self, llm, **kw):
        return J.Router(llm=llm, cache=self.cache, teams=self.teams, people=self.people, **kw)

    def test_a_good_pick_is_used(self):
        p = self.router(Fake(mid)).pick(self.tasks[0])
        self.assertEqual((p.model, p.rung, p.route, p.why), (G.MID, 1, "jev", "jev"))

    def test_same_problem_twice_same_pick_and_a_warm_cache_makes_0_calls(self):
        fake = Fake(mid)
        first = [self.router(fake).pick(t) for t in self.tasks]
        calls = fake.calls
        self.assertEqual(calls, len(self.tasks))
        again = [self.router(None).pick(t) for t in self.tasks]          # LLM off: the cache answers
        self.assertEqual(first, again)
        self.assertEqual(fake.calls, calls)

    def test_a_pick_outside_the_table_is_refused(self):
        cases = {"another agent": lambda k, f: {"agent": "diff_drafter", "model": G.SMALL, "reason": "Code."},
                 "a model off the ladder": lambda k, f: {"agent": "writer", "model": "gpt-9", "reason": "Big."},
                 "not JSON": lambda k, f: "use the big one"}
        for name, answer in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                router = J.Router(llm=Fake(answer), cache=Cache(d), teams=self.teams, people=self.people)
                p = router.pick(self.tasks[0])
                self.assertEqual((p.model, p.route), (G.SMALL, "rules"))
                self.assertTrue(p.why.startswith("refused"), p.why)

    def test_a_reason_that_says_a_fact_jev_wasnt_given_is_refused(self):
        def made_up(kind, facts):
            return {"agent": "writer", "model": G.MID, "reason": "The search team has 412 sites in cart/x.py."}
        p = self.router(Fake(made_up)).pick(self.tasks[0])
        self.assertEqual((p.model, p.route), (G.SMALL, "rules"))
        self.assertIn("not in the facts", p.why)

    def test_a_reply_naming_a_person_never_reaches_the_cache(self):
        def names(kind, facts):
            return {"agent": "writer", "model": G.MID, "reason": f"Ask {self.people[0]} first."}
        self.router(Fake(names)).pick(self.tasks[0])
        for f in Path(self.tmp.name).glob("*.json"):
            self.assertNotIn(self.people[0], f.read_text())

    def test_over_the_spend_cap_rules_pick_and_the_run_completes(self):
        fake = Fake(mid, cost=0.5)
        router = self.router(fake, spend_cap_usd=1.0)
        picks = [router.pick(t) for t in self.tasks]
        self.assertEqual(fake.calls, 2)                               # 0.5 + 0.5 reaches the cap
        self.assertEqual({p.why for p in picks[2:]}, {"over the spend cap"})
        self.assertEqual(len(picks), len(self.tasks))

    def test_top_model_budget(self):
        def top(kind, facts):
            return {"agent": "writer", "model": G.LARGE, "reason": "Long text."}
        router = self.router(Fake(top), top_budget=3)
        picks = [router.pick(t) for t in self.tasks]
        self.assertEqual(sum(p.model == G.LARGE for p in picks), 3)
        self.assertTrue(all(p.why == "top-model budget spent" for p in picks[3:]))


class Climb(unittest.TestCase):
    def test_a_retried_failure_climbs_one_model_then_stops(self):
        router = J.Router()
        p = J.rules_pick("rewrite")
        up = router.next(p, "bad_json")
        self.assertEqual((up.model, up.rung, up.route), (G.MID, 1, "rules"))
        top = router.next(up, "gate_miss")
        self.assertEqual(top.model, G.LARGE)
        self.assertIsNone(router.next(top, "gate_miss"))              # top failed: the template ships

    def test_a_failure_04_doesnt_retry_on_stops(self):
        self.assertIsNone(J.Router().next(J.rules_pick("rewrite"), "no_apply"))

    def test_climbing_counts_against_the_top_budget(self):
        router = J.Router(top_budget=0)
        self.assertIsNone(router.next(J.Pick("writer", 1, G.MID, "rules", "x"), "gate_miss"))

    def test_ladder_from_the_pick_up(self):
        models, route = J.Router().ladder(fixture()[0][0])
        self.assertEqual((models, route), (tuple(G.LADDER), "rules"))

    def test_the_prompt_version_covers_the_table(self):
        before = J.prompt_version()
        with mock.patch.dict(J.TABLE, {"writer": {"ladder": (G.SMALL,), "retry_on": ()}}):
            self.assertNotEqual(J.prompt_version(), before)


class Logging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks, cls.teams, cls.people = fixture()

    def test_a_rules_pick_without_a_jev_answer_is_not_a_call(self):
        calls = []
        router = J.Router(log=lambda **kw: calls.append(kw), teams=self.teams, people=self.people)
        [router.pick(t) for t in self.tasks]
        self.assertEqual(calls, [])

    def test_every_answer_is_logged_once_with_its_facts_hash(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            router = J.Router(llm=Fake(mid), cache=Cache(d), log=lambda **kw: calls.append(kw), teams=self.teams,
                              people=self.people)
            [router.pick(t) for t in self.tasks]
        self.assertEqual(len(calls), len(self.tasks))
        self.assertEqual([c["facts_hash"] for c in calls], [G.facts_hash(t.facts) for t in self.tasks])
        self.assertTrue(all(c["agent"] == "jev" and c["route"] == "jev" and c["gate_pass"] is True for c in calls))
        self.assertTrue(all(c["team"] == t.owner_team and c["task"] == t.kind for c, t in zip(calls, self.tasks)))


# tracking imports mlflow only when a run starts, so look for mlflow itself (as tests/test_agents.py does)
tracking = None
if importlib.util.find_spec("mlflow"):
    sys.path.append(str(Path(__file__).resolve().parents[2] / "Model-Pipeline"))
    from model import tracking


@unittest.skipIf(tracking is None, "needs mlflow (Model-Pipeline/requirements.txt); CI's model job runs it")
class HandOff(unittest.TestCase):
    """packets/README.md, Nov 9: Jev's answers go through fact_gate.check and tracking.log_llm_call(..., facts_hash=)
    on the fixture, with the LLM off: they come from a warm cache, so they're calls with 0 cost."""

    def test_jev_logs_to_meridian_agents_with_the_llm_off(self):
        tasks, teams, people = fixture()
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
        self.addCleanup(tmp.cleanup)
        cache = Cache(Path(tmp.name) / "cache")
        warm = J.Router(llm=Fake(mid), cache=cache, teams=teams, people=people)
        picks = [warm.pick(t) for t in tasks]                           # warm it, logging nothing
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": f"sqlite:///{tmp.name}/mlflow.db"}):
            with tracking.start_run(J.AGENT, owner="rachna", experiment=tracking.AGENTS, block_run_id="R2") as r:
                router = J.Router(cache=cache, log=tracking.log_llm_call, teams=teams, people=people)
                again = [router.pick(t) for t in tasks]
            table = tracking.load_llm_calls([r.info.run_id])
        self.assertEqual(again, picks)
        self.assertEqual(len(table), len(tasks))
        self.assertEqual(set(table["facts_hash"]), {G.facts_hash(t.facts) for t in tasks})
        self.assertTrue(table["cache_hit"].all())
        self.assertEqual(set(table["route"]), {"jev"})
        self.assertEqual(float(table["cost_usd"].sum()), 0.0)


if __name__ == "__main__":
    unittest.main()
