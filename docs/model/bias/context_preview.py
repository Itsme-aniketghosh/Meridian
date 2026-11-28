"""Gap 16 preview (decisions.md, Nov 13): the context cut on the real block with gap 24's bug-fix history.

Gap 24's rows land in the next block built. Until then, this recomputes file_bug_fixes with today's [szz] (szz.build's
own steps) over the block's out/<run_id>/ files, writes them into a scratch copy of the published block, and runs
meridian.context on the copy. The published block is never written.

    cd Data-Pipeline && python ../Model-Pipeline/packets/chaitali-bias/context_preview.py \
        --run-id 70dd1dabfed544b8 --db ../published/70dd1dabfed544b8.sqlite --scratch /tmp/preview.sqlite
"""
import argparse
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "Data-Pipeline"))
from meridian import context, szz  # noqa: E402
from meridian.common import CONFIG, data_root, handoff, load_config  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "reports" / "context_cut_preview.json"


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def history_rows(cfg, run_id):
    """file_bug_fixes as szz.build makes it today, from the block's own D3/D4 files."""
    d = handoff(cfg, run_id)
    manifest = json.loads((Path(data_root()) / "snapshots" / run_id / "manifest.json").read_text())
    commits, files = jsonl(d / "git_commit.jsonl"), jsonl(d / "commit_file.jsonl")
    tickets, commit_ticket = jsonl(d / "ticket.jsonl"), jsonl(d / "commit_ticket.jsonl")
    s, fmt = cfg["szz"], cfg["run"]["time_format"]
    fixes = dict(szz.fixes(commits, tickets, commit_ticket, s["fix"], s["fix_since"]))
    if s.get("fix_message"):
        fixes |= szz.message_fixes(commits, {r["repo"] for r in commit_ticket}, s["fix_message"], s["fix_since"])
    win = szz.windows(commits, manifest["repos"], manifest["snapshot_at"], s["bug_fixes_window_days"], fmt)
    return szz.file_bug_fixes(fixes, files, manifest["snapshot_at"], s["bug_fixes_window_days"], run_id, fmt, win)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--db", type=Path, required=True, help="the published block (read only)")
    ap.add_argument("--scratch", type=Path, required=True, help="the copy to write; outside the repo")
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    rows = history_rows(cfg, a.run_id)
    shutil.copyfile(a.db, a.scratch)
    with closing(sqlite3.connect(a.scratch)) as db:
        before = db.execute("SELECT COUNT(*) FROM file_bug_fixes WHERE run_id = ?", (a.run_id,)).fetchone()[0]
        cols = [r[1] for r in db.execute("PRAGMA table_info(file_bug_fixes)")]
        db.execute("DELETE FROM file_bug_fixes WHERE run_id = ?", (a.run_id,))
        db.executemany(f"INSERT INTO file_bug_fixes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       [tuple(r[c] for c in cols) for r in rows])
        db.commit()
    repos = sorted({r["repo"] for r in rows})
    print(f"file_bug_fixes: {before} rows -> {len(rows)}, in {len(repos)} repos")
    rep = context.main(["--run-id", a.run_id, "--db", str(a.scratch), "--config", str(a.config), "--out", str(a.out)])
    rep["preview"] = {"file_bug_fixes_rows": {"before": before, "after": len(rows)}, "repos_with_rows": repos}
    a.out.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
