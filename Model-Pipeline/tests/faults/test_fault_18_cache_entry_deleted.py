"""Fault 18: A cache entry deleted.
Expected: that one task calls the LLM again; everything else is a hit.
Why: the cache key is right and nothing else depends on it."""
import tempfile
import unittest

from tests.contracts.make_vishwa import fixture
from tests.test_handoff_writer import good


class CacheEntryDeleted(unittest.TestCase):
    def test_only_that_task_calls_again(self):
        K, agents, cfg = fixture()
        from meridian.llm_cache import Cache, key
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = Cache(tmp.name)
        first = agents.run(K.build(), cfg, "R2", K.R2[1], llm=good, cache=cache)  # warm the cache
        gone = next(w for w in first.written if w.task.kind == "rewrite")
        cache.path(key(agents.SMALL, agents.prompt_version("rewrite"), gone.task.facts["facts"])).unlink()
        prompts, logged = [], []

        def llm(model, prompt):
            prompts.append(prompt)
            return good(model, prompt)
        again = agents.run(K.build(), cfg, "R2", K.R2[1], llm=llm, cache=cache, log=lambda **kw: logged.append(kw))
        self.assertEqual(prompts, [agents.prompt(gone.task)])
        self.assertEqual([c["facts_hash"] for c in logged if not c["cache_hit"]], [gone.facts_hash])
        self.assertEqual(len(logged), len(again.written))  # one answer per task: the rest are hits
        self.assertEqual([(w.task.target, w.text) for w in again.written],
                         [(w.task.target, w.text) for w in first.written])


if __name__ == "__main__":
    unittest.main()
