"""Push check · Phase 2 · P1–P9 on a PR, against the last published block, as one markdown comment. Read-only.
See notes/UPDATE-PLAN.md (DAGs, Phases), 04-architecture §10 and KNOWLEDGE-BASE.md, "Push check".

    python -m meridian.pushcheck --base meridian.sqlite --repo owner/name --changed BASE...HEAD --checkout <PR checkout>
    python -m meridian.pushcheck --base meridian.sqlite --repo owner/name --changed a.py b/c.py --checkout <PR checkout>

--changed is a git range (anything with '..', read in --checkout) or a list of repo-relative paths. Prints the comment,
or nothing when there is nothing to say (also when --base doesn't exist: no base map, the check is silent). Exit 0.

How. The block is opened read-only. The push is laid over it in TEMP tables that shadow the block's own (SQLite reads
temp first): the changed files are scanned as the PR leaves them (meridian.scan, same rules), their sites get join's
content IDs, and every site that came or went gets a version at PUSH_AT. Then the doc's own queries (read from
KNOWLEDGE-BASE.md, which tests/test_kb.py proves on its fixture) run twice, as of the base and as of the push:
    P1 Drift            p1_drift: a present site the base didn't have
    P2 Legacy use       p2_legacy_use: a new site that resolves to a deprecation-marked symbol
    P3 Collision        p3_collision on every function the PR edits, and every function with sites in an importer
    P4 New block        derive_blocked_by as of the push minus as of the base
    P5 Ticket can move  p5_ticket_can_move as of the push minus as of the base
    P6 Mismatch         stall_ticket_mismatch as of the push minus as of the base: re-adds a site of a Done ticket
    P7 Risky edit       p7_risky_edit on every file where a call site changed
    P8 Bad key          Python, as the KB says (SQLite has no regex): a commit key that isn't a ticket in the block
    P9 Defect risk      p9_defect_risk on the PR's diff, above [push_check] defect_risk_threshold
Scope (04 §10): the changed files plus every non-test file that imports one (module_import, read in reverse). Over
[push_check] max_scope_files: skipped, said in one line. At most [push_check] max_lines findings are listed.
"""
import argparse
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from meridian.common import CONFIG, HERE, git, load_config, load_settings
from meridian.join import normalized, parse_diff, site_id, with_occurrence
from meridian.scan import PY, module_of, scan_repo, scope, tracked

KB = HERE / "KNOWLEDGE-BASE.md"
# the push's versions sort after every base version. Only in TEMP tables, never written
PUSH_AT = "9999-12-31T23:59:59Z"
PUSH_SHA = "0" * 40  # ... and their effective_sha: the commit is the PR itself, not in the block
MARKER = "<!-- meridian-push-check -->"  # the workflow finds its own comment by this line
NAMES = {"P1": "Drift", "P2": "Legacy use", "P3": "Collision", "P4": "New block", "P5": "Ticket can move",
         "P6": "Mismatch", "P7": "Risky edit", "P8": "Bad key", "P9": "Defect risk"}


def kb_queries():
    """{name: sql} for every named ```sql block in KNOWLEDGE-BASE.md: the same parse as tests/test_kb.py."""
    out = {}
    for body in re.findall(r"```sql\n(.*?)```", KB.read_text(encoding="utf-8"), re.S):
        if m := re.search(r"^-- name: (\S+)", body, re.M):
            out[m.group(1)] = body
    return out


def from_git(checkout, rng):
    """(changed paths, {new path: [edited lines], deleted: None}, numstat rows, [(sha, subject, body)], author
    email)."""
    log_rng = rng.replace("...", "..")  # diff A...B is from the merge base; log A...B would add main's own commits
    changed = [p for p in git(checkout, "diff", "--name-only", "--no-renames", rng).split("\n") if p]
    edited = {}
    for old, (new, hunks) in parse_diff(git(checkout, "diff", "-U0", "--no-renames", "--no-color",
                                            "--no-ext-diff", rng)).items():
        edited[old] = None if new is None else [n for _, _, c, d in hunks
                                                for n in (range(c, c + d) if d else [max(c, 1)])]
    numstat = [line.split("\t", 2)
               for line in git(checkout, "diff", "--numstat", "--no-renames", rng).split("\n") if line]
    numstat = [(p, None if a == "-" else int(a), None if d == "-" else int(d)) for a, d, p in numstat]  # '-': binary
    messages = [m.strip("\n").split("\x1f")
                for m in git(checkout, "log", "--format=%H%x1f%s%x1f%b%x1e", log_rng).split("\x1e")
                if m.strip()]
    author = git(checkout, "log", "-1", "--format=%ae", log_rng).strip().lower()
    return changed, edited, numstat, messages, author


def scopes_of(source, lines):
    """The dotted scope of each line, as scan names it: the node where the line's code starts."""
    root, text = PY.parse(source).root_node, source.split(b"\n")
    out = set()
    for n in lines:
        row = text[n - 1] if n <= len(text) else b""
        col = len(row) - len(row.lstrip())
        out.add(scope(root.descendant_for_point_range((n - 1, col), (n - 1, col))))
    return out


def head_sites(repo, checkout, paths, cfg):
    """({call_site_id: site}, {paths that don't parse}) for the changed files as the PR leaves them: scan's rows, kept
    as join keeps them (edit roles), with join's content ID."""
    with tempfile.TemporaryDirectory() as tmp:
        for p in paths:
            if (checkout / p).is_file():
                (Path(tmp) / p).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(checkout / p, Path(tmp) / p)
        cands, failures, *_ = scan_repo("push", repo, Path(tmp), tracked(cfg).get(repo, {}), cfg["scanner"])
    # shortcut: no resolver at push time, so a look-alike the resolver would drop still counts.
    # Upgrade: resolve the scope
    sites = with_occurrence([c | {"normalized_line": normalized(c["text"])} for c in cands
                             if c["role"] in cfg["scanner"]["edit_roles"]])
    return {site_id(s): s for s in sites}, {f["path"] for f in failures}


def match(head, base):
    """(new, gone): head sites with no base site and back. IDs first: join's ID is the content hash of the line. Then a
    leftover pair with the same (path, scope, symbol) is one site whose line changed, not one new and one removed."""
    new = {i: s for i, s in head.items() if i not in base}
    gone = {i: s for i, s in base.items() if i not in head}
    # shortcut: pairs in line order, not join's git line map.
    # Upgrade if 05-test's replay (push = map(C) − map(C^)) differs
    pairs = defaultdict(lambda: ([], []))
    for side, sites in enumerate((new, gone)):
        for i, s in sorted(sites.items(), key=lambda x: x[1]["line"]):
            pairs[(s["path"], s["scope"], s["symbol"])][side].append(i)
    for a, b in pairs.values():
        for i, j in zip(a, b):
            del new[i], gone[j]
    return new, gone


def rows(db, sql, params=()):
    return [tuple(r) for r in db.execute(sql, params).fetchall()]


def push_check(base, repo, checkout, changed, cfg, settings, rng=None):
    """The comment, '' when there's nothing to say. changed: paths (ignored when rng, a git range, is given)."""
    if not Path(base).is_file() or not any(r["repo"] == repo for d in cfg["datasets"].values()
                                           for r in d.get("repos", [])):
        return ""  # no base map (04 §10), or not a repo meridian.toml watches: silent
    checkout, q = Path(checkout), kb_queries()
    edited, numstat, messages, author = {}, [], [], ""
    if rng:
        changed, edited, numstat, messages, author = from_git(checkout, rng)
    changed = sorted(set(changed))
    db = sqlite3.connect(Path(base).resolve().as_uri() + "?mode=ro", uri=True)  # never writes the block
    try:
        base_run, base_at = db.execute("SELECT run_id, snapshot_at FROM run "
                                       "ORDER BY snapshot_at DESC LIMIT 1").fetchone()
        # the doc's rule for who imports a module (KNOWLEDGE-BASE.md imports_of), for every changed file, not only
        # files with call sites
        importers = sorted({p for f in changed if f.endswith(".py") for (p,) in rows(
            db, "SELECT path FROM module_import WHERE repo = ? AND is_test = 0 "
                "AND (module = ? OR module || '.' || name = ?)", (repo, module_of(f), module_of(f)))} - set(changed))
        head = (f"{MARKER}\n**Meridian push check** · base run `{base_run}` ({base_at}) · "
                f"{len(changed)} changed files, {len(importers)} importers")
        if len(changed) + len(importers) > settings["max_scope_files"]:
            return (f"{head}\n\nSkipped: {len(changed) + len(importers)} files in scope, "
                    f"over {settings['max_scope_files']}.\n")

        # the push, over the block: TEMP tables shadow the block's, so the doc's queries read base + push unchanged
        db.executescript("CREATE TEMP TABLE call_site AS SELECT * FROM main.call_site;"
                         "CREATE TEMP TABLE resolution AS SELECT * FROM main.resolution;"
                         # P9 reads the push only
                         "CREATE TEMP TABLE commit_file AS SELECT * FROM main.commit_file WHERE 0;"
                         "CREATE TEMP TABLE asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT);")
        db.execute("INSERT INTO asof VALUES (?, ?, ?, NULL)", (base_run, base_at, repo))  # this repo, every symbol
        db.executescript(q["setup"])
        db.row_factory = sqlite3.Row
        in_changed = f"path IN ({', '.join('?' * len(changed))})"
        base = {r["call_site_id"]: dict(r) for r in db.execute(
            f"SELECT * FROM site_now WHERE repo = ? AND status = 'present' AND {in_changed}", (repo, *changed))}
        sites, unparsed = head_sites(repo, checkout, changed, cfg)
        base = {i: s for i, s in base.items() if s["path"] not in unparsed}  # join keeps those present too
        site_changed = {s["path"] for i, s in (*sites.items(), *base.items()) if i not in sites or i not in base}  # P7
        new, gone = match(sites, base)
        db.row_factory = None
        cols = [c for (_, c, *_) in rows(db, "PRAGMA main.table_info(call_site)")]
        versions = [s | dict(status="removed") for s in gone.values()]
        for i, s in new.items():
            owner = rows(db, "SELECT owner_team FROM file_owner WHERE repo = ? AND path = ?", (repo, s["path"]))
            fixes = rows(db, "SELECT bug_fixes FROM file_bug_fixes WHERE repo = ? AND path = ?", (repo, s["path"]))
            versions.append(s | dict(call_site_id=i, detection="ast_unresolved",
                                     owner_team=owner[0][0] if owner else "unowned",
                                     bug_fixes=fixes[0][0] if fixes else 0, status="present"))
            # shortcut: no resolver at push time, so a new site resolves to what its symbol resolved to in the base
            # (the most common target). Upgrade with Phase 3's per-file resolve cache
            target = rows(db, "SELECT resolved_to FROM resolution WHERE repo = ? AND symbol = ? "
                              "AND resolved_to IS NOT NULL "
                              "GROUP BY resolved_to ORDER BY count(*) DESC, resolved_to LIMIT 1", (repo, s["symbol"]))
            db.execute("INSERT INTO resolution (run_id, repo, path, line, col, symbol, detection, via, resolved_to, "
                       "resolver) "
                       "VALUES ('push', ?, ?, ?, ?, ?, 'ast_unresolved', 'none', ?, 'push')",
                       (repo, s["path"], s["line"], s["col"], s["symbol"], target[0][0] if target else None))
        for v in versions:
            v |= dict(run_id="push", effective_date=PUSH_AT, effective_sha=PUSH_SHA, known_at=PUSH_AT)
            db.execute(f"INSERT INTO call_site ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [v[c] for c in cols])
        db.executemany("INSERT INTO commit_file (run_id, repo, sha, path, added, deleted) "
                       "VALUES ('push', ?, 'push', ?, ?, ?)", [(repo, *n) for n in numstat])

        def at(when, name, params=None):
            db.execute("UPDATE asof SET at = ?", (when,))
            return rows(db, q[name], params or {})

        before = {n: set(at(base_at, n)) for n in ("derive_blocked_by", "p5_ticket_can_move", "stall_ticket_mismatch")}
        after = {n: set(at(PUSH_AT, n)) for n in before}
        # asof is the push now: removed sites keep their last place
        where = {i: (p, ln, sym) for i, p, ln, sym in rows(db, "SELECT call_site_id, path, line, symbol FROM site_now")}
        loc = lambda i: f"`{where[i][0]}:{where[i][1]}`" if i in where else f"`{i}`"
        found = []  # (check, target, what): push_alert's check, target, evidence
        found += [("P1", f"{p}:{ln}", f"new call site of `{where[i][2]}` at `{p}:{ln}`")
                  for i, p, ln in at(PUSH_AT, "p1_drift", {"base": base_at})]
        found += [("P2", loc(i).strip("`"), f"{loc(i)} calls `{d}`, marked `{m}`")
                  for i, d, m in at(PUSH_AT, "p2_legacy_use", {"base": base_at})]

        # P3: the team of the PR's author, else the repo's team. '' when neither is known: every ticket is
        # another team's
        team = rows(db, "SELECT t.team FROM team_map t "
                        "LEFT JOIN person_alias p ON p.person_id = t.subject AND t.subject_kind = 'person' "
                        "WHERE p.email = ? OR (t.subject_kind = 'repo' AND t.subject = ?) "
                        "ORDER BY t.subject_kind = 'repo', t.team "
                        "LIMIT 1", (author, repo))
        team = team[0][0] if team else ""
        fns = set()
        for p in changed:
            lines = edited.get(p, "all")  # no hunks: an added file, or a path list: every line is edited
            if lines is None or not (checkout / p).is_file():  # deleted: every function with a base site
                fns |= {(p, s["scope"], "edits `{s}` in `{p}`") for s in base.values() if s["path"] == p}
            elif p.endswith(".py"):
                src = (checkout / p).read_bytes()
                fns |= {(p, s, "edits `{s}` in `{p}`")
                        for s in scopes_of(src, range(1, src.count(b"\n") + 2) if lines == "all" else lines)}
        fns |= {(p, s, "`{s}` in `{p}` imports a changed file") for p in importers
                for (s,) in rows(db, "SELECT DISTINCT scope FROM site_now WHERE repo = ? AND path = ?", (repo, p))}
        for p, s, how in sorted(fns):
            found += [("P3", f"{p}:{s}", f"{how.format(s=s, p=p)}: mapped to {k} ({st}), another team's open ticket")
                      for k, st in at(PUSH_AT, "p3_collision", {"path": p, "scope": s, "team": team})]

        # shortcut: P4 sees a new site inside a function others' receivers come from. A new receiver (A's side) needs
        # resolution.receiver_from, which only the resolver writes. Upgrade with Phase 3's per-file resolve cache
        found += [("P4", loc(b).strip("`"), f"{loc(a)} is now blocked by {loc(b)}")
                  for a, b, _ in sorted(after["derive_blocked_by"] - before["derive_blocked_by"])]
        found += [("P5", k, f"removes the last call site of {k}: the ticket can move")
                  for (k,) in sorted(after["p5_ticket_can_move"] - before["p5_ticket_can_move"])]
        found += [("P6", k, f"re-adds {loc(i)}, a call site of {k}, which is done") for _, k, i in
                  sorted(after["stall_ticket_mismatch"] - before["stall_ticket_mismatch"])]
        for p in sorted(site_changed):
            found += [("P7", p,
                       f"changes a call site in `{p}`: {n} bug fixes in the window ({', '.join(json.loads(keys)[:3])}"
                       f"{', …' if n > 3 else ''})") for p, n, keys in at(PUSH_AT, "p7_risky_edit", {"path": p})]

        # P8 in Python, as tickets.commit_tickets reads keys: the subject's, else the body's
        ds = next((d for d in cfg["datasets"].values() if any(r["repo"] == repo for r in d.get("repos", []))), {})
        if ds.get("tracker") == "jira":  # shortcut: GitLab refs (#123) aren't checked; trackers 'none' have no keys
            rx = re.compile(ds.get("key_regex") or cfg["tickets"]["key_regex_generic"])
            known = {k for (k,) in rows(db, "SELECT key FROM ticket")}
            for sha, subject, body in messages:
                found += [("P8", k, f"commit {sha[:10]} names {k}, not a ticket in the snapshot")
                          for k in sorted((set(rx.findall(subject)) or set(rx.findall(body))) - known)]
        if numstat:  # shortcut: a path list has no diff, so no P9 (or P8); the workflow always passes a range
            lines, files, risk = at(PUSH_AT, "p9_defect_risk", {"sha": "push"})[0]
            if risk is not None and risk > settings["defect_risk_threshold"]:
                found.append(("P9", "push",
                              f"{lines} lines × {files} files = {risk}, over {settings['defect_risk_threshold']}"))
    finally:
        db.close()
    if not found:
        return ""
    found.sort(key=lambda f: f[:2])  # the doc's queries have no ORDER BY: same PR, same comment
    shown = [f"- **{c} {NAMES[c]}**: {what}" for c, _, what in found[:settings["max_lines"]]]
    more = len(found) - len(shown)
    return "\n".join([head, "", *shown] + ([f"- … and {more} more"] if more else [])) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", required=True, help="the last published meridian.sqlite")
    ap.add_argument("--repo", required=True, help="owner/name, as in meridian.toml")
    ap.add_argument("--changed", required=True, nargs="+", help="a git range (BASE...HEAD) or repo-relative paths")
    ap.add_argument("--checkout", type=Path, default=Path("."),
                    help="the PR's head, checked out (git reads the range here)")
    ap.add_argument("--config", type=Path, default=CONFIG, help="meridian.toml")
    a = ap.parse_args(argv)
    rng = a.changed[0] if len(a.changed) == 1 and ".." in a.changed[0] else None
    out = push_check(a.base, a.repo, a.checkout, a.changed, load_config(a.config), load_settings("push_check"), rng)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(out)


if __name__ == "__main__":
    main()
