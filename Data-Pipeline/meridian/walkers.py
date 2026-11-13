"""Graph walkers (GAPS.md gap 17): the orchestrator's last resort when its tools fail.

A stalled team's context (meridian.context) holds only its own files, so a cause somewhere else, such as an upstream
team whose own migration is stuck, is out of sight. A walker skips the tools and queries the code graph itself:
KNOWLEDGE-BASE.md's named stall queries, as of the stall's moment (meridian.auditor.point). It starts at the stalled
team and follows `blocked_by` to each team that blocks it, up to `depth` hops. What it finds goes to the writer as
facts (Contract 5), and the writer's fact gate still checks every text: the walkers ignore the tools, never the gate.

    w = walk(db, cfg, stall, run_id, at, cache=QueryCache(path))       # one stall
    facts, source, why = fallback(tool, db=..., cfg=..., stall=..., run_id=..., at=..., cache=...)

"Failed" is decided by code, never by the orchestrator (failed()): the tool raises or times out, returns nothing
usable, or the writer's text has failed the fact gate twice.

Guardrails (LIMITS): the block is opened read-only (open_block); each query is capped in time and rows; queries and
teams walked are capped per stall; only named KNOWLEDGE-BASE.md queries run, never SQL written on the fly. Every
query's result is cached by (run_id, moment, repo, symbol, query), so a rerun makes 0 queries and gives the same facts.
No person is a fact: stall_orphaned_owner's person id is dropped.

Built for Aniket's orchestrator (gap 15), which wires `fallback` in. Utkarsh owns it, with the graph.
"""
import hashlib
import json
import sqlite3
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path

from meridian.auditor import NEEDS, REASONS, point, unknown
from meridian.llm_cache import canonical
from meridian.pushcheck import kb_queries


@dataclass(frozen=True)
class Limits:
    rows: int = 200          # rows kept per query; more is marked truncated
    seconds: float = 2.0     # wall time per query; longer is interrupted and marked
    queries: int = 24        # query lookups per stall, cached or not
    teams: int = 3           # teams walked per stall, the stalled one included
    depth: int = 2           # blocked_by hops from the stalled team
    tool_seconds: float = 30.0   # how long fallback() waits for the tool before it counts as failed


LIMITS = Limits()
DROP = {"stall_orphaned_owner": {1}}   # evidence columns never kept: the top person's id


class QueryTimeout(RuntimeError):
    pass


def open_block(path):
    """The block, read-only: a walker can't write to it."""
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)


def capped(db, sql, rows, seconds):
    """(up to `rows` rows, truncated?) of one query, interrupted after `seconds` (QueryTimeout)."""
    deadline = time.monotonic() + seconds
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = db.execute(sql)
        got = cur.fetchmany(rows + 1)
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise QueryTimeout(f"over {seconds} s") from None
        raise
    finally:
        db.set_progress_handler(None, 0)
    return [list(r) for r in got[:rows]], len(got) > rows


class QueryCache:
    """One JSON file per (run_id, moment, repo, symbol, query): <root>/<run_id>/<key>.json. A dict works too."""

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, run_id, k):
        return self.root / str(run_id) / f"{k}.json"

    def get(self, run_id, k):
        p = self._path(run_id, k)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def put(self, run_id, k, value):
        p = self._path(run_id, k)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


@dataclass
class Walk:
    stall_id: str
    team: str
    found: dict = field(default_factory=dict)    # {team: {reason label: [evidence rows]}}
    path: list = field(default_factory=list)     # [[from team, its site, blocker site, to team]]
    walked: list = field(default_factory=list)   # teams, in the order walked
    queries: int = 0                             # query lookups, cached or not
    ran: int = 0                                 # queries actually run against the block
    capped: list = field(default_factory=list)   # [[query, "rows" | "time" | "queries" | "teams"]]

    def facts(self):
        """Contract 5's facts object for the writer: only what the walk found, with where each part came from."""
        f = {"team": self.team, "walked": self.walked, "path": self.path,
             "found": {t: r for t, r in self.found.items() if r}}
        src = {"team": f"stall:{self.stall_id}", "walked": "walkers", "path": "kb:stall_upstream (blocked_by)",
               "found": "kb:" + ",".join(name for _, name in REASONS.values())}
        return {"facts": f, "source": src}


def _key(run_id, at, repo, symbol, name, sql):
    return hashlib.sha256(canonical({"run_id": run_id, "at": at, "repo": repo, "symbol": symbol, "query": name,
                                     "sql": hashlib.sha256(sql.encode()).hexdigest()}).encode()).hexdigest()


def walk(db, cfg, stall, run_id, at, cache=None, limits=LIMITS, q=None):
    """Walk the graph from one stall (an auditor Stall: task_id, repo, symbol, team). Read-only, capped, cached."""
    q = q or kb_queries()
    cache = {} if cache is None else cache
    point(db, at, stall.repo, stall.symbol, run_id, q)
    hidden = set(unknown(db, NEEDS))
    w = Walk(stall.task_id, stall.team)
    results = {}

    def rows_of(name):
        if name in results:
            return results[name]
        if w.queries >= limits.queries:
            w.capped.append([name, "queries"])
            return None
        w.queries += 1
        k = _key(run_id, at, stall.repo, stall.symbol, name, q[name])
        hit = cache.get(run_id, k) if isinstance(cache, QueryCache) else cache.get(k)
        if hit is None:
            try:
                got, truncated = capped(db, q[name], limits.rows, limits.seconds)
                hit = {"rows": got, "truncated": truncated, "timeout": False}
            except QueryTimeout:
                hit = {"rows": [], "truncated": False, "timeout": True}
            w.ran += 1
            if isinstance(cache, QueryCache):
                cache.put(run_id, k, hit)
            else:
                cache[k] = hit
        if hit["truncated"]:
            w.capped.append([name, "rows"])
        if hit["timeout"]:
            w.capped.append([name, "time"])
        results[name] = hit["rows"]
        return results[name]

    frontier, seen = [stall.team], {stall.team}
    for _ in range(limits.depth + 1):
        nxt = []
        for team in frontier:
            if len(w.walked) >= limits.teams:
                w.capped.append([team, "teams"])
                continue
            w.walked.append(team)
            found = {}
            for n, (label, name) in sorted(REASONS.items()):
                if n in hidden:
                    continue
                rows = rows_of(name)
                if rows is None:
                    continue
                mine = [[v for i, v in enumerate(r[1:]) if i not in DROP.get(name, ())] for r in rows if r[0] == team]
                if mine:
                    found[label] = mine
                if name == "stall_upstream":
                    for site, blocker_site, blocker_team in (r[1:4] for r in rows if r[0] == team):
                        w.path.append([team, site, blocker_site, blocker_team])
                        if blocker_team not in seen:
                            seen.add(blocker_team)
                            nxt.append(blocker_team)
            w.found[team] = found
        frontier = nxt
        if not frontier:
            break
    return w


def failed(*, error=None, result=None, gate_misses=0, usable=None):
    """(failed?, why), by code: the tool raised or timed out ('error'), gave nothing usable ('empty'), or the writer's
    text failed the fact gate twice ('gate'). usable(result) says what usable means for this tool (default: truthy)."""
    if error is not None:
        return True, "error"
    if gate_misses >= 2:
        return True, "gate"
    if not (usable(result) if usable else bool(result)):
        return True, "empty"
    return False, None


def fallback(tool, *, db, cfg, stall, run_id, at, cache=None, gate_misses=0, usable=None, limits=LIMITS, q=None):
    """(facts, 'tool' | 'walkers', why): the tool's result, or the walkers' facts when the tool failed."""
    error = result = None
    if gate_misses < 2:
        out = {}

        def run():
            try:
                out["result"] = tool()
            except Exception as e:      # any tool failure is the walkers' cue, never the run's end
                out["error"] = e
        th = threading.Thread(target=run, daemon=True)   # a hung tool is left behind, never waited for
        th.start()
        th.join(limits.tool_seconds)
        error = "timeout" if th.is_alive() else out.get("error")
        result = out.get("result")
    bad, why = failed(error=error, result=result, gate_misses=gate_misses, usable=usable)
    if not bad:
        return result, "tool", None
    return walk(db, cfg, stall, run_id, at, cache, limits, q).facts(), "walkers", why
