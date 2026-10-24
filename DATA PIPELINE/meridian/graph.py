"""Graph · step 1 · stage 3: blocked_by, blast_radius, chain. See 04-architecture §4 and CONTRACTS.md.

    python -m meridian.graph --run-id <run_id> --config meridian.toml

Reads out/<run_id>/ call_site (each site's newest version), call_edge, resolution, ticket_map, ticket. Writes
out/<run_id>/blocked_by.jsonl, blast_radius.jsonl, chain.jsonl. Running it again replaces them with the same rows.

blocked_by A -> B: A is a present call recv.name(...) whose recv = F(...) (resolution.receiver_from). B is a present
call site inside F (depth 1), or inside a function F reaches in depth - 1 resolved calls (call_edge), up to
[graph] blocked_by_max_depth; the shortest depth wins. A and B have different owner teams and neither is 'unowned'
([graph] blocked_by_needs_different_owners). Imports never count: call_edge has none. status = 'unknown' when A or B
is ast_unresolved, else 'resolved'.

blast_radius per (repo, symbol): direct_sites = present call sites (burndown). invocations = call_edge rows that reach
the symbol: depth 1 are the edges at a resolved resolution row of it (its calls, alias calls included), depth n the
calls of a function holding a depth n - 1 one, up to [graph] blast_radius_max_depth. Each edge counts once, at its
shortest depth. files = the distinct caller files of those invocations.

chain, ticket to ticket, through ticket_map's call_site targets:
- depends_on: a key on A and another on B, for a blocked_by A -> B. Evidence 'A>B'.
- duplicates: two keys on the same call site. Evidence: the shared sites.
- collides: two open keys ([rules] done_statuses) on sites in the same function that share no site. Evidence 'repo:path:scope'.
- untracked: per owner team, its present sites no key maps to. from_key = the team, to_key = ''.
in_cycle = 1 on a depends_on chain whose to_key leads back to its from_key.
"""
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from meridian.common import CONFIG, data_root, handoff, load_config, log, parser, read_jsonl, write_jsonl
from meridian.join import newest

KEYS = {
    "blocked_by": ("run_id", "call_site_id", "blocker_id"),
    "blast_radius": ("run_id", "repo", "symbol"),
    "chain": ("run_id", "from_key", "to_key", "type"),
}
NEED = ["call_site", "call_edge", "resolution", "ticket_map", "ticket"]


def fn_of(repo, path, scope):
    return f"{repo}:{path}:{scope}"


def build_blocked_by(run_id, present, res, edges, max_depth, different_owners):
    """-> blocked_by rows, stats. present: the newest version of every present call site."""
    inside = defaultdict(list)
    for s in present:
        inside[fn_of(s["repo"], s["path"], s["scope"])].append(s)
    calls = defaultdict(set)
    for e in edges:
        calls[fn_of(e["repo"], e["caller_path"], e["caller_scope"])].add(fn_of(e["callee_repo"], e["callee_path"], e["callee_scope"]))
    recv = {(r["repo"], r["path"], r["line"], r["col"]): r["receiver_from"] for r in res if r.get("receiver_from")}
    detection = {s["call_site_id"]: s["detection"] for s in present}
    rows, n = [], Counter()
    for a in present:
        f = recv.get((a["repo"], a["path"], a["line"], a["col"]))
        if not f:
            continue
        n["receivers"] += 1
        reach, frontier = {f: 1}, [f]
        for depth in range(2, max_depth + 1):
            frontier = sorted({g for fn in frontier for g in calls.get(fn, ()) if g not in reach})
            reach.update(dict.fromkeys(frontier, depth))
        for fn, depth in sorted(reach.items()):
            for b in inside.get(fn, []):
                if b["call_site_id"] == a["call_site_id"]:
                    continue
                teams = (a["owner_team"], b["owner_team"])
                if different_owners and (teams[0] == teams[1] or "unowned" in teams):
                    n["same_or_unowned_owner"] += 1
                    continue
                unknown = "ast_unresolved" in (detection[a["call_site_id"]], detection[b["call_site_id"]])
                rows.append({"run_id": run_id, "call_site_id": a["call_site_id"], "blocker_id": b["call_site_id"],
                             "via_function": f, "depth": depth, "status": "unknown" if unknown else "resolved"})
    return rows, n


def build_blast_radius(run_id, present, res, edges, max_depth):
    """-> one blast_radius row per (repo, symbol) with a present site or an invocation."""
    symbol_at = {(r["repo"], r["path"], r["line"], r["col"]): r["symbol"] for r in res if r["detection"] == "ast_resolved"}
    into = defaultdict(list)
    for e in edges:
        into[fn_of(e["callee_repo"], e["callee_path"], e["callee_scope"])].append(e)
    key = lambda e: (e["repo"], e["caller_path"], e["line"], e["col"], e["callee_repo"], e["callee_path"], e["callee_scope"])
    reached = defaultdict(dict)  # (repo, symbol) -> {edge: depth}
    for e in edges:
        if sym := symbol_at.get((e["repo"], e["caller_path"], e["line"], e["col"])):
            reached[(e["repo"], sym)][key(e)] = (1, e)
    for got in reached.values():
        level = [e for _, e in got.values()]
        for depth in range(2, max_depth + 1):
            fns = sorted({fn_of(e["repo"], e["caller_path"], e["caller_scope"]) for e in level})
            level = [x for fn in fns for x in into.get(fn, []) if key(x) not in got]
            got.update({key(x): (depth, x) for x in level})
    direct = Counter((s["repo"], s["symbol"]) for s in present)
    rows = []
    for repo, sym in sorted(set(direct) | set(reached)):
        got = reached.get((repo, sym), {})
        by_depth = Counter(d for d, _ in got.values())
        rows.append({"run_id": run_id, "repo": repo, "symbol": sym, "direct_sites": direct[(repo, sym)],
                     "invocations": len(got), "files": len({e["caller_path"] for _, e in got.values()}),
                     "by_depth": json.dumps({str(d): by_depth[d] for d in range(1, max_depth + 1)})})
    return rows


def in_cycle(edges):
    """{(from, to)}: the edges of a directed graph that lie on a cycle (to leads back to from)."""
    out = defaultdict(set)
    for a, b in edges:
        out[a].add(b)

    def reaches(start, goal):
        seen, todo = set(), [start]
        while todo:
            x = todo.pop()
            if x == goal:
                return True
            if x not in seen:
                seen.add(x)
                todo += out[x]
        return False
    return {(a, b) for a, b in edges if reaches(b, a)}


def build_chain(run_id, sites, present, blocked, tmap, tickets, done):
    """-> chain rows. sites: newest version of every site, present or removed."""
    keys_on = defaultdict(set)
    for m in tmap:
        if m["target_kind"] == "call_site":
            keys_on[m["target"]].add(m["key"])
    ev = defaultdict(set)  # (from_key, to_key, type) -> evidence
    for b in blocked:
        for ka in keys_on.get(b["call_site_id"], ()):
            for kb in keys_on.get(b["blocker_id"], ()):
                if ka != kb:
                    ev[(ka, kb, "depends_on")].add(f"{b['call_site_id']}>{b['blocker_id']}")
    for sid, ks in keys_on.items():
        for ka, kb in combinations(sorted(ks), 2):
            ev[(ka, kb, "duplicates")].add(sid)
    open_ = {t["key"] for t in tickets if t["status"] not in done}
    on_fn = defaultdict(set)
    for sid, ks in keys_on.items():
        if s := sites.get(sid):
            on_fn[fn_of(s["repo"], s["path"], s["scope"])] |= ks & open_
    for fn, ks in on_fn.items():
        for ka, kb in combinations(sorted(ks), 2):
            if (ka, kb, "duplicates") not in ev:  # sharing a site is a duplicate, not a collision
                ev[(ka, kb, "collides")].add(fn)
    for s in present:
        if s["call_site_id"] not in keys_on:
            ev[(s["owner_team"], "", "untracked")].add(s["call_site_id"])
    cyclic = in_cycle([(a, b) for a, b, t in ev if t == "depends_on"])
    return [{"run_id": run_id, "from_key": a, "to_key": b, "type": t, "evidence": json.dumps(sorted(e)),
             "in_cycle": int(t == "depends_on" and (a, b) in cyclic)} for (a, b, t), e in ev.items()]


def main(run_id, config=CONFIG, root=None):
    cfg = load_config(config)
    out = handoff(cfg, run_id, Path(root or data_root()))
    if missing := [t for t in NEED if not (out / f"{t}.jsonl").exists()]:
        raise SystemExit(f"missing in {out}: {', '.join(missing)}. Run resolve, history and join first")
    t = {name: read_jsonl(out / f"{name}.jsonl") for name in NEED}
    g = cfg["graph"]
    sites = newest(t["call_site"])
    present = [s for _, s in sorted(sites.items()) if s["status"] == "present"]

    blocked, n = build_blocked_by(run_id, present, t["resolution"], t["call_edge"], g["blocked_by_max_depth"],
                                  g["blocked_by_needs_different_owners"])
    blast = build_blast_radius(run_id, present, t["resolution"], t["call_edge"], g["blast_radius_max_depth"])
    chain = build_chain(run_id, sites, present, blocked, t["ticket_map"], t["ticket"], set(cfg["rules"]["done_statuses"]))

    for name, rows in (("blocked_by", blocked), ("blast_radius", blast), ("chain", chain)):
        write_jsonl(out / f"{name}.jsonl", rows, KEYS[name])
    log(f"{'blocked_by':45} {len(blocked):,} rows · {n['receivers']:,} present sites with a receiver_from · "
        f"{n['same_or_unowned_owner']:,} same or unowned owner, left out"
        + "".join(f" · {v:,} {k}" for k, v in sorted(Counter(r["status"] for r in blocked).items())))
    width = max([len(r["repo"] + r["symbol"]) + 1 for r in blast] + [43])
    for r in blast:
        log(f"  {r['repo'] + ' ' + r['symbol']:{width}} {r['direct_sites']:>6,} sites {r['invocations']:>6,} invocations "
            f"{r['files']:>5,} files  by depth {r['by_depth']}")
    log(f"{'chain':45} {len(chain):,} rows · " + ", ".join(f"{v:,} {k}" for k, v in sorted(Counter(r["type"] for r in chain).items()))
        + f" · {sum(r['in_cycle'] for r in chain):,} in a cycle")
    log(f"{len(blocked):,} blocked_by · {len(blast):,} blast_radius · {len(chain):,} chain -> {out}")
    return blocked, blast, chain


if __name__ == "__main__":
    a = parser(__doc__).parse_args()
    main(a.run_id, a.config)
