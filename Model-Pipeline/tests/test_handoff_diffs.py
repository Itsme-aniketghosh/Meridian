"""The product hand-off test (packets/README.md, Integration tests, Nov 9), the diff drafter's part: on the fixture
with the LLM off, the diff drafter gates every LLM line with fact_gate.check and logs each answer with
tracking.log_llm_call(..., facts_hash=). With the LLM off the answers come from a warm cache (calls with 0 cost). A
codemod draft isn't an LLM call, so it isn't logged.

    python -m unittest tests.test_handoff_diffs -v      # from Model-Pipeline/

It runs here, not only in Data-Pipeline/tests/test_diffs.py, because this is the CI job that has mlflow.
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mlflow

from model import tracking
from tests import mlflow_uri

DATA = Path(__file__).resolve().parents[2] / "Data-Pipeline"


def fixture():
    """Data-Pipeline's test_diffs (its url fixture and stand-in LLM) and meridian.diffs, importable from here."""
    # Data-Pipeline goes last on the path, as in tests/contracts/make_vishwa.py: both pipelines have a package named
    # tests, and a spawned worker re-imports this one's from its copy of sys.path
    before = list(sys.path)
    spec = importlib.util.spec_from_file_location("data_test_diffs", DATA / "tests" / "test_diffs.py")
    T = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(T)  # test_diffs puts Data-Pipeline first on the path: undo that
    sys.path[:] = before + ([str(DATA)] if str(DATA) not in before else [])
    from meridian import diffs
    from meridian.llm_cache import Cache
    return T, diffs, Cache


class DiffDrafterHandOff(unittest.TestCase):
    def test_the_diff_drafter_logs_every_answer_to_meridian_agents_with_the_llm_off(self):
        T, D, Cache = fixture()
        root, cfg = T.url_fixture()
        self.addCleanup(shutil.rmtree, root, True)
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
        self.addCleanup(tmp.cleanup)
        cache = Cache(Path(tmp.name) / "cache")
        T.draft_urls(root, cfg, llm=T.stand_in("    re_path,"), llm_cache=cache)  # warm the cache, logging nothing
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": mlflow_uri(tmp.name)}):
            with tracking.start_run(D.AGENT, owner="utkarsh", experiment=tracking.AGENTS,
                                    block_run_id=T.RUN) as run:
                drafts, _, _, rows = T.draft_urls(root, cfg, llm_cache=cache, log_call=tracking.log_llm_call,
                                                  strict_cache=True)
            table = tracking.load_llm_calls([run.info.run_id])
            metrics = mlflow.MlflowClient().get_run(run.info.run_id).data.metrics
        llm_rows = [r for r in rows if r["source"] == "llm"]
        self.assertEqual(len(llm_rows), 1)                        # c.py's bracketed `url,`
        self.assertEqual(drafts["o/r"][("c.py", 3)][1], "    re_path,\n")
        self.assertEqual(len(table), len(llm_rows))
        self.assertEqual(set(table["facts_hash"]), {r["facts_hash"] for r in llm_rows})
        self.assertTrue(table["cache_hit"].all() and table["gate_pass"].all())
        self.assertEqual(set(table["agent"]), {"diff_drafter"})
        self.assertEqual(metrics["llm_api_calls"], 0)             # PASS-BARS §5: warm cache, 0 LLM calls
        self.assertEqual(len([r for r in rows if r["source"] == "codemod"]), 2)   # a.py, b.py: not LLM calls


if __name__ == "__main__":
    unittest.main()
