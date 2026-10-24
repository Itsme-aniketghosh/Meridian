"""SZZ check · D6: is meridian.szz good enough to hand over? Not an Airflow part: a check you run before handing over.

    python -m meridian.szz_check --run-id <run_id> --d4j <dir>     # <dir>/framework/projects, <dir>/project_repos

1. SZZ on the 130 Defects4J labels ([szz] answer_key). Each label's fix is Defects4J's fixed revision; SZZ's pick is
   the commit blamed for the most lines (tie: the latest). Baselines, from the same code files before the fix:
   churn = the commit that changed the most lines in them, latest = the last commit to touch them.
2. Fragility on the run's Spark history, as week 1 measured it (9.7x): source files with >= risky_bug_fixes fixes in
   the year before a cutoff a year before snapshot_at, vs their share of the next year's fixes. Baseline: the same
   number of files picked by the year's churn instead. Reads out/<run_id>/meridian.sqlite, so run the writer first.
"""
import csv, re, sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meridian.common import CONFIG, HERE, git, handoff, load_config, log, parser
from meridian.szz import code_file, code_rules, szz


def history(co, parent, paths):
    """[(sha, author time, lines changed in paths)] for commits before the fix, newest first."""
    out, cur = [], None
    for line in git(co, "log", "--numstat", "--format=%x1e%H %at", parent, "--", *paths).split("\n"):
        if line.startswith("\x1e"):
            sha, at = line[1:].split()
            cur = [sha, int(at), 0]
            out.append(cur)
        elif line.strip():
            a, d, _ = line.split("\t", 2)
            cur[2] += (0 if a == "-" else int(a)) + (0 if d == "-" else int(d))
    return [tuple(c) for c in out]


def defects4j(d4j, labels, code):
    repos = {r[0]: r[1].rsplit("/", 1)[1] + ".git"
             for r in csv.reader(open(d4j / "project_repos" / "repos.csv", encoding="utf-8", newline=""))}
    fixed = {}
    for p in {r["pid"] for r in labels}:
        for name in ("active-bugs.csv", "deprecated-bugs.csv"):  # some labelled bugs are deprecated in v3, the fix still is one
            f = d4j / "framework" / "projects" / p / name
            for b in csv.DictReader(open(f, encoding="utf-8", newline="")) if f.exists() else []:
                fixed[(p, b["bug.id"])] = b["revision.id.fixed"]
    hits, sizes, recall = Counter(), [], 0
    for r in labels:
        co, fix = d4j / "project_repos" / repos[r["pid"]], fixed[(r["pid"], r["bid"])]
        parent = git(co, "rev-parse", fix + "^").strip()
        paths = [p for p in git(co, "diff", "--name-only", "--no-renames", parent, fix).split("\n") if p and code_file(p, code)]
        past = history(co, parent, paths) if paths else []
        when = {s: at for s, at, _ in past}
        blamed = Counter()
        for c in szz(co, fix, parent, code).values():
            blamed.update(c)
        truth = r["bic_d4j_v3"]
        picks = {
            "szz": max(blamed, key=lambda s: (blamed[s], when.get(s, 0))) if blamed else None,
            "churn": max(past, key=lambda c: (c[2], c[1]))[0] if past else None,
            "latest": past[0][0] if past else None,
        }
        for k, v in picks.items():
            hits[k] += v == truth
        recall += truth in blamed
        sizes.append(len(blamed))
    n = len(labels)
    log(f"[szz/defects4j] N={n}  SZZ pick right {hits['szz']}/{n} ({hits['szz'] / n:.0%})  "
        f"churn {hits['churn']}/{n} ({hits['churn'] / n:.0%})  latest {hits['latest']}/{n} ({hits['latest'] / n:.0%})")
    log(f"                label among SZZ's candidates {recall}/{n} ({recall / n:.0%}), "
        f"{sum(sizes) / n:.1f} candidates per fix, {sizes.count(0)} fixes with nothing to blame")
    return hits, recall


def fragility(db, snapshot_at, cfg):
    fmt, ck, rule, risky_at = cfg["run"]["time_format"], cfg["checks"], cfg["szz"]["fix"], cfg["graph"]["risky_bug_fixes"]
    end, days = datetime.strptime(snapshot_at, fmt).replace(tzinfo=timezone.utc), ck["fragility_window_days"]
    cut, start = (end - timedelta(days=days)).strftime(fmt), (end - timedelta(days=2 * days)).strftime(fmt)
    src_rx, test_rx = re.compile(ck["source_files"]), re.compile(ck["test_files"])
    src = lambda p: src_rx.search(p) and not test_rx.search(p)
    fix_sql = f"""SELECT DISTINCT c.repo, c.sha, f.path FROM commit_ticket ct JOIN ticket t ON t.key = ct.key
                 JOIN git_commit c ON c.repo = ct.repo AND c.sha = ct.sha JOIN commit_file f ON f.repo = c.repo AND f.sha = c.sha
                 WHERE {" AND ".join(f"t.{k} = ?" for k in rule)} AND t.bulk_closed = 0 AND c.first_parent = 1
                 AND c.is_merge = 0 AND c.authored_at >= ? AND c.authored_at > ? AND c.authored_at <= ?"""

    def fixes(a, b):
        out = defaultdict(Counter)
        for repo, _, path in db.execute(fix_sql, (*rule.values(), cfg["szz"]["fix_since"], a, b)):
            if src(path):
                out[repo][path] += 1
        return out

    prev, nxt = fixes(start, cut), fixes(cut, snapshot_at)
    for repo in sorted(prev):
        live, churn = set(), Counter()
        for path, a, d in db.execute("""SELECT f.path, f.added, f.deleted FROM commit_file f JOIN git_commit c ON c.repo = f.repo
                AND c.sha = f.sha WHERE f.repo = ? AND c.first_parent = 1 AND c.authored_at > ? AND c.authored_at <= ?""", (repo, start, cut)):
            if src(path):
                live.add(path)
                churn[path] += (a or 0) + (d or 0)
        n_next = {p: n for p, n in nxt[repo].items() if p in live}
        total = sum(n_next.values())
        risky = {p for p, n in prev[repo].items() if n >= risky_at and p in live}
        by_churn = set(sorted(live, key=lambda p: (-churn[p], p))[:len(risky)])

        def lift(files):  # as text: n/a when there's nothing to divide by (no files picked, or no fixes next year)
            got = sum(n_next.get(p, 0) for p in files)
            return (f"{got / total / (len(files) / len(live)):.1f}x" if files and total else "n/a"), (f"{got / total:.0%}" if total else "n/a")

        (l, s), (lc, sc) = lift(risky), lift(by_churn)
        log(f"[fragility] {repo}  cutoff {cut}  source files {len(live):,}, risky {len(risky)} ({len(risky) / len(live):.1%}) "
            f"-> {s} of next year's {total:,} file-fixes, lift {l}")
        log(f"            churn baseline, top {len(risky)} files by churn -> {sc}, lift {lc}")


def main(run_id, d4j, config=CONFIG, root=None):
    cfg = load_config(config)
    labels = list(csv.DictReader(open(HERE.parent / cfg["szz"]["answer_key"], encoding="utf-8", newline="")))
    defects4j(Path(d4j), labels, code_rules(cfg))
    db = sqlite3.connect(handoff(cfg, run_id, root) / "meridian.sqlite")
    (snapshot_at,) = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (run_id,)).fetchone()
    fragility(db, snapshot_at, cfg)


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--d4j", required=True, help="folder with framework/projects and project_repos (Defects4J v3)")
    a = ap.parse_args()
    main(a.run_id, a.d4j, a.config)
