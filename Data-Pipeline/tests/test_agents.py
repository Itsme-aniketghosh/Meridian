"""The writer agent on KNOWLEDGE-BASE.md's fixture (tests/test_kb.py), against PASS-BARS §5.

    python -m unittest tests.test_agents -v      # from Data-Pipeline/

No test calls a real LLM: `Fake` plays one, so bad JSON, gate misses, retries and the cache are all checked offline.
The hand-off test (packets/README.md, Nov 9) runs the writer with the LLM off through model.tracking.log_llm_call.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import agents as G
from meridian.common import CONFIG, load_config
from meridian.fact_gate import check
from meridian.llm_cache import Cache, key

CFG = load_config(CONFIG)
AT = K.R2[1]
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")


class Fake:
    """An LLM that answers from a function of (model, task facts), and counts its calls."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, model, prompt):
        facts = json.loads(prompt.split("Facts:\n", 1)[1])
        self.calls.append(model)
        return {"reply": self.answer(model, facts), "tokens_in": 800, "tokens_out": 90, "cost_usd": 0.0004}


def good(model, facts):
    """A reply that only says facts: the team, plus the ticket key when there is one."""
    said = f"{facts.get('key', 'Work')} for {facts['team']}."
    return json.dumps({"text": said, "used": ["team"] + (["key"] if "key" in facts else [])})


def run(**kw):
    db = K.build()
    return G.run(db, CFG, "R2", AT, **kw), db


class Templates(unittest.TestCase):
    """LLM off: every task returns its template, and every template passes the gate (PASS-BARS §5, LLM off)."""

    @classmethod
    def setUpClass(cls):
        (cls.written, cls.rows), db = run()
        cls.teams, cls.people = G.names(db)

    def by(self, kind, target):
        return next(w for w in self.written if w.task.kind == kind and w.task.target == target)

    def test_every_task_ships_its_template_with_the_llm_off(self):
        self.assertEqual(G.fallback_counts(self.written),
                         {"consumer": (5, 5), "rewrite": (2, 2), "stall_reason": (4, 4), "ticket_draft": (3, 3)})
        for w in self.written:
            self.assertEqual((w.source, w.model, w.attempts), ("template", None, []))
            self.assertTrue(check(w.text, w.task.facts, teams=self.teams, people=self.people), w.text)

    def test_the_rewrite_is_02_products_after(self):
        text = self.by("rewrite", "CHK-311").text
        for part in ("CHK-311: Migrate cart/ off verify_token", "cart/pricing.py:214, :231 · owner checkout",
                     "Done when", "1 of the 2 calls sits inside an except block. Keep the same handling?"):
            self.assertIn(part, text)

    def test_the_consumer_text_is_02_products_and_fragility_ships_with_ticket_ids(self):
        self.assertIn("4 bug fixes in the last year (BUG-0, BUG-1, BUG-2, BUG-3)", self.by("consumer", "checkout").text)
        self.assertIn("blocked by shared/client.py, which platform hasn't migrated",
                      self.by("consumer", "fulfillment").text)
        self.assertIn("You are blocking fulfillment", self.by("consumer", "platform").text)

    def test_stall_reasons_follow_the_served_policy(self):
        w = self.by("stall_reason", "checkout")
        self.assertEqual([r["reason"] for r in w.task.facts["facts"]["reasons"]],
                         ["Orphaned owner", "Semantics unclear", "Test gap", "Competing load"])  # v0: by number
        self.assertEqual(w.task.facts["source"]["policy"], "stall-v0")
        self.assertIsNone(w.task.policy_id)  # the fixture block has no policy rows to reference

    def test_maker_each_untracked_site_in_exactly_one_draft(self):
        db = K.build()
        from meridian import auditor as A
        A.point(db, AT, K.REPO, K.SYMBOL, "R2")
        untracked = {f"{p}:{ln}" for p, ln in db.execute(
            "SELECT path, line FROM site_now WHERE status = 'present' AND call_site_id NOT IN "
            "(SELECT target FROM ticket_map WHERE target_kind = 'call_site')")}
        drafted = [s for w in self.written if w.task.kind == "ticket_draft"
                   for s in w.task.facts["facts"]["sites"].split(", ")]
        self.assertEqual(sorted(drafted), sorted(untracked))
        self.assertEqual(len(drafted), len(set(drafted)))

    def test_no_person_in_any_fact_or_prompt(self):
        for w in self.written:
            for text in (json.dumps(w.task.facts), G.prompt(w.task)):
                self.assertIsNone(EMAIL.search(text), text)
                for name in self.people:
                    self.assertNotRegex(text, rf"(?i)\b{re.escape(name)}\b")
                self.assertNotIn("p_", json.dumps(w.task.facts["facts"]))  # no person_id from the stall evidence

    def test_a_template_that_fails_its_own_gate_stops_the_run(self):
        t = self.by("ticket_draft", "billing").task
        bad = G.Task(t.kind, t.target_kind, t.target, t.owner_team, t.repo, t.facts, t.template + " 99 more.")
        with self.assertRaises(ValueError):
            G.write(bad, teams=self.teams, people=self.people)


class Rows(unittest.TestCase):
    """The suggestion rows (Contract 5, schema.sql)."""

    def test_rows_fit_the_schema_and_are_offered_once(self):
        (written, rows), db = run()
        new = [r for r in rows if r["run_id"] == "R2"]
        self.assertEqual(sorted((r["kind"], r["target"]) for r in new),
                         [("rewrite", "CHK-311"), ("rewrite", "FUL-7"), ("stall_reason", "billing"),
                          ("stall_reason", "checkout"), ("stall_reason", "fulfillment"), ("stall_reason", "platform"),
                          ("ticket_draft", "billing"), ("ticket_draft", "checkout"), ("ticket_draft", "payments")])
        cols = [r[1] for r in db.execute("PRAGMA table_info(suggestion)")]
        db.executemany(f"INSERT INTO suggestion ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [[r[c] for c in cols] for r in new])  # the CHECKs and the UNIQUE hold
        _, again = G.run(db, CFG, "R2", AT)
        self.assertEqual(len(again), len(rows))  # the same facts aren't offered again
        for r in new:
            self.assertEqual((r["source"], r["model"], r["route"], r["agent"], r["offered_at"]),
                             ("template", None, "rules", "writer", AT))

    def test_the_consumer_text_has_no_row(self):
        (written, rows), _ = run()
        self.assertFalse(any(r["kind"] == "consumer" for r in rows))

    def test_the_committed_contract_examples_have_todays_shape(self):
        # Model-Pipeline/tests/contracts/make_vishwa.py: rerun it after a shape change, and announce it (S10)
        here = Path(__file__).resolve().parents[2] / "Model-Pipeline" / "tests" / "contracts"
        facts = json.loads((here / "facts.json").read_text(encoding="utf-8"))
        rows = json.loads((here / "suggestions.json").read_text(encoding="utf-8"))
        (written, now), _ = run()
        now = [r for r in now if r["run_id"] == "R2"]
        built = {}
        for w in written:
            built.setdefault(w.task.kind, w.task.facts)
        self.assertEqual({k: sorted(v["facts"]) for k, v in facts.items()},
                         {k: sorted(v["facts"]) for k, v in built.items()})
        self.assertEqual({tuple(sorted(r)) for r in rows}, {tuple(sorted(r)) for r in now})
        self.assertEqual(sorted((r["kind"], r["target"]) for r in rows), sorted((r["kind"], r["target"]) for r in now))


class WithLLM(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Cache(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_a_good_reply_ships_and_a_warm_cache_makes_0_calls(self):
        llm = Fake(good)
        (first, rows1), _ = run(llm=llm, cache=self.cache)
        self.assertEqual(len(llm.calls), 14)
        self.assertTrue(all(w.source == "llm" and w.model == G.SMALL for w in first))
        again = Fake(good)
        (second, rows2), _ = run(llm=again, cache=self.cache)
        self.assertEqual(again.calls, [])  # PASS-BARS §5: warm cache, 0 LLM calls
        self.assertEqual(rows1, rows2)     # and the same rows, byte for byte
        (off, _), _ = run(cache=self.cache)  # no LLM at all: the cache still answers
        self.assertTrue(all(w.source == "llm" for w in off))

    def test_bad_json_retries_one_model_up_then_ships_the_template(self):
        llm = Fake(lambda m, f: "not json" if m == G.SMALL else good(m, f))
        (written, _), _ = run(llm=llm)
        self.assertTrue(all(w.model == G.MID and w.attempts[0][2] == "bad_json" for w in written))
        never = Fake(lambda m, f: '{"text": "x"}')  # no "used"
        (written, _), _ = run(llm=never)
        self.assertEqual(never.calls, [G.SMALL, G.MID] * 14)  # never the large model
        self.assertTrue(all(w.source == "template" for w in written))

    def test_a_gate_miss_retries_then_ships_the_whole_template(self):
        llm = Fake(lambda m, f: json.dumps({"text": f"{f['team']} has 977 sites.", "used": ["team"]}))
        (written, _), _ = run(llm=llm)
        for w in written:
            self.assertEqual([a[2] for a in w.attempts], ["gate_miss", "gate_miss"])
            self.assertEqual(w.text, w.task.template)  # never the bad text with the number cut out

    def test_a_cached_reply_is_gated_again_on_every_run(self):
        (written, _), _ = run()
        w = next(x for x in written if x.task.kind == "rewrite")
        k = key(G.SMALL, G.prompt_version("rewrite"), w.task.facts["facts"])
        self.cache.put(k, {"reply": json.dumps({"text": "CHK-311: ask payments.", "used": ["key"]}),
                           "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0})
        (again, _), _ = run(cache=self.cache)
        hit = next(x for x in again if x.task.target == w.task.target)
        self.assertEqual((hit.source, hit.attempts), ("template", [(G.SMALL, True, "gate_miss")]))

    def test_a_miss_with_strict_cache_stops_the_run(self):
        with self.assertRaises(G.CacheMiss):
            run(cache=self.cache, strict_cache=True)  # PASS-BARS §5: a cache miss in CI fails the job

    def test_an_edited_prompt_is_a_new_version(self):
        v = G.prompt_version("rewrite")
        with mock.patch.dict(G.PROMPTS, {"rewrite": G.PROMPTS["rewrite"] + " Be brief."}):
            self.assertNotEqual(G.prompt_version("rewrite"), v)
        self.assertEqual(G.prompt_version("rewrite"), v)

    def test_no_person_in_any_cache_entry(self):
        run(llm=Fake(good), cache=self.cache)
        _, people = G.names(K.build())
        for k, e in self.cache.entries().items():
            body = json.dumps(e)
            self.assertIsNone(EMAIL.search(body), k)
            for name in people:
                self.assertNotRegex(body, rf"(?i)\b{re.escape(name)}\b", k)


class Logging(unittest.TestCase):
    def test_every_answer_and_template_is_logged_with_its_facts_hash(self):
        calls = []
        (written, _), _ = run(log=lambda **kw: calls.append(kw))
        self.assertEqual(len(calls), 14)
        self.assertEqual({c["facts_hash"] for c in calls}, {w.facts_hash for w in written})
        self.assertTrue(all(c["model"] == G.TEMPLATE_MODEL and c["source"] == "template" and c["tokens_in"] == 0
                            and c["gate_pass"] is True and c["route"] == "rules" for c in calls))
        calls.clear()
        run(llm=Fake(lambda m, f: "nope" if m == G.SMALL else good(m, f)), log=lambda **kw: calls.append(kw))
        first = [c for c in calls if c["model"] == G.SMALL]
        self.assertTrue(all(c["gate_pass"] is None and c["source"] is None for c in first))  # bad JSON: not gated


# tracking imports mlflow only when a run starts, so look for mlflow itself. Data-Pipeline's CI job has no mlflow:
# there the model job runs this same test (Model-Pipeline/tests/test_handoff_writer.py)
tracking = None
if importlib.util.find_spec("mlflow"):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Model-Pipeline"))
    from model import tracking


@unittest.skipIf(tracking is None, "needs mlflow (Model-Pipeline/requirements.txt); CI's model job runs it")
class HandOff(unittest.TestCase):
    """packets/README.md, Nov 9: the writer calls fact_gate.check and tracking.log_llm_call(..., facts_hash=) on the
    fixture, with the LLM off."""

    def test_the_writer_logs_to_meridian_agents_with_the_llm_off(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": f"sqlite:///{tmp.name}/mlflow.db"}):
            with tracking.start_run(G.AGENT, owner="vishwa", experiment=tracking.AGENTS, block_run_id="R2") as r:
                (written, _), _ = run(log=tracking.log_llm_call)
            table = tracking.load_llm_calls([r.info.run_id])
        self.assertEqual(len(table), 14)
        self.assertEqual(set(table["facts_hash"]), {w.facts_hash for w in written})
        self.assertEqual(set(table["source"]), {"template"})


if __name__ == "__main__":
    unittest.main()
