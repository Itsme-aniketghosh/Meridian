"""Publish · make one validated run the block every view reads (notes/UPDATE-PLAN.md #10).

    python -m meridian.publish --run-id <run_id> --config meridian.toml [--prev-run <run_id>] [--from-root <data root>]

Runs only after validate (the DAG's order), and refuses on its own too: out/<run_id>/anomalies.json must exist and
hold no error, and the run must be complete (a tracker down makes a code-only 'partial' run: --allow-partial). Then,
under the data root's [publish] dir:
1. out/<run_id>/meridian.sqlite -> <run_id>.sqlite.
2. latest.json -> that file: run_id, prev_run, snapshot_at, repo_shas, config_hash, file, published_at. The views and
   meridian.status read it, so a failed or refused publish leaves the last good block live.
Both are written to a tmp file, then renamed: a reader sees the old file or the new one, never half of one. Never a
rollback: refused, before anything live changes, if the live run's snapshot_at is later than this run's, or for a
rebuild if another run is live. After an update's publish the main data root keeps snapshots/, out/, the published
<run_id>(.rebuild).sqlite files (and the mirrors' refs/runs/) only for this run and its prev, the run live before it and
its prev, and the frozen [pipeline] run_id. --from-root publishes a run computed under another data root (the weekly
rebuild) as <run_id>.rebuild.sqlite, so the update's copy of the same run stays, and swaps its out/<run_id>/ (no
gx_project/) in for the update's (the old one renamed aside, then deleted), which the next update's join reads as its
prev run. [dvc] push = true: dvc commit and push after, for the main data root and [pipeline] run_id only (what
dvc.yaml tracks); a dvc failure alerts ("warn"), the publish is live by then.
"""
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from meridian import alert
from meridian.common import (CONFIG, HERE, atomic_write, data_root, git, handoff, load_config, load_settings, log,
                             parser, read_jsonl)


def published(root=None, settings=None):
    return Path(root or data_root()) / load_settings("publish", settings).get("dir", "published")


def latest(root=None, settings=None):
    """The live latest.json, {} if nothing is published yet."""
    p = published(root, settings) / "latest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main(run_id, config=CONFIG, root=None, prev_run=None, from_root=None, settings=None, allow_partial=False):
    cfg = load_config(config)
    out = handoff(cfg, run_id, Path(from_root or root or data_root()))
    anomalies = out / "anomalies.json"
    if not anomalies.exists():
        raise SystemExit(f"no {anomalies}: validate {run_id} first")
    if errors := [f for f in json.loads(anomalies.read_text(encoding="utf-8")) if f[0] == "error"]:
        raise SystemExit(f"{len(errors)} data errors in {anomalies}: {run_id} not published, "
                         "the last good block stays live")
    run = read_jsonl(out / "run.jsonl")[0]
    if run["status"] != "complete" and not allow_partial:
        raise SystemExit(f"{run_id} is {run['status']} ({run['notes']}): not published over a complete block. Rerun "
                         "collect once the tracker is back, or --allow-partial")
    dest, base = published(root, settings), Path(root or data_root())
    dest.mkdir(parents=True, exist_ok=True)
    name = f"{run_id}{'.rebuild' if from_root else ''}.sqlite"
    atomic_write(dest / name, lambda tmp: shutil.copyfile(out / "meridian.sqlite", tmp))
    if from_root:   # the update's out/ was wrong, and the next update's join reads it as its prev run
        main_out = handoff(cfg, run_id, base)
        tmp, old = main_out.with_name(main_out.name + ".tmp"), main_out.with_name(main_out.name + ".old")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(out, tmp, ignore=shutil.ignore_patterns("gx_project"))
    doc = {"run_id": run_id, "prev_run": prev_run or "", "snapshot_at": run["snapshot_at"],
           "repo_shas": json.loads(run["repo_shas"]), "config_hash": run["config_hash"], "file": name,
           "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    # here, not before the copies: an update can publish while a rebuild copies
    live = latest(root, settings)
    if live and (live["snapshot_at"] > run["snapshot_at"] or from_root and live["run_id"] != run_id):
        if from_root:
            shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(f"{live['run_id']} is live now: publishing {run_id} would roll it back")
    if from_root:   # moved aside, not deleted first: a crash in between leaves one whole out/<run_id>, never none
        shutil.rmtree(old, ignore_errors=True)
        if main_out.exists():
            main_out.rename(old)
        tmp.rename(main_out)
        shutil.rmtree(old, ignore_errors=True)
    atomic_write(dest / "latest.json", lambda tmp: tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                                                                  encoding="utf-8", newline="\n"))
    log(f"published {run_id} -> {dest / name}, {dest / 'latest.json'}")
    if not from_root:   # the rebuild root's snapshots/ and out/<prev> are links into the main root's
        # and the frozen team run: the golden test and dvc.lock name it, and its refs keep its SHAs from git gc.
        # And the run live before this one with its prev: a rebuild of it running now links its prev's out/
        keep = {run_id, doc["prev_run"], load_settings("pipeline", settings).get("run_id"),
                *((live or {}).get(k) for k in ("run_id", "prev_run"))}
        for d in (base / "snapshots", handoff(cfg, run_id, base).parent):
            for p in d.glob("*"):
                if re.fullmatch(r"[0-9a-f]{16}", p.name) and p.name not in keep:
                    shutil.rmtree(p, ignore_errors=True)
        for p in dest.glob("*.sqlite"):   # never latest.json or gx/
            if (m := re.fullmatch(r"([0-9a-f]{16})(\.rebuild)?\.sqlite", p.name)) and m[1] not in keep:
                p.unlink(missing_ok=True)
        for m in (base / load_settings("collect", settings).get("mirrors", "snapshots/_cache/repos")).glob("*.git"):
            for ref in git(m, "for-each-ref", "--format=%(refname:lstrip=2)", "refs/runs/").split():
                if ref not in keep:
                    git(m, "update-ref", "-d", f"refs/runs/{ref}")
    if load_settings("dvc", settings).get("push"):
        if from_root:
            log("dvc: skipped, dvc.yaml tracks the main data root only")
        elif run_id != load_settings("pipeline", settings).get("run_id"):
            log(f"dvc: skipped, dvc.yaml tracks [pipeline] run_id only, not {run_id}")
        else:
            try:   # latest.json is written: the publish is live, a failed upload is news, not a failed publish
                for args in (["commit", "--force", "--quiet"], ["push"]):
                    subprocess.run([sys.executable, "-m", "dvc", *args], cwd=HERE, check=True)
            except (subprocess.CalledProcessError, OSError) as e:
                log(f"dvc: {e}", level=logging.WARNING)
                alert.send(f"meridian {run_id}: published, but dvc failed", [str(e)], "warn", settings)
    return doc


if __name__ == "__main__":
    ap = parser(__doc__, "the validated run to publish")
    ap.add_argument("--prev-run", default=None, help="the run it was matched against, recorded for the rebuild")
    ap.add_argument("--from-root", type=Path, default=None,
                    help="the data root the run was computed under, if not this one")
    ap.add_argument("--allow-partial", action="store_true",
                    help="publish a partial run (a tracker was down) all the same")
    a = ap.parse_args()
    main(a.run_id, a.config, prev_run=a.prev_run, from_root=a.from_root, allow_partial=a.allow_partial)
