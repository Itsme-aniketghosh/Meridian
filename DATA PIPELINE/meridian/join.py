"""Join · step 1 · stage 2: call_site, ticket_map, coverage. See 04-architecture §3 and CONTRACTS.md.

    python -m meridian.join --run-id <run_id> --config meridian.toml                       # first run: every site is new
    python -m meridian.join --run-id <run_id> --config meridian.toml --prev-run <run_id>   # match to the last run

Reads out/<run_id>/ (candidate, resolution, file_owner, file_bug_fixes, ticket, commit_ticket, commit_file, git_commit,
run, parse_failure), the snapshot checkouts, and with --prev-run that run's out/<prev>/call_site.jsonl and run.jsonl.
Writes out/<run_id>/call_site.jsonl, ticket_map.jsonl, coverage.jsonl. Running it again for the same run replaces its
own files with the same rows.

call_site: a line someone must edit (04-architecture, Definitions). One row per candidate whose role is in
[scanner] edit_roles, unless the resolver dropped it (a look-alike). ast_unresolved rows stay, counted. Alias calls
(_("...")) are blast radius, not call sites. Keyword-argument and parameter names (role reference) stay in candidate.
- call_site_id = h([join] call_site_id fields): repo, symbol, path, scope, the normalized line, and its occurrence
  among sites with the same other fields (0, 1, ... in line, col order). Normalized = all whitespace removed.
- Append-only, across runs: a version is written only when something about the site changed. call_site.jsonl holds
  every version so far (the previous runs' rows keep their run_id), so "now" is each site's newest version.
- Matching to the last run, in [join] match_order: (1) git's line map from the last run's SHA to this one, renames
  followed: an unchanged line lands at its new path and line; (2) else the same (repo, symbol, scope, normalized
  line), in occurrence order, anywhere in the repo (a moved line, a split file). Unmatched last-run sites are written
  as removed, unmatched new ones get a new ID.
- effective_date: the first first-parent commit where the version became true. Present: blame of the line at this
  run's SHA (--first-parent -w). Removed: the first-parent commit after the last one that still had the line
  (reverse blame). Dates are that commit's authored_at from git_commit.
- owner_team from file_owner ('unowned' if the file has no row), bug_fixes from file_bug_fixes (0 if none).

ticket_map: every ticket gets rows, all hits kept, no fuzzy matching ([join] fuzzy_ticket_matching must be false).
- commit_key: every file a commit carrying the key touched, if it still exists at the pinned SHA. Target 'repo:path'.
- path: a token in the summary or description that is exactly a file path at the pinned SHA.
- stack_trace: Java/Scala frames 'at pkg.Class.m(File.scala:12)' -> the one file ending in pkg/File.scala; Python
  frames 'File ".../pkg/mod.py", line 12' -> the one longest repo path the frame ends with. Several candidates is
  not a match (counted). A frame on a call site's line also maps to that call site.
- symbol: a tracked symbol's dotted name (module.name) in the text -> every present call site of it in the dataset.
- No hit -> one 'unmapped' row. Tickets of a dataset with no code in the snapshot (GitLab is Ruby) are all unmapped.

coverage: what we missed, as counts (CONTRACTS.md rule 9). One row per metric, the note says what it means.
"""
import hashlib, json, re
from collections import Counter, defaultdict
from pathlib import Path

from meridian.common import CONFIG, data_root, git, handoff, load_config, log, parser, read_jsonl, write_jsonl
from meridian.scan import files as scan_files

KEYS = {
    "call_site": ("call_site_id", "run_id"),
    "ticket_map": ("run_id", "key", "target_kind", "target", "method"),
    "coverage": ("run_id", "metric"),
}
ID_FIELDS = ["repo", "symbol", "path", "scope", "normalized_line", "occurrence"]
MATCH_ORDER = ["git_line_map_with_renames", "same_repo_symbol_scope_normalized_line"]
# fields that make a new version when they change; run_id, known_at and effective_date describe the version itself
VERSIONED = ("repo", "path", "line", "col", "scope", "symbol", "role", "detection", "junk", "in_except",
             "owner_team", "bug_fixes", "status")
JAVA_FRAME = re.compile(r"\bat\s+((?:[\w$]+\.)+)[\w$<>]+\(([\w$]+\.(?:java|scala)):(\d+)\)")
PY_FRAME = re.compile(r'File "([^"\n]+\.py)", line (\d+)')
PATH_TOKEN = re.compile(r"[\w./-]+\.\w+")


def h(*fields):
    return hashlib.sha256("\x1f".join(fields).encode()).hexdigest()[:16]


def normalized(text):
    return re.sub(r"\s+", "", text)


def check_settings(cfg):
    j = cfg["join"]
    if j["call_site_id"] != ID_FIELDS or j["normalize_line"] != "strip_whitespace" or j["match_order"] != MATCH_ORDER:
        raise SystemExit(f"[join] asks for what the code doesn't do: the code does call_site_id = {ID_FIELDS}, "
                         f"normalize_line = 'strip_whitespace', match_order = {MATCH_ORDER}")
    if j["fuzzy_ticket_matching"]:
        raise SystemExit("[join] fuzzy_ticket_matching = true: 04-architecture rules it out, the code has none")


# ───────────── call sites ─────────────

def current_sites(cands, res, edit_roles):
    """Sites at this run's SHA, before IDs: candidates with an edit role that the resolver didn't drop."""
    by_key = {(r["repo"], r["path"], r["line"], r["col"]): r for r in res}
    sites, n = [], Counter()
    for c in cands:
        r = by_key.get((c["repo"], c["path"], c["line"], c["col"]))
        if r is None:
            raise SystemExit(f"no resolution row for candidate {c['repo']}:{c['path']}:{c['line']}:{c['col']}: rerun meridian.resolve")
        if c["role"] not in edit_roles:
            n["not_edit_role"] += 1
            continue
        if r["detection"] == "dropped":
            n["dropped"] += 1
            continue
        sites.append({k: c[k] for k in ("repo", "path", "line", "col", "scope", "symbol", "role", "junk", "in_except")}
                     | {"detection": r["detection"], "normalized_line": normalized(c["text"])})
    return sorted(sites, key=lambda s: (s["repo"], s["path"], s["line"], s["col"])), n


def with_occurrence(sites):
    """occurrence: the site's place among sites with the same repo, symbol, path, scope and normalized line."""
    seen = Counter()
    for s in sites:  # already in (repo, path, line, col) order
        k = (s["repo"], s["symbol"], s["path"], s["scope"], s["normalized_line"])
        s["occurrence"] = seen[k]
        seen[k] += 1
    return sites


def site_id(s):
    return h(*(str(s[f]) for f in ID_FIELDS))


def parse_diff(text):
    """git diff -M -U0 -> {old_path: (new_path or None, [(old_start, old_len, new_start, new_len)])}."""
    files, cur = {}, None
    strip = lambda p: p.rstrip("\t")  # git ends a path holding a space with a tab

    def done():
        if cur and cur["old"]:  # added files have no old path: nothing of the last run lived there
            files[cur["old"]] = (cur["new"], cur["hunks"])

    for line in text.split("\n"):
        if line.startswith("diff --git "):
            done()
            cur = {"old": None, "new": None, "hunks": [], "headers": True}
        elif cur is None:
            continue
        elif cur["headers"] and line.startswith("rename from "):
            cur["old"] = strip(line[len("rename from "):])
        elif cur["headers"] and line.startswith("rename to "):
            cur["new"] = strip(line[len("rename to "):])
        elif cur["headers"] and line.startswith("--- "):
            p = strip(line[4:])
            cur["old"] = cur["old"] or (None if p == "/dev/null" else p[2:])
        elif cur["headers"] and line.startswith("+++ "):
            p = strip(line[4:])
            cur["new"] = cur["new"] or (None if p == "/dev/null" else p[2:])
        elif line.startswith("@@ "):
            cur["headers"] = False  # from here on '--- x' is a deleted line '-- x', not a header
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            cur["hunks"].append((int(m[1]), int(m[2] or 1), int(m[3]), int(m[4] or 1)))
    done()
    return files


def map_line(diff, path, line):
    """Where an unchanged line of the old SHA sits at the new one, or None if it changed or went away."""
    if path not in diff:
        return path, line
    new_path, hunks = diff[path]
    if new_path is None:
        return None
    offset = 0
    for a, b, c, d in hunks:
        if b == 0:  # pure insertion after old line a
            if line <= a:
                break
        else:
            if line < a:
                break
            if line < a + b:
                return None
        offset += d - b
    return new_path, line + offset


def line_map(co, old_sha, new_sha):
    text = git(co, "diff", "-M", "-U0", "--no-color", "--no-ext-diff", old_sha, new_sha)
    return parse_diff(text)


def porcelain_shas(text):
    """git blame --porcelain -> {final line: sha}."""
    got = {}
    for line in text.split("\n"):
        m = re.match(r"([0-9a-f]{40}) \d+ (\d+)", line)
        if m:
            got[int(m[2])] = m[1]
    return got


def blame(co, rev, path, lines, reverse=False):
    """{line: sha}: the first-parent commit that made each line as it is at rev (or, reverse, the last that had it)."""
    ranges = [a for n in sorted(set(lines)) for a in ("-L", f"{n},{n}")]
    args = ["blame", "--porcelain", "--first-parent", "-w", *(["--reverse"] if reverse else []), *ranges, rev, "--", path]
    return porcelain_shas(git(co, *args))


def match(prev, now, diff):
    """prev: last run's present sites (with call_site_id); now: this run's sites. -> {index in now: id}, unmatched prev."""
    taken, ids = set(), {}
    at = {(s["path"], s["line"], s["col"], s["symbol"]): i for i, s in enumerate(now)}
    left = []
    for p in prev:  # 1. git line map, renames followed
        to = map_line(diff, p["path"], p["line"])
        i = at.get((to[0], to[1], p["col"], p["symbol"])) if to else None
        if i is not None and i not in taken:
            ids[i] = p["call_site_id"]
            taken.add(i)
        else:
            left.append(p)
    pool = defaultdict(list)  # 2. same repo, symbol, scope, normalized line, in occurrence order
    for i, s in enumerate(now):
        if i not in taken:
            pool[(s["symbol"], s["scope"], s["normalized_line"])].append(i)
    gone = []
    for p in sorted(left, key=lambda p: (p["path"], p["line"], p["col"])):
        q = pool.get((p["symbol"], p["scope"], p["normalized_line"]))
        if q:
            i = q.pop(0)
            ids[i] = p["call_site_id"]
            taken.add(i)
        else:
            gone.append(p)
    return ids, gone


def newest(history):
    """{call_site_id: newest version} over every version written so far."""
    last = {}
    for r in sorted(history, key=lambda r: r["known_at"]):
        last[r["call_site_id"]] = r
    return last


def build_sites(run_id, run, prev, sites, owner, fixes, commits, checkout):
    """-> (every call_site version so far, plus this run's), stats."""
    snapshot_at, shas = run["snapshot_at"], json.loads(run["repo_shas"])
    history = prev["history"] if prev else []
    last = newest(history)
    by_repo = defaultdict(list)
    for s in sites:
        by_repo[s["repo"]].append(s)
    date = {(c["repo"], c["sha"]): c["authored_at"] for c in commits}
    fp_seq = {(c["repo"], c["seq"]): c["sha"] for c in commits if c["first_parent"]}
    seq_of = {(c["repo"], c["sha"]): c["seq"] for c in commits if c["first_parent"]}
    written, n = [], Counter()
    for repo in sorted(set(by_repo) | {r["repo"] for r in last.values()}):
        co, now = checkout(repo), by_repo.get(repo, [])
        was = [r | {"normalized_line": normalized(prev["text"][(repo, r["path"], r["line"], r["col"])])}
               for r in last.values() if r["repo"] == repo and r["status"] == "present"]
        ids, gone = {}, []
        if was:
            old_sha = json.loads(prev["run"]["repo_shas"])[repo]
            ids, gone = match(was, now, line_map(co, old_sha, shas[repo]))
        used = set(last) | set(ids.values())
        for i, s in enumerate(now):  # new sites: their own ID, bumped past any ID another site already holds
            if i in ids:
                continue
            sid = site_id(s)
            while sid in used and not (sid in last and last[sid]["status"] == "removed"):
                s["occurrence"] += 1
                sid = site_id(s)
            ids[i] = sid
            used.add(sid)
        by_path = defaultdict(list)
        for i, s in enumerate(now):
            by_path[s["path"]].append(s["line"])
        made = {p: blame(co, shas[repo], p, ls) for p, ls in sorted(by_path.items())}
        for i, s in enumerate(now):
            sha = made[s["path"]][s["line"]]
            if (repo, sha) not in date:
                raise SystemExit(f"{repo}:{s['path']}:{s['line']}: blame gives {sha}, which git_commit doesn't have")
            row = {k: s[k] for k in VERSIONED if k in s} | {
                "call_site_id": ids[i], "run_id": run_id, "status": "present", "known_at": snapshot_at,
                "owner_team": owner.get((repo, s["path"]), "unowned"), "bug_fixes": fixes.get((repo, s["path"]), 0),
                "effective_date": date[(repo, sha)]}
            old = last.get(ids[i])
            if old and all(old[k] == row[k] for k in VERSIONED):
                n["unchanged"] += 1
                continue
            n["new" if old is None else "reappeared" if old["status"] == "removed" else "changed"] += 1
            written.append(row)
        if gone:
            old_sha = json.loads(prev["run"]["repo_shas"])[repo]
            for path in sorted({p["path"] for p in gone}):
                ps = [p for p in gone if p["path"] == path]
                still = blame(co, f"{old_sha}..{shas[repo]}", path, [p["line"] for p in ps], reverse=True)
                for p in ps:
                    nxt = fp_seq.get((repo, seq_of.get((repo, still[p["line"]]), -2) + 1))
                    written.append({**{k: p[k] for k in VERSIONED}, "call_site_id": p["call_site_id"], "run_id": run_id,
                                    "status": "removed", "known_at": snapshot_at,
                                    "effective_date": date[(repo, nxt or shas[repo])]})
                    n["removed"] += 1
        n["matched"] += len([i for i in ids if ids[i] in last and last[ids[i]]["status"] == "present"])
        n["prev_present"] += len(was)
    return history + written, n


# ───────────── ticket map ─────────────

def suffix_index(paths):
    by_name = defaultdict(list)
    for p in paths:
        by_name[p.rsplit("/", 1)[-1]].append(p)
    return by_name


def frames(text, by_name):
    """Stack frames in a ticket's text -> [(path, line)] that name exactly one file. Also the count of ambiguous ones."""
    hits, unsure = [], 0
    for pkg, name, ln in JAVA_FRAME.findall(text):
        tail = "/".join(pkg.rstrip(".").split(".")[:-1] + [name])  # pkg.Class. -> pkg/File.scala
        found = [p for p in by_name.get(name, []) if p == tail or p.endswith("/" + tail)]
        if len(found) == 1:
            hits.append((found[0], int(ln)))
        elif found:
            unsure += 1
    for frame, ln in PY_FRAME.findall(text):
        frame = frame.replace("\\", "/")
        found = [p for p in by_name.get(frame.rsplit("/", 1)[-1], []) if frame == p or frame.endswith("/" + p)]
        if found:
            best = max(len(p) for p in found)
            longest = [p for p in found if len(p) == best]
            if len(longest) == 1:
                hits.append((longest[0], int(ln)))
            else:
                unsure += 1
    return hits, unsure


def build_ticket_map(run_id, cfg, tickets, commit_ticket, commit_file, head_paths, sites_now):
    """-> ticket_map rows, stats. head_paths: {repo: set of paths at the pinned SHA}."""
    datasets = cfg["datasets"]
    jira_ds = {ds: re.compile(d["key_regex"]) for ds, d in datasets.items() if d.get("tracker") == "jira"}
    repos_of = {ds: [r["repo"] for r in d.get("repos", [])] for ds, d in datasets.items()}
    gitlab_ds = [ds for ds, d in datasets.items() if d.get("tracker") == "gitlab"]

    def dataset(t):
        if t["tracker"] == "gitlab":
            return gitlab_ds[0] if gitlab_ds else None
        return next((ds for ds, rx in sorted(jira_ds.items()) if rx.fullmatch(t["key"])), None)

    files_of = defaultdict(list)
    for f in commit_file:
        files_of[(f["repo"], f["sha"])].append(f["path"])
    rows, n = set(), Counter()
    for ct in commit_ticket:
        for path in files_of[(ct["repo"], ct["sha"])]:
            n["commit_paths"] += 1
            if path in head_paths.get(ct["repo"], ()):
                rows.add((ct["key"], "path", f"{ct['repo']}:{path}", "commit_key"))
            else:
                n["commit_paths_gone"] += 1
    index = {repo: suffix_index(paths) for repo, paths in head_paths.items()}
    site_at = {(s["repo"], s["path"], s["line"]): s["call_site_id"] for s in sites_now}
    symbols = {ds: [(f"{s['module']}.{s['name']}", s["name"]) for s in d.get("symbols", [])] for ds, d in datasets.items()}
    for t in tickets:
        ds = dataset(t)
        text = f"{t['summary']}\n{t['description']}"
        for repo in repos_of.get(ds, []):
            paths = head_paths.get(repo, set())
            for tok in set(PATH_TOKEN.findall(text)):
                if tok in paths:
                    rows.add((t["key"], "path", f"{repo}:{tok}", "path"))
            hits, unsure = frames(text, index.get(repo, {}))
            n["frames_ambiguous"] += unsure
            n["frames"] += len(hits) + unsure
            for path, ln in hits:
                rows.add((t["key"], "path", f"{repo}:{path}", "stack_trace"))
                if (repo, path, ln) in site_at:
                    rows.add((t["key"], "call_site", site_at[(repo, path, ln)], "stack_trace"))
        for dotted, name in symbols.get(ds, []):
            if re.search(rf"(?<![\w.]){re.escape(dotted)}(?!\w)", text):
                for s in sites_now:
                    if s["symbol"] == name and s["repo"] in repos_of.get(ds, []):
                        rows.add((t["key"], "call_site", s["call_site_id"], "symbol"))
    mapped = {k for k, *_ in rows}
    rows |= {(t["key"], "unmapped", "", "none") for t in tickets if t["key"] not in mapped}
    return [{"run_id": run_id, "key": k, "target_kind": kind, "target": target, "method": method}
            for k, kind, target, method in rows], n


# ───────────── coverage ─────────────

def build_coverage(run_id, sites_now, site_stats, cands, failures, n_files, owners, tickets, tmap, tmap_stats, match_stats, prev_run):
    by_tracker = Counter(t["tracker"] for t in tickets)
    mapped = {r["key"] for r in tmap if r["target_kind"] != "unmapped"}
    tracker_of = {t["key"]: t["tracker"] for t in tickets}
    present = len(sites_now)
    rows = [
        ("call_sites_resolved", sum(s["detection"] == "ast_resolved" for s in sites_now), present,
         "present call sites the resolver bound to the tracked symbol"),
        ("call_sites_unresolved", sum(s["detection"] == "ast_unresolved" for s in sites_now), present,
         "present call sites Jedi had no answer for: kept, not dropped"),
        ("call_sites_owned", sum(s["owner_team"] != "unowned" for s in sites_now), present,
         "present call sites whose file has an owner team"),
        ("candidates_dropped", site_stats["dropped"], len(cands),
         "edit-role candidates the resolver bound elsewhere (look-alikes): not call sites"),
        ("candidates_not_edit_role", site_stats["not_edit_role"], len(cands),
         "definitions, attributes, references: kept in candidate, not call sites"),
        ("parse_failures", len(failures), n_files, "files excluded because tree-sitter found an error in them"),
        ("files_owned", sum(o["owner_team"] != "unowned" for o in owners), len(owners),
         "files at the pinned SHAs with an owner team"),
        ("tickets_mapped", len(mapped), len(tickets), "tickets with at least one code target"),
        ("commit_paths_gone", tmap_stats["commit_paths_gone"], tmap_stats["commit_paths"],
         "files a keyed commit touched that no longer exist at the pinned SHA: not mapped"),
        ("stack_frames_ambiguous", tmap_stats["frames_ambiguous"], tmap_stats["frames"],
         "stack frames naming a repo file that name more than one: not mapped"),
        ("call_sites_matched", match_stats["matched"], match_stats["prev_present"],
         f"last run's present sites found again (prev run {prev_run})" if prev_run else "first run: nothing to match"),
        ("call_sites_removed", match_stats["removed"], match_stats["prev_present"], "last run's present sites gone"),
    ]
    for tracker in sorted(by_tracker):
        note = "GitLab is Ruby: no code in the snapshot" if tracker == "gitlab" else f"{tracker} tickets with a code target"
        rows.append((f"tickets_mapped_{tracker}", sum(tracker_of[k] == tracker for k in mapped), by_tracker[tracker], note))
    return [{"run_id": run_id, "metric": m, "numerator": a, "denominator": b, "note": note} for m, a, b, note in rows]


# ───────────── the command ─────────────

def main(run_id, config=CONFIG, root=None, prev_run=None):
    cfg, root = load_config(config), Path(root or data_root())
    check_settings(cfg)
    snap = root / "snapshots" / run_id
    if not (snap / "manifest.json").exists():
        raise SystemExit(f"no snapshot {snap}: run meridian.collect first")
    out = handoff(cfg, run_id, root)
    need = ["run", "candidate", "resolution", "parse_failure", "file_owner", "file_bug_fixes", "ticket",
            "commit_ticket", "commit_file", "git_commit"]
    if missing := [t for t in need if not (out / f"{t}.jsonl").exists()]:
        raise SystemExit(f"missing in {out}: {', '.join(missing)}. Run collect, history, scan, resolve and owners first")
    t = {name: read_jsonl(out / f"{name}.jsonl") for name in need}
    run = t["run"][0]
    prev = None
    if prev_run:
        pout = handoff(cfg, prev_run, root)
        if not (pout / "call_site.jsonl").exists():
            raise SystemExit(f"no {pout / 'call_site.jsonl'}: join the previous run first")
        prev = {"run": read_jsonl(pout / "run.jsonl")[0], "history": read_jsonl(pout / "call_site.jsonl"),
                "text": {(c["repo"], c["path"], c["line"], c["col"]): c["text"] for c in read_jsonl(pout / "candidate.jsonl")}}
        if prev["run"]["snapshot_at"] >= run["snapshot_at"]:
            raise SystemExit(f"--prev-run {prev_run} is not before {run_id} (snapshot_at)")

    checkout = lambda repo: snap / "repos" / repo.replace("/", "__")
    sites, site_stats = current_sites(t["candidate"], t["resolution"], cfg["scanner"]["edit_roles"])
    sites = with_occurrence(sites)
    owner = {(o["repo"], o["path"]): o["owner_team"] for o in t["file_owner"]}
    fixes = {(f["repo"], f["path"]): f["bug_fixes"] for f in t["file_bug_fixes"]}
    call_site, match_stats = build_sites(run_id, run, prev, sites, owner, fixes, t["git_commit"], checkout)
    now = [r for r in newest(call_site).values() if r["status"] == "present"]

    shas = json.loads(run["repo_shas"])
    head_paths = {}
    for ds, d in cfg["datasets"].items():
        if d.get("tracker") in ("jira", "gitlab"):
            for r in d.get("repos", []):
                head_paths[r["repo"]] = set(git(checkout(r["repo"]), "ls-tree", "-r", "--name-only", "-z",
                                                shas[r["repo"]]).split("\0")) - {""}
    tmap, tmap_stats = build_ticket_map(run_id, cfg, t["ticket"], t["commit_ticket"], t["commit_file"], head_paths, now)

    scanned = {c["repo"] for c in t["candidate"]} | {f["repo"] for f in t["parse_failure"]}
    n_files = sum(len(scan_files(checkout(repo), cfg["scanner"])) for repo in sorted(scanned))
    coverage = build_coverage(run_id, now, site_stats, t["candidate"], t["parse_failure"], n_files, t["file_owner"],
                              t["ticket"], tmap, tmap_stats, match_stats, prev_run)

    for name, rows in (("call_site", call_site), ("ticket_map", tmap), ("coverage", coverage)):
        write_jsonl(out / f"{name}.jsonl", rows, KEYS[name])
    by_repo = Counter(s["repo"] for s in now)
    for repo in sorted(by_repo):
        log(f"{repo:45} {by_repo[repo]:>6,} call sites")
    log(f"{'call_site':45} {len(now):,} present · versions written this run: " +
        ", ".join(f"{v:,} {k}" for k, v in sorted(match_stats.items()) if k not in ("matched", "prev_present")) +
        (f" · matched {match_stats['matched']:,} of {match_stats['prev_present']:,} from {prev_run}" if prev_run else " · first run"))
    log(f"{'ticket_map':45} {len(tmap):,} rows · " +
        ", ".join(f"{v:,} {k}" for k, v in sorted(Counter(r['method'] for r in tmap).items())))
    for c in coverage:
        log(f"  {c['metric']:28} {c['numerator']:>8,} / {c['denominator']:<8,} {c['note']}")
    log(f"{len(call_site):,} call_site · {len(tmap):,} ticket_map · {len(coverage)} coverage -> {out}")
    return call_site, tmap, coverage


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--prev-run", default=None, help="the last joined run, to match call sites to (none: first run)")
    a = ap.parse_args()
    main(a.run_id, a.config, prev_run=a.prev_run)
