"""The LLM client (meridian.llm) and the writer's model comparison, offline.

    python -m unittest tests.test_llm -v      # from Data-Pipeline/

No test reaches a network: each provider is replaced by a transport that plays one.
"""
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import agents as G
from meridian import llm as L
from meridian import writer_compare as W
from meridian.llm_cache import Cache


def reply(text):
    return lambda m, prompt: (text, 1000, 200)


def client(**kw):
    t = {p: reply('{"text": "x", "used": []}') for p in ("claude", "gemini", "maas", "mistral", "ollama")}
    t.update(kw.pop("transport", {}))
    return L.Client("proj", transport=t, **kw)


class TestClient(unittest.TestCase):
    def test_it_returns_what_the_fake_returns_with_cost_from_the_price(self):
        r = client()("claude-sonnet-5-5", "hi")
        self.assertEqual(set(r), {"reply", "tokens_in", "tokens_out", "cost_usd"})
        self.assertEqual((r["tokens_in"], r["tokens_out"]), (1000, 200))
        self.assertAlmostEqual(r["cost_usd"], (1000 * 2 + 200 * 10) / 1e6)

    def test_every_model_has_a_known_provider(self):
        for name, m in L.MODELS.items():
            self.assertTrue(hasattr(L.Client, f"_{m.provider}"), name)

    def test_the_writer_ladder_is_in_the_table(self):
        self.assertTrue(set(G.LADDER) <= set(L.MODELS))

    def test_a_model_with_no_checked_price_is_never_called(self):
        called = []
        c = client(transport={"gemini": lambda m, p: called.append(1)})
        with self.assertRaises(L.Unpriced):
            c("gemini-pro", "hi")
        self.assertEqual(called, [])

    def test_an_unknown_model_is_refused(self):
        with self.assertRaises(ValueError):
            client()("gpt-9", "hi")

    def test_it_stops_at_its_spend_cap(self):
        c = client(max_usd=0.005)
        c("claude-opus-5-5", "hi")   # 0.008: the cap is checked before each call
        with self.assertRaises(L.BudgetExceeded):
            c("claude-haiku-5-5", "hi")
        self.assertEqual(c.calls, 1)

    def test_a_retryable_error_is_retried_and_a_bad_request_is_not(self):
        tries = []

        def flaky(m, p):
            tries.append(1)
            if len(tries) < 3:
                raise L.HTTPError(429, "slow down")
            return "ok", 1, 1
        with mock.patch.object(L.time, "sleep"):
            self.assertEqual(client(transport={"claude": flaky})("claude-haiku-5-5", "hi")["reply"], "ok")
        self.assertEqual(len(tries), 3)

        def bad(m, p):
            tries.append(1)
            raise L.HTTPError(400, "no")
        tries.clear()
        with self.assertRaises(L.HTTPError):
            client(transport={"claude": bad})("claude-haiku-5-5", "hi")
        self.assertEqual(len(tries), 1)

    def test_no_project_is_a_clear_stop(self):
        with mock.patch.dict(L.os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                L.Client(None)("claude-haiku-5-5", "hi")

    def test_claude_refusal_is_an_empty_reply(self):
        class Block:
            type, text = "text", "partial"

        class Resp:
            stop_reason, content = "refusal", [Block()]
            usage = type("U", (), {"input_tokens": 5, "output_tokens": 0})

        c = L.Client("proj", transport=None)
        c._clients[("claude", "global")] = type("C", (), {"messages": type("M", (), {
            "create": staticmethod(lambda **kw: Resp())})})
        self.assertEqual(c("claude-haiku-5-5", "hi")["reply"], "")


class TestWriterCLI(unittest.TestCase):
    """--llm --models --limit --calls through main, with the client's providers faked."""

    def test_one_model_writes_the_first_tasks_and_logs_each_call(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "block.sqlite"
        src = K.build()
        src.commit()  # K.build leaves its inserts open: backup() would wait on them forever
        with contextlib.closing(sqlite3.connect(db)) as out:
            src.backup(out)

        def answer(m, prompt):
            facts = json.loads(prompt.split("Facts:\n", 1)[1])
            said = f"{facts['key']} needs a done condition." if "key" in facts else f"Work for {facts['team']}."
            return json.dumps({"text": said, "used": []}), 800, 90

        real = L.Client
        calls = Path(tmp.name) / "calls.jsonl"
        with mock.patch.object(L, "Client", lambda **kw: real("proj", transport={"claude": answer}, **kw)), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            G.main(["--run-id", "R2", "--db", str(db), "--llm", "--models", "claude-haiku-5-5", "--limit", "3",
                    "--cache-dir", str(Path(tmp.name) / "cache"), "--calls", str(calls), "--quiet"])
        rows = [json.loads(x) for x in calls.read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["model"] for r in rows}, {"claude-haiku-5-5"})
        self.assertFalse(any(r["cache_hit"] for r in rows))
        self.assertEqual(len(list(Cache(Path(tmp.name) / "cache").root.glob("*.json"))), 3)
        t = W.table(rows)["claude-haiku-5-5"]
        self.assertEqual((t["tasks"], t["paid_calls"]), (3, 3))


class TestCompare(unittest.TestCase):
    def row(self, model, gate, usd, ms=100, hit=False):
        return {"agent": "writer", "model": model, "gate_pass": gate, "cost_usd": usd, "latency_ms": ms,
                "cache_hit": hit}

    def test_the_cheapest_within_2_points_wins(self):
        rows = [self.row("big", True, 0.01)] * 100 + \
               [self.row("small", True, 0.001)] * 99 + [self.row("small", False, 0.001)] + \
               [self.row("tiny", True, 0.0001)] * 90 + [self.row("tiny", None, 0.0001)] * 10
        t = W.table(rows)
        self.assertEqual(t["tiny"]["valid_json"], 90.0)
        self.assertEqual(W.pick(t), "small")

    def test_a_tie_goes_to_open_weights(self):
        rows = [self.row("claude-haiku-5-5", True, 0.001), self.row("gemma-4", True, 0.001)]
        self.assertEqual(W.pick(W.table(rows)), "gemma-4")

    def test_cache_hits_count_as_answers_but_not_as_cost_or_latency(self):
        t = W.table([self.row("m", True, 0.002, 500), self.row("m", True, 0.0, 0, hit=True)])["m"]
        self.assertEqual((t["tasks"], t["paid_calls"], t["usd_per_text"], t["p95_ms"]), (2, 1, 0.001, 500))


if __name__ == "__main__":
    unittest.main()
