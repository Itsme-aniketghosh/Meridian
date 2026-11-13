"""Graph walkers (GAPS.md gap 17) on KNOWLEDGE-BASE.md's fixture block.

    python -m unittest tests.test_walkers -v      # from Data-Pipeline/

The fixture's fulfillment team is stalled with one reason: its site S_f1 is blocked by S_shared, which platform owns.
platform's own cause, a test gap on shared.client, is in a file fulfillment doesn't own, so fulfillment's context
can't show it. Done when (GAPS 17): with the context builder made to fail, the walkers find it, and a rerun replays
from the cache.
"""
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian import auditor as A  # noqa: E402
from meridian import walkers as W  # noqa: E402
from meridian.common import CONFIG, load_config  # noqa: E402
from meridian.fact_gate import check  # noqa: E402
from tests import test_kb as K  # noqa: E402

CFG = load_config(CONFIG)
RUN, AT = K.R2


def stall_of(db, team):
    return next(s for s in A.block_stalls(db, CFG, AT, RUN) if s.team == team)


class Walkers(unittest.TestCase):
    def setUp(self):
        self.db = K.build()
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_the_context_builder_fails_and_the_walkers_find_the_cause_upstream(self):
        stall = stall_of(self.db, "fulfillment")
        self.assertEqual(set(stall.facts), {1})                      # fulfillment's own reasons: only "upstream"

        def context_builder():
            raise RuntimeError("context builder down")
        facts, source, why = W.fallback(context_builder, db=self.db, cfg=CFG, stall=stall, run_id=RUN, at=AT,
                                        cache=W.QueryCache(self.root))
        self.assertEqual((source, why), ("walkers", "error"))
        f = facts["facts"]
        self.assertEqual(f["walked"], ["fulfillment", "platform"])
        self.assertEqual(f["path"], [["fulfillment", "S_f1", "S_shared", "platform"]])
        self.assertEqual(f["found"]["platform"], {"Test gap": [["shared.client"]]})   # the cause, outside the context

    def test_a_rerun_replays_from_the_cache_with_0_queries(self):
        stall, cache = stall_of(self.db, "fulfillment"), W.QueryCache(self.root)
        first = W.walk(self.db, CFG, stall, RUN, AT, cache)
        again = W.walk(K.build(), CFG, stall, RUN, AT, cache)            # a fresh connection, the same cache
        self.assertGreater(first.ran, 0)
        self.assertEqual(again.ran, 0)
        self.assertEqual(again.facts(), first.facts())

    def test_failure_is_decided_by_code(self):
        self.assertEqual(W.failed(error=ValueError("x")), (True, "error"))
        self.assertEqual(W.failed(result={"files": []}, usable=lambda r: bool(r["files"])), (True, "empty"))
        self.assertEqual(W.failed(result={"files": ["a.py"]}, gate_misses=2), (True, "gate"))
        self.assertEqual(W.failed(result={"files": ["a.py"]}, gate_misses=1), (False, None))
        stall = stall_of(self.db, "fulfillment")
        good = {"facts": {"files": ["a.py"]}}
        self.assertEqual(W.fallback(lambda: good, db=self.db, cfg=CFG, stall=stall, run_id=RUN, at=AT)[1:],
                         ("tool", None))
        self.assertEqual(W.fallback(lambda: good, db=self.db, cfg=CFG, stall=stall, run_id=RUN, at=AT,
                                    gate_misses=2)[1:], ("walkers", "gate"))   # the writer failed the gate twice

    def test_a_tool_that_hangs_counts_as_failed(self):
        stall = stall_of(self.db, "fulfillment")
        t0 = time.monotonic()
        _, source, why = W.fallback(lambda: time.sleep(60), db=self.db, cfg=CFG, stall=stall, run_id=RUN, at=AT,
                                    limits=W.Limits(tool_seconds=0.05))
        self.assertEqual((source, why), ("walkers", "error"))
        self.assertLess(time.monotonic() - t0, 10)                  # never waits for the hung tool

    def test_the_caps_hold(self):
        stall = stall_of(self.db, "fulfillment")
        w = W.walk(self.db, CFG, stall, RUN, AT, limits=W.Limits(teams=1))
        self.assertEqual(w.walked, ["fulfillment"])
        self.assertIn(["platform", "teams"], w.capped)
        w = W.walk(self.db, CFG, stall, RUN, AT, limits=W.Limits(queries=2))
        self.assertEqual(w.queries, 2)
        self.assertTrue(any(c[1] == "queries" for c in w.capped))
        w = W.walk(self.db, CFG, stall, RUN, AT, limits=W.Limits(rows=0))
        self.assertIn(["stall_upstream", "rows"], w.capped)
        slow = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n"
        with self.assertRaises(W.QueryTimeout):
            W.capped(self.db, slow, 10, 0.05)

    def test_the_block_is_read_only(self):
        path = self.root / "b.sqlite"
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE t (x)")
        db = W.open_block(path)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("INSERT INTO t VALUES (1)")

    def test_no_person_is_a_fact(self):
        stall = stall_of(self.db, "checkout")
        f = W.walk(self.db, CFG, stall, RUN, AT).facts()
        self.assertEqual(f["facts"]["found"]["checkout"]["Orphaned owner"], [["cart/pricing.py", 91]])  # no p_ann
        check("", f, teams=["checkout", "platform"], people=["Ann Lee"])   # raises if a fact names a person


if __name__ == "__main__":
    unittest.main()
