"""LLM bias on the live run's texts (decisions.md, Nov 13): the orchestrator's run replayed from the committed cache.

orchestrator.main as Utkarsh ran it (--models gemini-flash), with the LLM off, strict_cache on (a miss stops it, so 0
calls) and agents.run's log appending each answer's row to --calls, as agents --calls does. The orchestrator itself
has no --calls, and the code is frozen, so this lives here. Then: python -m model.llm_bias --calls <that file>.

    cd Data-Pipeline && MERIDIAN_DATA=~/meridian-block3 \
        python ../Model-Pipeline/packets/chaitali-bias/llm_bias_live.py --run-id bb6b5f4e32a4bee7 --calls /tmp/live_calls.jsonl
"""
import argparse
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "Data-Pipeline"))
from meridian import agents, orchestrator as O, walkers  # noqa: E402
from meridian.common import CONFIG, handoff, load_config  # noqa: E402
from meridian.llm_cache import Cache  # noqa: E402


def replay(run_id, calls, config=CONFIG, ladder=("gemini-flash",), workers=8):
    cfg = load_config(config)
    path = handoff(cfg, run_id) / "meridian.sqlite"
    calls.unlink(missing_ok=True)
    log = agents._tee(None, calls)
    cache, plans = Cache(), []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        at = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (run_id,)).fetchone()[0]
        teams, people = agents.names(db)
        orch = O.Orchestrator(llm=None, cache=cache, teams=teams, people=people)
        walk = O.graph_walk(db, cfg, run_id, at, walkers.QueryCache(handoff(cfg, run_id) / "walk_cache"))
        result = agents.run(db, cfg, run_id, at, llm=None, cache=cache, log=log, strict_cache=True, ladder=ladder,
                            workers=workers, plan=lambda todo: orch.select(todo, plans, workers, walk))
    return plans, result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--calls", type=Path, required=True, help="the jsonl to write (replaced)")
    ap.add_argument("--config", type=Path, default=CONFIG)
    a = ap.parse_args(argv)
    plans, result = replay(a.run_id, a.calls, a.config)
    src = Counter(w.source for w in result.written)
    print(f"{len(plans)} stalls planned {dict(Counter(p.route for p in plans))}; walkers for "
          f"{sum(p.walk for p in plans)}; {src.get('llm', 0)} LLM texts, {src.get('template', 0)} templates; "
          f"by kind {dict(Counter((w.task.kind, w.source) for w in result.written))}")


if __name__ == "__main__":
    main()
