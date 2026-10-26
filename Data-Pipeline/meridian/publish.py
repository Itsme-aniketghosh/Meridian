"""Publish · make one validated run the block every view reads (UPDATE-PLAN.md #10).

    python -m meridian.publish --run-id <run_id> --config meridian.toml [--prev-run <run_id>] [--from-root <data root>]

Runs only after validate (the DAG's order), and refuses on its own too: out/<run_id>/anomalies.json must exist and
hold no error, and the run must be complete (a tracker down makes a code-only 'partial' run: --allow-partial). Then,
under the data root's [publish] dir:
1. out/<run_id>/meridian.sqlite -> <run_id>.sqlite, and bias.json, if the run has one, -> <run_id>.bias.json.
2. latest.json -> that file: run_id, prev_run, snapshot_at, repo_shas, config_hash, file, published_at. The views and
   meridian.status read it, so a failed or refused publish leaves the last good block live.
Both are written to a tmp file, then renamed: a reader sees the old file or the new one, never half of one. Older
published runs are never deleted. --from-root publishes a run computed under another data root (the weekly rebuild) as
<run_id>.rebuild.sqlite, so the update's copy of the same run stays, and copies its out/<run_id>/ over the update's,
which the next update's join reads as its prev run. It refuses once another run is live: never a rollback. [dvc]
push = true: dvc commit and push after, for the main data root and [pipeline] run_id only (what dvc.yaml tracks).
"""
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from meridian.common import CONFIG, HERE, data_root, handoff, load_config, load_settings, log, parser, read_jsonl


def published(root=None, settings=None):
    return Path(root or data_root()) / load_settings("publish", settings).get("dir", "published")


def latest(root=None, settings=None):
    """The live latest.json, {} if nothing is published yet."""
    p = published(root, settings) / "latest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def put(path, write):
    """write(tmp), then rename tmp onto path."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def main(run_id, config=CONFIG, root=None, prev_run=None, from_root=None, settings=None, allow_partial=False):
    cfg = load_config(config)
    out = handoff(cfg, run_id, Path(from_root or root or data_root()))
    anomalies = out / "anomalies.json"
    if not anomalies.exists():
        raise SystemExit(f"no {anomalies}: validate {run_id} first")
    if errors := [f for f in json.loads(anomalies.read_text(encoding="utf-8")) if f[0] == "error"]:
        raise SystemExit(f"{len(errors)} data errors in {anomalies}: {run_id} not published, the last good block stays live")
    run = read_jsonl(out / "run.jsonl")[0]
    if run["status"] != "complete" and not allow_partial:
        raise SystemExit(f"{run_id} is {run['status']} ({run['notes']}): not published over a complete block. Rerun "
                         "collect once the tracker is back, or --allow-partial")
    if from_root and (live := latest(root, settings).get("run_id", "")) not in ("", run_id):
        raise SystemExit(f"{live} is live now: the rebuild of {run_id} would roll it back")
    dest = published(root, settings)
    dest.mkdir(parents=True, exist_ok=True)
    name = f"{run_id}{'.rebuild' if from_root else ''}.sqlite"
    put(dest / name, lambda tmp: shutil.copyfile(out / "meridian.sqlite", tmp))
    if (out / "bias.json").exists():   # the weekly rebuild's
        put(dest / name.replace(".sqlite", ".bias.json"), lambda tmp: shutil.copyfile(out / "bias.json", tmp))
    if from_root:   # the update's out/ was wrong, and the next update's join reads it as its prev run
        shutil.copytree(out, handoff(cfg, run_id, Path(root or data_root())), dirs_exist_ok=True)
    doc = {"run_id": run_id, "prev_run": prev_run or "", "snapshot_at": run["snapshot_at"],
           "repo_shas": json.loads(run["repo_shas"]), "config_hash": run["config_hash"], "file": name,
           "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    put(dest / "latest.json", lambda tmp: tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                                                         encoding="utf-8", newline="\n"))
    log(f"published {run_id} -> {dest / name}, {dest / 'latest.json'}")
    if load_settings("dvc", settings).get("push"):
        if from_root:
            log("dvc: skipped, dvc.yaml tracks the main data root only")
        elif run_id != load_settings("pipeline", settings).get("run_id"):
            log(f"dvc: skipped, dvc.yaml tracks [pipeline] run_id only, not {run_id}")
        else:
            for args in (["commit", "--force", "--quiet"], ["push"]):
                subprocess.run([sys.executable, "-m", "dvc", *args], cwd=HERE, check=True)
    return doc


if __name__ == "__main__":
    ap = parser(__doc__, "the validated run to publish")
    ap.add_argument("--prev-run", default=None, help="the run it was matched against, recorded for the rebuild")
    ap.add_argument("--from-root", type=Path, default=None, help="the data root the run was computed under, if not this one")
    ap.add_argument("--allow-partial", action="store_true", help="publish a partial run (a tracker was down) all the same")
    a = ap.parse_args()
    main(a.run_id, a.config, prev_run=a.prev_run, from_root=a.from_root, allow_partial=a.allow_partial)
