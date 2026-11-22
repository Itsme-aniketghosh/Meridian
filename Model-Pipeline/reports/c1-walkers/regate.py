"""C1 re-gate at $0: replay the orchestrator on bb6b5f4e from the committed cache (no live calls, no cache writes),
then gate each walk task's cached reply (looked up under its old facts, without `reasons`) against its new facts.
From Data-Pipeline/:
    PYTHONPATH=. python ../Model-Pipeline/reports/c1-walkers/regate.py ../Model-Pipeline/reports/c1-walkers/regate.json
"""
import json
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from meridian import agents, walkers
from meridian.common import CONFIG, handoff, load_config
from meridian.fact_gate import check
from meridian.llm_cache import Cache, key
from meridian.orchestrator import Orchestrator, graph_walk

RUN, MODEL = "bb6b5f4e32a4bee7", "gemini-flash"


class ReadOnly(Cache):
    def put(self, k, v):
        raise RuntimeError("re-gate writes nothing to the cache")


cfg = load_config(CONFIG)
cache, plans = ReadOnly(), []
with closing(sqlite3.connect((handoff(cfg, RUN) / "meridian.sqlite").resolve().as_uri() + "?mode=ro", uri=True)) as db:
    at = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (RUN,)).fetchone()[0]
    teams, people = agents.names(db)
    orch = Orchestrator(llm=None, cache=cache, teams=teams, people=people)
    walk = graph_walk(db, cfg, RUN, at, walkers.QueryCache(handoff(cfg, RUN) / "walk_cache"))
    res = agents.run(db, cfg, RUN, at, llm=None, cache=cache, strict_cache=False, ladder=(MODEL,), workers=8,
                     plan=lambda todo: orch.select(todo, plans, 8, walk))
kinds = Counter(w.task.kind for w in res.written)
other = Counter((w.task.kind, w.source) for w in res.written if w.task.kind != "walk")
rows = []
pv = agents.prompt_version("walk")
for w in (w for w in res.written if w.task.kind == "walk"):
    f = w.task.facts["facts"]
    old = {k: v for k, v in f.items() if k != "reasons"}
    e = cache.get(key(MODEL, pv, old))
    text = agents.parse(e.get("reply"), w.task.facts) if e else None
    before = check(text, {"facts": old}, teams=teams, people=people, vocab=agents.VOCAB) if text else None
    after = check(text, w.task.facts, teams=teams, people=people, vocab=agents.VOCAB) if text else None
    rows.append({"team": f["team"], "reasons": f["reasons"], "cached": e is not None,
                 "before": None if before is None else [(m.kind, m.token) for m in before.misses],
                 "after": None if after is None else [(m.kind, m.token) for m in after.misses],
                 "template_misses": [(m.kind, m.token) for m in w.template_misses],
                 "new_key_cached": cache.get(key(MODEL, pv, f)) is not None})
n = len(rows)
print(f"plans {len(plans)}: {dict(Counter(p.route for p in plans))}, walkers for {sum(p.walk for p in plans)}")
print(f"written {dict(kinds)}; non-walk tasks by source {dict(other)}")
print(f"walk tasks {n}, old reply cached {sum(r['cached'] for r in rows)}")
passed = {when: sum(r[when] == [] for r in rows) for when in ("before", "after")}
print(f"gate passes before (old facts) {passed['before']}/{n}, after (new facts) {passed['after']}/{n}")
flagged, cached = sum(bool(r["template_misses"]) for r in rows), sum(r["new_key_cached"] for r in rows)
print(f"templates the gate flags: {flagged}/{n}; new keys already cached: {cached}")
print("misses after:", Counter(t for r in rows for t in map(tuple, r["after"] or [])))
json.dump(rows, open(sys.argv[1], "w"), indent=1)
