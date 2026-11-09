"""The product hand-off test (packets/README.md, Integration tests, Nov 9), the writer's part: on the fixture with
the LLM off, the writer gates every text with fact_gate.check and logs it with tracking.log_llm_call(..., facts_hash=).

    python -m unittest tests.test_handoff_writer -v      # from Model-Pipeline/

It runs here, not only in Data-Pipeline/tests/test_agents.py, because this is the CI job that has mlflow.
"""
import os
import tempfile
import unittest
from unittest import mock

from model import tracking
from tests.contracts.make_vishwa import fixture


class WriterHandOff(unittest.TestCase):
    def test_the_writer_logs_every_text_to_meridian_agents_with_the_llm_off(self):
        K, agents, cfg = fixture()
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": f"sqlite:///{tmp.name}/mlflow.db"}):
            with tracking.start_run(agents.AGENT, owner="vishwa", experiment=tracking.AGENTS,
                                    block_run_id="R2") as run:
                written, rows = agents.run(K.build(), cfg, "R2", K.R2[1], log=tracking.log_llm_call)
            table = tracking.load_llm_calls([run.info.run_id])
        self.assertEqual(len(table), len(written))
        self.assertEqual(set(table["facts_hash"]), {w.facts_hash for w in written})
        self.assertEqual(set(table["source"]), {"template"})
        self.assertTrue(table["gate_pass"].all())
        self.assertEqual({r["facts_hash"] for r in rows if r["run_id"] == "R2"} - set(table["facts_hash"]), set())


if __name__ == "__main__":
    unittest.main()
