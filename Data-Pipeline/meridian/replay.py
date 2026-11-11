"""Push replay (GAPS.md gap 5, decision 15, Rachna): the 21 migration commits through the push check, as real
push_alert rows.

    python -m meridian.replay --run-id <run_id>                      # -> out/<run_id>/push_alert.jsonl
    python -m meridian.replay --run-id <run_id> --apply              # ... and put the rows into its meridian.sqlite

The 21: the commit after each pinned SHA in meridian.toml, Django's conversion commit (c651331b34) and the 20 url()
migrations, the same pushes report/figures_scenarios.py replays. Each is checked out from collect's bare mirror
(snapshots/_cache/repos) and put through meridian.pushcheck against the run's block, with settings.toml's limits, as
the PR workflow would. Every finding becomes one push_alert row: push_sha (the commit), base_run_id (the block),
check_id, target, evidence ({"what": the comment's line}). outcome and decided_at stay NULL: nobody acted on these
alerts, they're the history replayed.

push_alert.jsonl is what the writer loads (meridian.writer, a tracking table), so the next block build carries the
rows. --apply also inserts them into the block now (creating push_alert if the block predates schema v1.3), so /push
shows them without a rebuild. Rows of other pushes already in the file are kept; a replayed push's rows are replaced.

Prints alerts per push, which is 05-test's bar for the push check (median <= 1 alert per push, decision 15).
"""
import argparse
import json
import sqlite3
import statistics
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from meridian.common import CONFIG, HERE, SCHEMA, load_config, load_settings

ROOT = HERE.parent
MIRRORS = ROOT / "snapshots" / "_cache" / "repos"
OUT = ROOT / "out"
COLUMNS = ("push_sha", "base_run_id", "check_id", "target", "evidence", "outcome", "decided_at")


def next_commits(cfg):
    """{repo: (pinned parent, the next commit)}: Django's conversion, and each package's url() migration."""
    out = {"django/django": ("4353640ea9", "c651331b34")}
    for r in cfg["datasets"]["django_packages"]["repos"]:      # each pinned at the parent of its migration commit
        out[r["repo"]] = (r["ref"], r["ref"].removesuffix("^1"))
    return out


def git(*args):
    return subprocess.run(["git", "-c", "core.quotePath=false", *args], check=True, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def rows_for(push_sha, run_id, found):
    """push_alert rows for one push's findings, one per (check, target): the first finding of each wins."""
    seen, out = set(), []
    for check, target, what in found:
        if (check, target) in seen:
            continue
        seen.add((check, target))
        out.append({"push_sha": push_sha, "base_run_id": run_id, "check_id": check, "target": target,
                    "evidence": json.dumps({"what": what}, ensure_ascii=False), "outcome": None, "decided_at": None})
    return out


def replay(db_path, run_id, mirrors, cfg, settings, commits=None, check=None):
    """[(repo, push_sha, status, rows)] for each push. status: 'checked', 'skipped' (over the scope limit) or
    'missing' (no mirror). check: the push check (meridian.pushcheck.push_check, injectable for tests)."""
    if check is None:
        from meridian.pushcheck import push_check as check
    out = []
    for repo, (parent, child) in (commits or next_commits(cfg)).items():
        mirror = Path(mirrors) / (repo.replace("/", "__") + ".git")
        if not mirror.is_dir():
            out.append((repo, None, "missing", []))
            continue
        with tempfile.TemporaryDirectory() as tmp:
            co = Path(tmp) / "co"
            git("clone", "-q", "--shared", "--no-checkout", str(mirror), str(co))
            git("-C", str(co), "checkout", "-q", child)
            sha = git("-C", str(co), "rev-parse", "HEAD").strip()
            found = []
            text = check(db_path, repo, co, [], cfg, settings, f"{parent}...{child}", collect=found)
        status = "skipped" if "\nSkipped:" in text else "checked"
        out.append((repo, sha, status, rows_for(sha, run_id, found)))
    return out


def merge(path, new_rows, replayed):
    """The file's rows of other pushes, then the replayed pushes' new rows, sorted by push_alert's primary key."""
    kept = []
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line)["push_sha"] not in replayed:
                kept.append(json.loads(line))
    rows = kept + new_rows
    rows.sort(key=lambda r: (r["push_sha"], r["check_id"], r["target"]))
    Path(path).write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                          encoding="utf-8")
    return rows


def apply(db_path, rows, replayed):
    """Into the block: push_alert as schema.sql defines it if the block predates it, the replayed pushes' rows
    replaced."""
    ddl = SCHEMA.read_text(encoding="utf-8")
    start = ddl.index("CREATE TABLE push_alert")
    create = ddl[start:ddl.index(";", start) + 1]
    db = sqlite3.connect(db_path)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'push_alert'").fetchone():
            db.execute(create)
        db.executemany("DELETE FROM push_alert WHERE push_sha = ?", [(s,) for s in sorted(replayed)])
        db.executemany(f"INSERT INTO push_alert ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                       [[r[c] for c in COLUMNS] for r in rows if r["push_sha"] in replayed])
        db.commit()
    finally:
        db.close()


def summary(results):
    """Alerts per checked push, 05-test's bar: median <= 1."""
    per = [len(rows) for _, _, status, rows in results if status == "checked"]
    by_check = Counter(r["check_id"] for *_, rows in results for r in rows)
    return {"pushes": len(results), "checked": len(per), "skipped": sum(s == "skipped" for _, _, s, _ in results),
            "missing": sum(s == "missing" for _, _, s, _ in results), "alerts": sum(per),
            "median_per_push": statistics.median(per) if per else None, "by_check": dict(sorted(by_check.items())),
            "meets_05_test": bool(per) and statistics.median(per) <= 1}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True, help="the block's run, out/<run_id>/meridian.sqlite")
    ap.add_argument("--db", type=Path, help="the block (default out/<run_id>/meridian.sqlite)")
    ap.add_argument("--mirrors", type=Path, default=MIRRORS, help="collect's bare git mirrors")
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--apply", action="store_true", help="also put the rows into the block now")
    a = ap.parse_args(argv)
    db = a.db or OUT / a.run_id / "meridian.sqlite"
    if not db.is_file():
        raise SystemExit(f"{db} not found")
    cfg, settings = load_config(a.config), load_settings("push_check")
    results = replay(db, a.run_id, a.mirrors, cfg, settings)
    for repo, sha, status, rows in results:
        checks = dict(Counter(r["check_id"] for r in rows))
        print(f"  {repo:42} {(sha or '')[:10]:10} {status:8} {len(rows)} alert(s) {checks or ''}")
    new = [r for *_, rows in results for r in rows]
    replayed = {sha for _, sha, status, _ in results if sha}
    out = db.parent / "push_alert.jsonl"
    merge(out, new, replayed)
    s = summary(results)
    print(f"{s['checked']} of {s['pushes']} pushes checked ({s['skipped']} skipped over the scope limit, "
          f"{s['missing']} with no mirror): {s['alerts']} alerts, median {s['median_per_push']} per push "
          f"({'meets' if s['meets_05_test'] else 'misses'} 05-test's median <= 1); by check {s['by_check']}")
    print(f"wrote {out}")
    if a.apply:
        apply(db, new, replayed)
        print(f"and put {len(new)} rows into {db} (push_alert)")
    (db.parent / "push_replay.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    return s


if __name__ == "__main__":
    main()
