"""The product hand-off test (packets/README.md, Integration tests, Nov 9), the writer's part: on the fixture with
the LLM off, the writer gates every text with fact_gate.check and logs each answer with
tracking.log_llm_call(..., facts_hash=). With the LLM off the answers come from a warm cache (calls with 0 cost);
a template that ships with no LLM attempt isn't a call, so it isn't logged.

    python -m unittest tests.test_handoff_writer -v      # from Model-Pipeline/

It runs here, not only in Data-Pipeline/tests/test_agents.py, because this is the CI job that has mlflow.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mlflow

from model import tracking
from tests.contracts.make_vishwa import fixture


def good(model, prompt):
    """A stand-in LLM whose reply only says facts: the ticket key, or the team."""
    facts = json.loads(prompt.split("Facts:\n", 1)[1])
    said, used = (f"{facts['key']} needs a done condition.", ["key"]) if "key" in facts else \
        (f"Work for {facts['team']}.", ["team"])
    return {"reply": json.dumps({"text": said, "used": used}), "tokens_in": 800, "tokens_out": 90, "cost_usd": 0.0004}


class WriterHandOff(unittest.TestCase):
    def test_the_writer_logs_every_answer_to_meridian_agents_with_the_llm_off(self):
        K, agents, cfg = fixture()
        from meridian.llm_cache import Cache
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
        self.addCleanup(tmp.cleanup)
        cache = Cache(Path(tmp.name) / "cache")
        agents.run(K.build(), cfg, "R2", K.R2[1], llm=good, cache=cache)  # warm the cache, logging nothing
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": f"sqlite:///{tmp.name}/mlflow.db"}):
            with tracking.start_run(agents.AGENT, owner="vishwa", experiment=tracking.AGENTS,
                                    block_run_id="R2") as run:
                result = agents.run(K.build(), cfg, "R2", K.R2[1], cache=cache, log=tracking.log_llm_call)
            table = tracking.load_llm_calls([run.info.run_id])
            metrics = mlflow.MlflowClient().get_run(run.info.run_id).data.metrics
        self.assertEqual(len(table), len(result.written))
        self.assertEqual(set(table["facts_hash"]), {w.facts_hash for w in result.written})
        self.assertTrue(table["cache_hit"].all() and table["gate_pass"].all())
        self.assertEqual(metrics["llm_api_calls"], 0)  # PASS-BARS §5: warm cache, 0 LLM calls
        self.assertEqual({r["facts_hash"] for r in result.rows if r["run_id"] == "R2"} - set(table["facts_hash"]),
                         set())


if __name__ == "__main__":
    unittest.main()
