"""SZZ · step 1 · D6: bug_inducing and file_bug_fixes from a snapshot. Run by meridian.history. See CONTRACTS.md.

- A fix = a first-parent, non-merge commit from [szz] fix_since on (CONTRACTS.md: "SZZ counts fix commits from 2015 on
  only"), keyed in commit_ticket to a ticket matching [szz] fix (Bug + Fixed) that isn't bulk-closed.
  First-parent only, as in week 1: a side commit and the merge that brings it in would count one fix twice.
- SZZ: for each code file the fix changed, the lines it deleted or changed (git diff -w: whitespace-only edits don't
  count) are blamed at the fix's parent (blame -w). Blank and comment-only lines are skipped, and so are test files.
  A fix that only adds lines has nothing to blame and gives no rows; nor does a root commit (no parent), counted in the
  log.
- bug_inducing: one row per (fix, file, inducing commit). path is the blamed file, at the fix's parent. key is the
  fix's Bug ticket, the smallest if it names several.
- file_bug_fixes: per path, the fix commits that touched it in the 365 days up to snapshot_at (authored_at, start
  excluded), every file, tests too. path is as in that commit; a file renamed inside the window counts under both names.
- Cache (settings.toml [cache] szz, off by default): a fix commit blamed in an earlier run keeps its blame, from
  snapshots/_cache/szz/<repo>.json; only new fix commits are blamed. Which commits are fixes, their key, and
  file_bug_fixes (its 365-day window moves) are worked out again every run. Same bytes either way.
"""
import re
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial

from meridian.common import cache_key, git, log, source_hash
from meridian.tickets import jlist

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")
SHA_LINE = re.compile(r"^[0-9a-f]{40} \d+ \d+")


def code_rules(cfg):
    """[szz] test_paths and comment_prefixes, compiled once: (test path regex, {extension: comment prefixes})."""
    s = cfg["szz"]
    return re.compile(s["test_paths"]), {e: tuple(c["prefixes"]) for c in s["comment_prefixes"] for e in c["ext"]}


def code_file(path, code):
    test, comment = code
    return path.rsplit(".", 1)[-1] in comment and not test.search(path)


def changed(co, parent, fix, code):
    """{old_path: [old line numbers]} the fix deleted or changed in code files, whitespace ignored."""
    out, head, lines = {}, False, None
    diff = git(co, "diff", "-w", "--unified=0", "--no-color", "--no-ext-diff", "--find-renames",
               "--src-prefix=a/", "--dst-prefix=b/", parent, fix)
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            head, lines = True, None
        elif head and line.startswith("--- "):  # only in the header: a deleted "-- x" line is "--- x" too
            # git ends a path with a space in a tab
            old = line[6:].removesuffix("\t") if line.startswith("--- a/") else None
            lines = out.setdefault(old, []) if old and code_file(old, code) else None
        elif line.startswith("@@"):
            head = False
            if lines is not None:
                m = HUNK.match(line)
                start, n = int(m.group(1)), int(m.group(2) or 1)
                lines.extend(range(start, start + n))
    return {p: ls for p, ls in out.items() if ls}


def ranges(lines):
    """[1, 2, 3, 7] -> ['1,3', '7,7']"""
    out, lo = [], None
    for i, n in enumerate(lines):
        if lo is None:
            lo = n
        if i + 1 == len(lines) or lines[i + 1] != n + 1:
            out.append(f"{lo},{n}")
            lo = None
    return out


def blame(co, rev, path, lines, code):
    """Counter {inducing sha: lines}, blank and comment-only lines skipped."""
    args = [a for r in ranges(sorted(set(lines))) for a in ("-L", r)]
    out, sha, skip = Counter(), None, code[1][path.rsplit(".", 1)[-1]]
    for line in git(co, "blame", "-w", "--porcelain", *args, rev, "--", path).split("\n"):
        if SHA_LINE.match(line):
            sha = line[:40]
        elif line.startswith("\t"):
            text = line[1:].strip()
            if text and not text.startswith(skip):
                out[sha] += 1
    return out


def szz(co, fix, parent, code):
    """{blamed path: Counter {inducing sha: lines}} for one fix commit. code = code_rules(cfg)."""
    out = {}
    for path, lines in sorted(changed(co, parent, fix, code).items()):
        c = blame(co, parent, path, lines, code)
        if c:
            out[path] = c
    return out


def _szz(code, job):
    co, fix, parent = job
    return fix, szz(co, fix, parent, code)


def fixes(commits, tickets, commit_ticket, rule, since):
    """{(repo, sha): (commit row, [Bug ticket keys])}"""
    ok = {t["key"] for t in tickets if not t["bulk_closed"] and all(t[k] == v for k, v in rule.items())}
    by = {(c["repo"], c["sha"]): c for c in commits}
    out = defaultdict(list)
    for r in commit_ticket:
        c = by.get((r["repo"], r["sha"]))
        if r["key"] in ok and c and c["first_parent"] and not c["is_merge"] and c["authored_at"] >= since:
            out[(r["repo"], r["sha"])].append(r["key"])
    return {k: (by[k], sorted(v)) for k, v in out.items()}


def bug_inducing(fix_rows, co_of, run_id, code, workers=None, cache_of=None):
    """cache_of ([cache] szz): repo -> common.Cache of each fix's {path: inducing SHAs}, keyed on its SHA, this code,
    the code rules and git's version. A commit's SHA fixes its parent and their history, so its blame never changes.
    key (the fix's ticket) is never cached: which tickets a commit fixes can change from run to run."""
    rows, by_repo = [], defaultdict(list)
    for (repo, sha), (c, keys) in fix_rows.items():
        by_repo[repo].append((c, keys))
    for repo, fs in sorted(by_repo.items()):
        keys = {c["sha"]: ks[0] for c, ks in fs}
        # a root commit has no parent to blame
        jobs = sorted((str(co_of(repo)), c["sha"], c["parent_sha"]) for c, _ in fs if c["parent_sha"])
        cache, done = cache_of and cache_of(repo), {}
        if cache:
            base = cache_key(source_hash(__file__), code[0].pattern, sorted(code[1].items()),
                             git(co_of(repo), "version"))
            done = {fix: hit for _, fix, _ in jobs if (hit := cache.get(cache_key(base, fix))) is not None}
        with ProcessPoolExecutor(workers) as pool:
            for fix, files in pool.map(partial(_szz, code), [j for j in jobs if j[1] not in done], chunksize=8):
                done[fix] = {path: sorted(c) for path, c in files.items()}
                if cache:
                    cache.put(cache_key(base, fix), done[fix])
        for fix, files in sorted(done.items()):
            for path, shas in files.items():
                rows += [{"run_id": run_id, "repo": repo, "fix_sha": fix, "key": keys[fix], "path": path,
                          "inducing_sha": s} for s in shas]
        log(f"{repo:45} {len(fs):>7,} fix commits  "
            f"{len({r['fix_sha'] for r in rows if r['repo'] == repo}):>7,} with lines to blame  "
            f"{sum(r['repo'] == repo for r in rows):>8,} bug_inducing rows  {len(fs) - len(jobs)} root commits skipped"
            + (f"  {len(cache.hits)} reused from the cache" if cache else ""))
        if cache:
            cache.save()
    return rows


def file_bug_fixes(fix_rows, files, snapshot_at, days, run_id, fmt):
    end = datetime.strptime(snapshot_at, fmt).replace(tzinfo=timezone.utc)
    start = (end - timedelta(days=days)).strftime(fmt)
    shas, keys = defaultdict(set), defaultdict(set)
    for f in files:
        fx = fix_rows.get((f["repo"], f["sha"]))
        if fx and start < fx[0]["authored_at"] <= snapshot_at:
            shas[(f["repo"], f["path"])].add(f["sha"])
            keys[(f["repo"], f["path"])].update(fx[1])
    return [{"run_id": run_id, "repo": repo, "path": path, "bug_fixes": len(s), "fix_keys": jlist(keys[(repo, path)]),
             "window_start": start, "window_end": snapshot_at} for (repo, path), s in shas.items()]


def build(commits, files, d4, manifest, cfg, run_id, co_of, workers=None, cache_of=None):
    """{table: rows} for bug_inducing and file_bug_fixes."""
    s = cfg["szz"]
    fx = fixes(commits, d4["ticket"], d4["commit_ticket"], s["fix"], s["fix_since"])
    return {"bug_inducing": bug_inducing(fx, co_of, run_id, code_rules(cfg), workers, cache_of),
            "file_bug_fixes": file_bug_fixes(fx, files, manifest["snapshot_at"], s["bug_fixes_window_days"], run_id,
                                             cfg["run"]["time_format"])}
