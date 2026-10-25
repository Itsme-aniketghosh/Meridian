"""Change report · what changed since the last run, and which commit caused it. UPDATE-PLAN.md #5 and #17.

    python -m meridian.report --run-id <run_id> --config meridian.toml                       # first run: everything is added
    python -m meridian.report --run-id <run_id> --config meridian.toml --prev-run <run_id>   # changes since that run

Reads out/<run_id>/ run, git_commit, call_site, call_edge, blast_radius, and with --prev-run that run's call_site,
call_edge, blast_radius. Writes out/<run_id>/changes.json, not a table. Running it again writes the same bytes.

- Grouped by commit (repo, sha, with subject and authored_at from git_commit, never names or emails), so a run that
  batched many pushes still reads "this commit did that".
- Call sites, each site's newest version in either run: added = present now and not before (new, or back after a
  removal); removed = present before, removed now; changed = present in both, a versioned field differs ('was' holds
  the old values). Each goes under its version's effective_sha.
- Call edges, as (repo, caller_path, caller_scope, callee, introduced_sha): a line that only moved keeps its blame,
  so it is neither added nor removed; an edited line is a removed edge and an added one. Added edges go under their
  introduced_sha. A removed edge keeps the introduced_sha it had: the commit that removed it isn't stored (join's
  reverse blame covers call sites only), so removed edges are listed apart, not under a commit.
- Blast radius per (repo, symbol) in either run: before -> after (direct_sites, invocations, files, by_depth); null
  where the run has no row.
"""
import json
from collections import defaultdict
from pathlib import Path

from meridian.common import CONFIG, data_root, handoff, load_config, log, parser, read_jsonl
from meridian.join import VERSIONED, newest

SITE = ("call_site_id", "path", "line", "col", "scope", "symbol", "role")
EDGE = ("caller_path", "caller_scope", "line", "col", "callee_repo", "callee_path", "callee_scope")
BLAST = ("direct_sites", "invocations", "files", "by_depth")
NEED, NEED_PREV = ["run", "git_commit", "call_site", "call_edge", "blast_radius"], ["call_site", "call_edge", "blast_radius"]


def site_changes(before, now):
    """-> [(kind, newest version, {field: old value})]. before, now: {call_site_id: newest version}."""
    out = []
    for sid, s in sorted(now.items()):
        b = before.get(sid)
        was = b is not None and b["status"] == "present"
        if s["status"] == "present" and not was:
            out.append(("added", s, {}))
        elif s["status"] == "removed" and was:
            out.append(("removed", s, {}))
        elif s["status"] == "present" and (diff := {k: b[k] for k in VERSIONED if b[k] != s[k]}):
            out.append(("changed", s, diff))
    return out


def edge_changes(before, now):
    """-> (added, removed) edges. Same identity, different count: the extra ones, in line order."""
    def by_id(edges):
        got = defaultdict(list)
        for e in sorted(edges, key=lambda e: (e["repo"], *(e[k] for k in EDGE))):
            got[(e["repo"], e["caller_path"], e["caller_scope"], e["callee_repo"], e["callee_path"], e["callee_scope"],
                 e["introduced_sha"])].append(e)
        return got
    b, n = by_id(before), by_id(now)
    return ([e for k in sorted(n) for e in n[k][len(b.get(k, [])):]],
            [e for k in sorted(b) for e in b[k][len(n.get(k, [])):]])


def build(run, prev_run, commits, sites, edges, blast, prev):
    """-> the changes.json document. prev: {table: rows} of the previous run, {} on a first run."""
    groups = defaultdict(lambda: {"call_sites": {"added": [], "removed": [], "changed": []}, "call_edges": {"added": []}})
    for kind, s, was in site_changes(newest(prev.get("call_site", [])), newest(sites)):
        groups[(s["repo"], s["effective_sha"])]["call_sites"][kind].append({k: s[k] for k in SITE} | ({"was": was} if was else {}))
    added, removed = edge_changes(prev.get("call_edge", []), edges)
    for e in added:
        groups[(e["repo"], e["introduced_sha"])]["call_edges"]["added"].append({k: e[k] for k in EDGE})
    info = {(c["repo"], c["sha"]): c for c in commits if (c["repo"], c["sha"]) in groups}
    if missing := sorted(set(groups) - set(info)):
        raise SystemExit(f"commits not in git_commit: {missing[:5]}. Rerun meridian.history for this run")
    by_commit = []
    for (repo, sha), g in groups.items():
        for kind, rows in g["call_sites"].items():
            rows.sort(key=lambda s: (s["path"], s["line"], s["col"], s["call_site_id"]))
        by_commit.append({"repo": repo, "sha": sha, "subject": info[(repo, sha)]["subject"],
                          "authored_at": info[(repo, sha)]["authored_at"], **g})
    by_commit.sort(key=lambda c: (c["repo"], c["authored_at"], c["sha"]))
    pick = lambda r: r and {k: json.loads(r[k]) if k == "by_depth" else r[k] for k in BLAST}
    was, now = ({(r["repo"], r["symbol"]): r for r in rows} for rows in (prev.get("blast_radius", []), blast))
    totals = {f"call_sites_{k}": sum(len(c["call_sites"][k]) for c in by_commit) for k in ("added", "removed", "changed")}
    return {"run_id": run["run_id"], "prev_run": prev_run, "snapshot_at": run["snapshot_at"],
            "totals": totals | {"call_edges_added": len(added), "call_edges_removed": len(removed), "commits": len(by_commit)},
            "commits": by_commit,
            "call_edges_removed": [{k: e[k] for k in ("repo", *EDGE, "introduced_sha")} for e in removed],
            "blast_radius": [{"repo": repo, "symbol": sym, "before": pick(was.get((repo, sym))), "after": pick(now.get((repo, sym)))}
                             for repo, sym in sorted(set(was) | set(now))]}


def main(run_id, config=CONFIG, root=None, prev_run=None):
    cfg, root = load_config(config), Path(root or data_root())
    out = handoff(cfg, run_id, root)
    if missing := [t for t in NEED if not (out / f"{t}.jsonl").exists()]:
        raise SystemExit(f"missing in {out}: {', '.join(missing)}. Run history, resolve, join and graph first")
    t = {name: read_jsonl(out / f"{name}.jsonl") for name in NEED}
    prev = {}
    if prev_run:
        pout = handoff(cfg, prev_run, root)
        if missing := [x for x in NEED_PREV if not (pout / f"{x}.jsonl").exists()]:
            raise SystemExit(f"missing in {pout}: {', '.join(missing)}. --prev-run must be a finished run")
        prev = {name: read_jsonl(pout / f"{name}.jsonl") for name in NEED_PREV}
    doc = build(t["run"][0], prev_run, t["git_commit"], t["call_site"], t["call_edge"], t["blast_radius"], prev)
    (out / "changes.json").write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                      encoding="utf-8", newline="\n")

    log(f"changes in {run_id} since {prev_run or 'nothing (first run)'}: " + ", ".join(f"{v:,} {k}" for k, v in doc["totals"].items()))
    for c in doc["commits"]:
        s, e = c["call_sites"], c["call_edges"]
        log(f"  {c['repo']} {c['sha'][:10]} {c['authored_at']}  sites +{len(s['added'])} -{len(s['removed'])} "
            f"~{len(s['changed'])}  edges +{len(e['added'])}  {c['subject'][:72]}")
    if doc["call_edges_removed"]:
        log(f"  {len(doc['call_edges_removed']):,} call edges removed (removing commit not stored, see changes.json)")
    for b in doc["blast_radius"]:
        if b["before"] != b["after"]:
            fmt = lambda x: f"{x['invocations']:,} invocations by depth {json.dumps(x['by_depth'])}" if x else "none"
            log(f"  blast radius {b['repo']} {b['symbol']}: {fmt(b['before'])} -> {fmt(b['after'])}")
    log(f"changes.json -> {out}")
    return doc


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--prev-run", default=None, help="the last reported run, to compare with (none: first run, all added)")
    a = ap.parse_args()
    main(a.run_id, a.config, prev_run=a.prev_run)
