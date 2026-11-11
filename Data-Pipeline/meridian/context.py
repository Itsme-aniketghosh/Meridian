"""The context builder (GAPS.md gap 16, Chaitali's decision 12): a stalled team's riskiest files, the rest as counts.

The writer's consumer task carries every file a team has call sites in. `cut` keeps files in order of risk until the
kept ones hold `cover` (90%) of the team's bug fixes, at least one file, and folds the rest into `files_cut` and
`sites_cut`, so `left` and every text stay true and nothing cut is named.

Risk is a file's bug fixes in the last year (`file_bug_fixes`, what the consumer facts already carry). The champion
model isn't used: it was trained on Spark only, P9 keeps lines x files for every other repo, and the real block's call
sites are all in Django-ecosystem repos. Stall-reason tasks list reasons, not files: nothing to cut there.

    python -m meridian.context --run-id 70dd1dabfed544b8 --db ../published/70dd1dabfed544b8.sqlite

Measures, over the block's consumer tasks: files, sites and facts characters kept, bug-fix coverage, and the coverage a
random file order would reach with the same number of files. Not switched on in agents.py (decision 12).
"""
import argparse
import json
import random
import sqlite3
import statistics
from contextlib import closing
from pathlib import Path

from meridian.common import CONFIG, handoff, load_config, log as say
from meridian.llm_cache import canonical

COVER = 0.90           # decision 12: the share of a team's bug fixes the kept files hold
ORDERS, SEED = 100, 1  # random orders for the comparison
OUT = Path(__file__).resolve().parents[2] / "Model-Pipeline" / "reports" / "context_cut.json"


def ranked(files):
    """Most bug fixes first, then most sites, then path."""
    return sorted(files, key=lambda f: (-f["bug_fixes"], -f["sites"], f["path"]))


def keep_n(files, cover=COVER):
    """How many files of `files`, in their order, hold `cover` of their bug fixes (at least 1)."""
    total = sum(f["bug_fixes"] for f in files)
    if total == 0:
        return min(1, len(files))
    held = 0
    for i, f in enumerate(files, 1):
        held += f["bug_fixes"]
        if held >= cover * total:
            return i
    return len(files)


def cut(facts, cover=COVER):
    """A consumer task's facts with its files cut by decision 12. Returns a new dict; the input is unchanged."""
    files = ranked(facts["files"])
    n = keep_n(files, cover)
    out = {**facts, "files": files[:n]}
    gone = files[n:]
    if gone:
        out["files_cut"], out["sites_cut"] = len(gone), sum(f["sites"] for f in gone)
    return out


def coverage(files, n):
    total = sum(f["bug_fixes"] for f in files)
    return 1.0 if total == 0 else sum(f["bug_fixes"] for f in files[:n]) / total


def measure(consumer_facts, cover=COVER):
    """The cut over every consumer task: per team and in total."""
    rng = random.Random(SEED)
    cols = {"files": ("files", "kept"), "sites": ("sites", "sites_kept"), "chars": ("chars", "chars_kept"),
            "bug_fixes": ("bug_fixes", "bug_fixes_kept")}
    teams, tot = [], {k: [0, 0] for k in cols}
    random_cov = []
    for facts in consumer_facts:
        files = facts["files"]
        after = cut(facts, cover)
        kept = after["files"]
        row = {"team": facts["team"], "symbol": facts["symbol"], "files": len(files), "kept": len(kept),
               "sites": sum(f["sites"] for f in files), "sites_kept": sum(f["sites"] for f in kept),
               "chars": len(canonical(facts)), "chars_kept": len(canonical(after)),
               "bug_fixes": sum(f["bug_fixes"] for f in files), "bug_fixes_kept": sum(f["bug_fixes"] for f in kept)}
        teams.append(row)
        for k, (all_col, kept_col) in cols.items():
            tot[k][0] += row[all_col]
            tot[k][1] += row[kept_col]
        if row["bug_fixes"]:
            shuffles = []
            for _ in range(ORDERS):
                order = files[:]
                rng.shuffle(order)
                shuffles.append(coverage(order, len(kept)))
            random_cov.append((row["bug_fixes"], statistics.mean(shuffles)))
    share = {k: {"all": a, "kept": b, "kept_share": b / a if a else None} for k, (a, b) in tot.items()}
    with_fixes = [t for t in teams if t["bug_fixes"]]
    return {"cover": cover, "teams": len(teams), "teams_with_bug_fixes": len(with_fixes),
            "teams_cut": sum(t["kept"] < t["files"] for t in teams), "total": share,
            "bug_fix_coverage": share["bug_fixes"]["kept_share"],
            "random_order_coverage": (sum(b * c for b, c in random_cov) / sum(b for b, _ in random_cov)
                                      if random_cov else None),
            "per_team": sorted(teams, key=lambda t: (t["team"], t["symbol"]))}


def consumer_facts(db, cfg, run_id, at):
    from meridian.agents import tasks  # here: agents is the heavier import
    return [t.facts["facts"] for t in tasks(db, cfg, run_id, at) if t.kind == "consumer"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--db", type=Path, help="this SQLite file instead of out/<run_id>/meridian.sqlite")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    path = Path(a.db or handoff(cfg, a.run_id) / "meridian.sqlite")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        at = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (a.run_id,)).fetchone()[0]
        rep = {"run_id": a.run_id, "at": at, **measure(consumer_facts(db, cfg, a.run_id, at))}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8", newline="\n")
    t = rep["total"]
    say(f"{rep['teams']} consumer tasks, {rep['teams_cut']} cut: files {t['files']['kept']}/{t['files']['all']}, "
        f"sites {t['sites']['kept']}/{t['sites']['all']}, facts characters {t['chars']['kept']:,}/{t['chars']['all']:,}"
        + (f"; bug fixes covered {rep['bug_fix_coverage']:.3f} (a random order with the same files: "
           f"{rep['random_order_coverage']:.3f})" if rep["bug_fix_coverage"] is not None else
           "; no file has a bug fix, so nothing ranks them: each team keeps its file with the most sites")
        + f"; wrote {a.out}")
    return rep


if __name__ == "__main__":
    main()
