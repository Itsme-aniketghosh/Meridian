"""The writer agent on KNOWLEDGE-BASE.md's fixture (tests/test_kb.py), against PASS-BARS §5.

    python -m unittest tests.test_agents -v      # from Data-Pipeline/

No test calls a real LLM: `Fake` plays one, so bad JSON, gate misses, retries and the cache are all checked offline.
`Ugly` puts the real block's awkward names into the fixture (review of PR #48): a placeholder person named test, teams
named test, tests and Bug, a symbol named url, a non-.py file, a ticket across two symbols and two owners.
The hand-off test (packets/README.md, Nov 9) runs the writer with the LLM off through model.tracking.log_llm_call.
"""
import contextlib
import importlib.util
import io
import json
import time
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import agents as G
from meridian import auditor as A
from meridian.common import CONFIG, load_config
from meridian.fact_gate import check
from meridian.llm_cache import ROOT as COMMITTED_CACHE, Cache, key

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
    """A reply that only says facts: the team (or the ticket key, for a rewrite)."""
    said, used = (f"{facts['key']} needs a done condition.", ["key"]) if "key" in facts else \
        (f"Work for {facts['team']}.", ["team"])
    return json.dumps({"text": said, "used": used})


def run(db=None, **kw):
    db = db or K.build()
    return G.run(db, CFG, "R2", AT, **kw), db


def person(db, pid, name, email, generic=0, bot=0):
    db.execute("INSERT INTO person (run_id, person_id, name, is_bot) VALUES ('R2', ?, ?, ?)", (pid, name, bot))
    db.execute("INSERT INTO person_alias (run_id, email, person_id, name, generic, source) "
               "VALUES ('R2', ?, ?, ?, ?, 'single')", (email, pid, name, generic))


def team(db, name):
    db.execute("INSERT INTO team_map (run_id, dataset, subject_kind, subject, team, rule) "
               "VALUES ('R2', 'shop', 'label', ?, ?, 'test')", (name, name))


def ugly():
    """The fixture with the real block's awkward names in it."""
    db = K.build()
    person(db, "p_test", "test", "joseph@example.invalid", generic=1)  # a placeholder alias, as in the real block
    for t in ("test", "tests", "Bug", "urls"):
        team(db, t)
    db.execute("UPDATE call_site SET symbol = 'url'")                    # a symbol whose plural is a team
    db.execute("UPDATE call_site SET path = 'cart/pricing.sample' WHERE path = 'cart/pricing.py'")  # not .py
    db.execute("UPDATE file_bug_fixes SET path = 'cart/pricing.sample' WHERE path = 'cart/pricing.py'")
    return db


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
            self.assertEqual((w.source, w.model, w.attempts, w.template_misses), ("template", None, [], ()))
            self.assertTrue(check(w.text, w.task.facts, teams=self.teams, people=self.people, vocab=G.VOCAB), w.text)

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

    def test_the_consumer_text_keeps_the_riskiest_files_and_counts_the_rest(self):
        # gap 16 (vishwa decision 6): checkout's riskiest file holds all 4 of its bug fixes, so its other file is cut
        w = self.by("consumer", "checkout")
        facts = w.task.facts["facts"]
        self.assertEqual([f["path"] for f in facts["files"]], ["cart/pricing.py"])
        self.assertEqual((facts["files_cut"], facts["sites_cut"], facts["left"], facts["files_n"]), (1, 1, 3, 2))
        self.assertIn("- 1 call site in 1 other file", w.text)
        db = K.build()
        whole = next(t for t in G.tasks(db, load_config(CONFIG), "R2", K.R2[1], cut=False)
                     if t.kind == "consumer" and t.target == "checkout")
        gone = [f["path"] for f in whole.facts["facts"]["files"] if f["path"] != "cart/pricing.py"]
        self.assertEqual(len(gone), 1)
        self.assertNotIn(gone[0], w.text)                      # nothing cut is named
        self.assertNotIn("files_cut", whole.facts["facts"])
        self.assertNotEqual(G.facts_hash(whole.facts), w.facts_hash)  # the cut is a new cache key

    def test_a_stall_text_names_its_symbol_and_start_and_claims_no_likelihood(self):
        w = self.by("stall_reason", "checkout")
        self.assertEqual(sorted(w.task.facts["facts"]), ["left", "reasons", "since", "symbol", "team"])  # no age
        self.assertIn("verify_token call sites since", w.text)
        self.assertNotIn("likely", w.text)  # 06-dead-ideas: no probabilities on stall reasons

    def test_maker_each_untracked_site_in_exactly_one_draft(self):
        db = A.point(K.build(), AT, K.REPO, K.SYMBOL, "R2")
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


class RealBlockNames(unittest.TestCase):
    """Review of PR #48, finding 1: on the real block the run died on its first template."""

    def test_awkward_names_neither_stop_the_run_nor_fail_a_template(self):
        result, db = run(ugly())
        self.assertEqual(result.skipped, [])  # the placeholder person 'test' isn't a person: "Test gap" is fine
        self.assertEqual({w.task.kind: [] for w in result.written},
                         {w.task.kind: [m for x in result.written if x.task.kind == w.task.kind
                                        for m in x.template_misses] for w in result.written})
        texts = "\n".join(w.text for w in result.written)
        self.assertIn("cart/pricing.sample:214, :231", texts)
        self.assertIn("bug fixes", texts)  # a team named Bug doesn't make "bug fixes" a team

    def test_a_task_whose_facts_name_a_person_is_skipped_and_counted_not_fatal(self):
        db = K.build()
        person(db, "p_gap", "Gap", "gap@example.invalid")  # a real person named like a stall reason's word
        result, _ = run(db)
        self.assertTrue(result.skipped)
        self.assertTrue(all(kind == "stall_reason" for kind, _, _ in result.skipped))
        self.assertEqual(G.report(result)["stall_reason"]["skipped_person"], len(result.skipped))
        self.assertEqual(len(result.written) + len(result.skipped), 14)

    def test_a_template_the_gate_flags_still_ships_and_is_counted(self):
        t = next(w.task for w in run()[0].written if w.task.kind == "ticket_draft")
        bad = G.Task(t.kind, t.target_kind, t.target, t.owner_team, t.repo, t.facts, t.template + " 99 more.")
        w = G.write(bad, teams=["billing"], people=[])
        self.assertEqual((w.source, w.text, [m.token for m in w.template_misses]), ("template", bad.template, ["99"]))

    def test_a_block_with_no_suggestion_table(self):
        db = K.build()
        db.executescript("DROP TABLE outcome; DROP TABLE suggestion;")  # a block from before schema v1.3
        result, _ = run(db)
        self.assertEqual(len(result.rows), 9)


class Rewrites(unittest.TestCase):
    """Findings 6 and 7: one rewrite per ticket, naming every symbol and every owner."""

    def test_a_ticket_across_two_symbols_gets_one_rewrite_naming_both(self):
        db = K.build()
        db.execute("UPDATE call_site SET symbol = 'other_sym' WHERE path = 'cart/pricing.py' AND line = 231")
        rewrites = [w for w in run(db)[0].written if w.task.kind == "rewrite" and w.task.target == "CHK-311"]
        self.assertEqual(len(rewrites), 1)
        self.assertIn("off other_sym and verify_token", rewrites[0].text)
        self.assertIn("Done when no site above calls other_sym and verify_token", rewrites[0].text)

    def test_a_cross_team_ticket_names_each_owner(self):
        db = K.build()
        db.execute("UPDATE call_site SET owner_team = 'billing' WHERE path = 'cart/pricing.py' AND line = 231")
        w = next(w for w in run(db)[0].written if w.task.target == "CHK-311")
        self.assertIn("cart/pricing.py:214 · owner checkout", w.text)
        self.assertIn("cart/pricing.py:231 · owner billing", w.text)

    def test_no_shared_directory_means_no_area(self):
        self.assertEqual((G._area(["cart/a.py", "cart/b.py"]), G._area(["pricing.py"]), G._area(["a/x.py", "b/y.py"])),
                         ("cart/", "", ""))


class Stalls(unittest.TestCase):
    """Findings 9 and 10: the served policy's order, and a row that stays the same while the stall lasts."""

    def test_the_served_policy_orders_the_reasons(self):
        backwards = A.Policy("stall-v9", "stall", 9, "t", lambda x: sorted(x.facts, reverse=True),
                             "2026-01-01T00:00:00Z")
        with mock.patch.dict(A.POLICIES, {"stall-v9": backwards}), \
                mock.patch.object(A, "serve", lambda *a: "stall-v9"):
            w = next(w for w in run()[0].written if w.task.kind == "stall_reason" and w.task.target == "checkout")
        self.assertEqual([r["reason"] for r in w.task.facts["facts"]["reasons"]],
                         ["Competing load", "Test gap", "Semantics unclear", "Orphaned owner"])
        self.assertEqual(w.task.facts["source"]["policy"], "stall-v9")

    def test_the_same_stall_a_week_later_is_the_same_suggestion(self):
        now = {w.task.target: w.facts_hash for w in run()[0].written if w.task.kind == "stall_reason"}
        later = G.run(K.build(), CFG, "R2", "2026-10-08T00:00:00Z")
        later = {w.task.target: w.facts_hash for w in later.written if w.task.kind == "stall_reason"}
        self.assertEqual(now, later)


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

    def test_workers_write_the_same_texts_and_rows_with_the_same_calls(self):
        one, many = Fake(good), Fake(lambda m, f: (time.sleep(0.01), good(m, f))[1])  # slow, so threads overlap
        (w1, rows1), _ = run(llm=one, cache=Cache(Path(self.tmp.name) / "one"))
        (w8, rows8), _ = run(llm=many, cache=Cache(Path(self.tmp.name) / "many"), workers=8)
        self.assertEqual([(w.task.kind, w.task.target, w.text) for w in w1],
                         [(w.task.kind, w.task.target, w.text) for w in w8])
        self.assertEqual(json.dumps(rows1, sort_keys=True), json.dumps(rows8, sort_keys=True))
        self.assertEqual(sorted(one.calls), sorted(many.calls))  # a shared cache key is still one call

    def test_a_good_reply_ships_and_a_warm_cache_makes_0_calls(self):
        llm = Fake(good)
        (first, rows1), _ = run(llm=llm, cache=self.cache)
        self.assertEqual(len(llm.calls), 14)
        self.assertTrue(all(w.source == "llm" and w.model == G.SMALL for w in first))
        again = Fake(good)
        (second, rows2), _ = run(llm=again, cache=self.cache)
        self.assertEqual(again.calls, [])  # PASS-BARS §5: warm cache, 0 LLM calls
        self.assertEqual(json.dumps(rows1, sort_keys=True), json.dumps(rows2, sort_keys=True))  # byte for byte
        (off, _), _ = run(cache=self.cache)  # no LLM at all: the cache still answers
        self.assertTrue(all(w.source == "llm" for w in off))

    def test_bad_json_climbs_the_ladder_then_ships_the_template(self):
        llm = Fake(lambda m, f: "not json" if m == G.SMALL else good(m, f))
        (written, _), _ = run(llm=llm)
        self.assertTrue(all(w.model == G.MID and w.attempts[0][2] == "bad_json" for w in written))
        never = Fake(lambda m, f: '{"text": "x"}')  # no "used"
        (written, _), _ = run(llm=never)
        self.assertEqual(never.calls, [G.SMALL, G.MID, G.LARGE] * 14)  # 04 §6: small, mid, large
        self.assertTrue(all(w.source == "template" for w in written))

    def test_a_fenced_json_reply_is_read(self):
        llm = Fake(lambda m, f: "```json\n" + good(m, f) + "\n```")
        (written, _), _ = run(llm=llm)
        self.assertTrue(all(w.source == "llm" for w in written))

    def test_a_gate_miss_climbs_then_ships_the_whole_template(self):
        llm = Fake(lambda m, f: json.dumps({"text": f"{f.get('team', f.get('key'))} has 977 sites.",
                                            "used": ["team"] if "team" in f else ["key"]}))
        (written, _), _ = run(llm=llm)
        for w in written:
            self.assertEqual([a[2] for a in w.attempts], ["gate_miss"] * 3)
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
            run(cache=self.cache, strict_cache=True)  # PASS-BARS §6.5: a cache miss in CI fails the job

    def test_any_change_to_the_prompt_is_a_new_version(self):
        v = G.prompt_version("rewrite")
        for patch in (mock.patch.dict(G.PROMPTS, {"rewrite": G.PROMPTS["rewrite"] + " Be brief."}),
                      mock.patch.object(G, "FRAME", G.FRAME.replace("Facts:", "Facts as JSON:")),
                      mock.patch.object(G, "FACTS_FORMAT", "bullets"),
                      mock.patch.object(G, "SYSTEM", G.SYSTEM + " ")):
            with patch:
                self.assertNotEqual(G.prompt_version("rewrite"), v)
        self.assertEqual(G.prompt_version("rewrite"), v)

    def test_a_reply_naming_a_person_never_reaches_the_cache(self):
        llm = Fake(lambda m, f: json.dumps({"text": "Ask Ann at ann@acme.com.", "used": []}))
        (written, _), _ = run(llm=llm, cache=self.cache)
        self.assertTrue(all(w.source == "template" for w in written))
        for k, e in self.cache.entries().items():
            body = json.dumps(e)
            self.assertIsNone(EMAIL.search(body), k)
            self.assertNotRegex(body, r"\bAnn\b", k)
            self.assertEqual(e["redacted"], "a person")
        again = Fake(good)
        run(llm=again, cache=self.cache)
        self.assertEqual(again.calls, [])  # the redacted entry still answers: 0 calls on a rerun

    def test_no_person_in_any_cache_entry(self):
        run(llm=Fake(good), cache=self.cache)
        _, people = G.names(K.build())
        for root in (self.tmp.name, COMMITTED_CACHE):  # this run's cache, and the committed llm_cache/
            for k, e in Cache(root).entries().items():
                body = json.dumps(e)
                self.assertIsNone(EMAIL.search(body), k)
                for name in people:
                    self.assertNotRegex(body, rf"(?i)\b{re.escape(name)}\b", k)


class Logging(unittest.TestCase):
    """Finding 3: only LLM answers are logged, so MLflow's api_calls and gate rates are the LLM's."""

    def test_a_template_with_no_llm_attempt_is_not_a_call(self):
        calls = []
        run(log=lambda **kw: calls.append(kw))
        self.assertEqual(calls, [])

    def test_every_answer_is_logged_once_with_what_shipped(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache, calls = Cache(tmp.name), []
        run(llm=Fake(lambda m, f: "nope" if m == G.SMALL else good(m, f)), cache=cache,
            log=lambda **kw: calls.append(kw))
        self.assertEqual(len(calls), 28)
        small = [c for c in calls if c["model"] == G.SMALL]
        self.assertTrue(all(c["gate_pass"] is None and c["source"] is None and not c["cache_hit"] for c in small))
        mid = [c for c in calls if c["model"] == G.MID]
        self.assertTrue(all(c["gate_pass"] is True and c["source"] == "llm" for c in mid))
        calls.clear()
        run(cache=cache, log=lambda **kw: calls.append(kw))  # LLM off, warm cache: every answer is a hit
        self.assertTrue(calls and all(c["cache_hit"] and c["tokens_in"] == 0 and c["cost_usd"] == 0 for c in calls))

    def test_the_last_failed_attempt_says_the_template_shipped(self):
        calls = []
        run(llm=Fake(lambda m, f: "nope"), log=lambda **kw: calls.append(kw))
        self.assertEqual({c["source"] for c in calls if c["model"] == G.LARGE}, {"template"})
        self.assertEqual({c["source"] for c in calls if c["model"] != G.LARGE}, {None})


class Stub:
    """A router that starts every task on the given ladder, and remembers what it was shown."""

    def __init__(self, ladder, route="jev"):
        self.answer, self.seen = (tuple(ladder), route), []

    def ladder(self, task):
        self.seen.append((task.kind, task.target))
        return self.answer


class Routing(unittest.TestCase):
    """Jev (Rachna, meridian/jev.py) picks where each task starts: write(ladder=, route=) from Router.ladder(task)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_writer_starts_where_the_router_says_and_records_its_route(self):
        llm, calls = Fake(good), []
        (written, rows), _ = run(llm=llm, router=Stub((G.MID, G.LARGE)), log=lambda **kw: calls.append(kw))
        self.assertEqual(set(llm.calls), {G.MID})
        self.assertTrue(all(w.model == G.MID and w.route == "jev" for w in written))
        new = [r for r in rows if r["run_id"] == "R2"]  # the fixture's R1 rows stay as they were offered
        self.assertTrue(new)
        self.assertEqual({r["route"] for r in new}, {"jev"})
        self.assertEqual({c["route"] for c in calls}, {"jev"})

    def test_it_climbs_only_the_ladder_it_was_given(self):
        never = Fake(lambda m, f: "nope")
        (written, _), _ = run(llm=never, router=Stub((G.MID, G.LARGE)))
        self.assertEqual(never.calls, [G.MID, G.LARGE] * len(written))
        self.assertTrue(all(w.source == "template" for w in written))

    def test_an_empty_ladder_ships_the_template_with_no_call(self):
        llm = Fake(good)
        (written, rows), _ = run(llm=llm, router=Stub((), "rules"))
        self.assertEqual(llm.calls, [])
        self.assertTrue(all(w.source == "template" and w.route == "rules" for w in written))

    def test_no_router_is_the_fixed_rules(self):
        (written, rows), _ = run(llm=Fake(good))
        self.assertTrue(all(w.model == G.SMALL and w.route == "rules" for w in written))
        self.assertEqual({r["route"] for r in rows if r["run_id"] == "R2"}, {"rules"})

    def test_a_task_naming_a_person_never_reaches_the_router(self):
        db = K.build()
        person(db, "p_gap", "Gap", "gap@example.invalid")
        router = Stub((G.SMALL,))
        result, _ = run(db, router=router)
        self.assertTrue(result.skipped)
        self.assertEqual({(k, t) for k, t, _ in result.skipped} & set(router.seen), set())
        self.assertEqual(len(router.seen), len(result.written))

    def test_with_jev_off_the_rows_are_the_same_as_without_it(self):
        from meridian.jev import Router
        cache = Cache(self.tmp.name)
        _, rows0 = run(llm=Fake(good), cache=cache)[0]
        _, rows1 = run(cache=cache, router=Router(cache=cache))[0]  # Jev off: rules, smallest model, warm cache
        self.assertEqual(json.dumps(rows0, sort_keys=True), json.dumps(rows1, sort_keys=True))

    def test_jevs_pick_starts_the_writer(self):
        from meridian.jev import Router
        teams, people = G.names(K.build())
        calls = []  # Jev's own calls: its prompt isn't the writer's, so it answers whole

        def jev_llm(model, prompt):
            calls.append(model)
            return {"reply": json.dumps({"agent": "writer", "model": G.MID, "reason": "A short text."}),
                    "tokens_in": 300, "tokens_out": 20, "cost_usd": 0.0001}
        writer = Fake(good)
        (written, rows), _ = run(llm=writer, router=Router(llm=jev_llm, teams=teams, people=people))
        self.assertEqual(len(calls), len(written))  # one Jev pick per task
        self.assertEqual(set(writer.calls), {G.MID})
        self.assertTrue(all(w.route == "jev" and w.model == G.MID for w in written))


class Command(unittest.TestCase):
    """python -m meridian.agents on a fixture file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "fixture.sqlite"
        src = K.build()
        src.commit()  # K.build leaves its inserts open: backup() would wait on them forever
        dst = sqlite3.connect(self.db)
        src.backup(dst)
        dst.close()

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = G.main(["--run-id", "R2", "--db", str(self.db), "--cache-dir", str(Path(self.tmp.name) / "c"),
                           "--quiet", *args])
        return code, out.getvalue()

    def test_write_puts_the_rows_and_the_report_next_to_the_block(self):
        out = Path(self.tmp.name) / "out"
        code, said = self.main("--write", "--out", str(out))
        self.assertEqual(code, 0)
        rows = [json.loads(x) for x in (out / "suggestion.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(r["run_id"] == "R2" for r in rows), 9)
        self.assertEqual(json.loads((out / "writer_report.json").read_text())["consumer"]["texts"], 5)
        self.assertIn("rewrite: 2 texts, 2 templates", said)

    def test_jev_without_an_llm_falls_back_to_the_same_rows(self):
        plain, routed = Path(self.tmp.name) / "plain", Path(self.tmp.name) / "routed"
        self.assertEqual(self.main("--write", "--out", str(plain))[0], 0)
        self.assertEqual(self.main("--write", "--out", str(routed), "--jev")[0], 0)
        self.assertEqual((routed / "suggestion.jsonl").read_bytes(), (plain / "suggestion.jsonl").read_bytes())

    def test_write_refuses_an_at_that_isnt_the_snapshot(self):
        with self.assertRaises(SystemExit):
            self.main("--write", "--at", "2026-10-08T00:00:00Z")


# tracking imports mlflow only when a run starts, so look for mlflow itself. Data-Pipeline's CI job has no mlflow:
# there the model job runs this same test (Model-Pipeline/tests/test_handoff_writer.py)
tracking = None
if importlib.util.find_spec("mlflow"):
    sys.path.append(str(Path(__file__).resolve().parents[2] / "Model-Pipeline"))  # last: both have a package tests
    from model import tracking


@unittest.skipIf(tracking is None, "needs mlflow (Model-Pipeline/requirements.txt); CI's model job runs it")
class HandOff(unittest.TestCase):
    """packets/README.md, Nov 9: the writer calls fact_gate.check and tracking.log_llm_call(..., facts_hash=) on the
    fixture, with the LLM off: the answers come from a warm cache, so they're calls with 0 cost."""

    def test_the_writer_logs_to_meridian_agents_with_the_llm_off(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
        self.addCleanup(tmp.cleanup)
        cache = Cache(Path(tmp.name) / "cache")
        run(llm=Fake(good), cache=cache)  # warm it, logging nothing
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": f"sqlite:///{tmp.name}/mlflow.db"}):
            with tracking.start_run(G.AGENT, owner="vishwa", experiment=tracking.AGENTS, block_run_id="R2") as r:
                (written, _), _ = run(cache=cache, log=tracking.log_llm_call)
            table = tracking.load_llm_calls([r.info.run_id])
        self.assertEqual(len(table), 14)
        self.assertEqual(set(table["facts_hash"]), {w.facts_hash for w in written})
        self.assertTrue(table["cache_hit"].all())
        self.assertEqual(set(table["source"]), {"llm"})


class Decision5b(unittest.TestCase):
    """The person check and the `used` list, as measured on the real block (decisions.md, decision 5b)."""

    FACTS = {"facts": {"team": "billing", "left": 1, "files": [{"path": "a.py", "sites": 1, "bug_fixes": 0}],
                       "reasons": [{"reason": "Never notified", "evidence": 1}]}}

    def reply(self, used):
        return json.dumps({"text": "billing has 1 call site left in a.py.", "used": used})

    def test_used_may_name_a_fact_inside_another(self):
        for used in (["team"], ["files[0].path", "left"], ["files.bug_fixes"], ["sites"], ["reasons[0].evidence"]):
            self.assertIsNotNone(G.parse(self.reply(used), self.FACTS), used)

    def test_used_may_not_name_a_value_or_an_unknown_field(self):
        for used in (["2016-04-22T17:39:13Z"], ["billing"], ["files[0].owner"], [""], "team"):
            self.assertIsNone(G.parse(self.reply(used), self.FACTS), used)

    def test_a_person_named_a_function_word_is_not_a_name_but_a_first_name_still_is(self):
        db = K.build()
        person(db, "p_nothing", "NOTHING", "nothing@example.com")
        person(db, "p_will", "Will", "will@example.com")
        _, people = G.names(db)
        self.assertNotIn("NOTHING", people)
        self.assertIn("Will", people)
        self.assertFalse(G.names_a_person("billing is blocked by nothing.", people))
        self.assertTrue(G.names_a_person("Ask Will to review it.", people))


if __name__ == "__main__":
    unittest.main()
