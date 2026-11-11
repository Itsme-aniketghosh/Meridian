"""Retrain on fresh data, and replace the champion only if the new model beats it. Run from Model-Pipeline/.

    python -m model.refresh                 # once: if a newer block is published, retrain on it
    python -m model.refresh --every 24      # the loop: check every 24 hours, forever
    python -m model.refresh --refit         # refit the committed best params instead of a full search (faster)
    python -m model.refresh --dry-run       # say what it would do, change nothing

One cycle:
1. Read published/latest.json. If it names the block model.toml was built from, nothing is new: exit 99, the
   Data-Pipeline's "nothing new" code.
2. Roll model.toml forward to that block. [data] run_id becomes its run, and follow_up_end becomes its snapshot_at
   minus window_days, so every label still gets its full window. [split] moves with it: test is the last calendar
   year that ends by follow_up_end, val the year before it, and train runs from start_year to the year before val.
   On today's block (snapshot 2026-10-01) that gives exactly the split we use: train 2015-2022, val 2023, test 2024.
3. Rebuild the feature table (model.data), then run every step of model.run_all on it: train all three model kinds,
   seed noise, evaluate, bias, gate, registry.

Nothing new is decided here. Whether the new model replaces production is the gate's and the registry's call, as on
CI. The gate fails a model that's worse than the champion beyond seed noise. The registry promotes only on a gain
above [registry] noise_sds seed-noise SDs, with a passing bias report. It re-scores the champion's own model on the
new val rows first (decision 7), so both are judged on the same fresh data. A model that loses stays registered,
with its reason. Each cycle's champion is the bar for the next.

Refuses while [split] test_allowed is true: the one 2024 test run is in progress, and rolling would move its split.
train.py never reads test rows, so a rolled-forward test year stays unseen too.

No deployment: it pushes nothing. The registry writes registry/champion.json locally; CI's model.yml pushes on main.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from model import data, run_all

HERE = Path(__file__).resolve().parents[1]   # Model-Pipeline/
NOTHING_NEW = 99
TABLE = ("features.parquet", "features.schema.json")   # what model.data writes into data/
FMT = "%Y-%m-%dT%H:%M:%SZ"


def rolled_split(snapshot_at, window_days, start_year):
    """(follow_up_end, train, val, test) for a block taken at snapshot_at."""
    end = datetime.strptime(snapshot_at, FMT).replace(tzinfo=timezone.utc) - timedelta(days=window_days)
    test = end.year if (end.month, end.day, end.hour, end.minute, end.second) == (12, 31, 23, 59, 59) else end.year - 1
    if test - 2 < start_year:
        raise SystemExit(f"a block at {snapshot_at} leaves no train year from start_year {start_year}")
    return end.strftime(FMT), [start_year, test - 2], test - 1, test


def set_keys(text, section, values):
    """text with `key = value` lines in [section] set to values (TOML literals), comments kept. Every key must exist."""
    out, current, seen = [], None, set()
    for line in text.splitlines(keepends=True):
        head = re.match(r"\s*\[([^\]]+)\]\s*$", line)
        if head:
            current = head.group(1).strip()
        m = re.match(r"(\s*)([A-Za-z_]+)(\s*=\s*)(\"[^\"]*\"|\[[^\]]*\]|[^\s#]+)(.*)", line, re.S)
        if current == section and m and m.group(2) in values:
            line = m.group(1) + m.group(2) + m.group(3) + values[m.group(2)] + m.group(5)
            seen.add(m.group(2))
        out.append(line)
    missing = set(values) - seen
    if missing:
        raise SystemExit(f"model.toml [{section}] has no {sorted(missing)}")
    return "".join(out)


def roll(text, run_id, snapshot_at):
    """model.toml's text, moved to the block run_id taken at snapshot_at."""
    cfg = tomllib.loads(text)
    d = cfg["data"]
    end, train, val, test = rolled_split(snapshot_at, int(d["window_days"]), int(d["start_year"]))
    text = set_keys(text, "data", {"run_id": json.dumps(run_id), "follow_up_end": json.dumps(end)})
    return set_keys(text, "split", {"train": f"[{train[0]}, {train[1]}]", "val": str(val), "test": str(test)})


def latest_block(published):
    """(run_id, snapshot_at) of the published block."""
    latest = json.loads((Path(published) / "latest.json").read_text(encoding="utf-8"))
    db = Path(published) / latest["file"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("select snapshot_at from run where run_id = ?", (latest["run_id"],)).fetchone()
    finally:
        con.close()
    if row is None:
        raise SystemExit(f"{db} has no run row for {latest['run_id']}")
    return latest["run_id"], row[0]


def cycle(config, published, refit=False, dry_run=False, run=run_all.run, build=data.main, data_dir=HERE / "data"):
    """One cycle. Returns NOTHING_NEW, or the pipeline's exit code (0: every step passed, promoted or not)."""
    config = Path(config)
    text = config.read_text(encoding="utf-8")
    cfg = tomllib.loads(text)
    if cfg["split"].get("test_allowed"):
        raise SystemExit("[split] test_allowed is true: the one test run is in progress, so the split can't move")
    run_id, snapshot_at = latest_block(published)
    if run_id == cfg["data"]["run_id"]:
        print(f"nothing new: the published block is still {run_id}, the one model.toml was built from")
        return NOTHING_NEW
    new = roll(text, run_id, snapshot_at)
    s = tomllib.loads(new)["split"]
    print(f"new block {run_id} (snapshot {snapshot_at}): train {s['train'][0]}-{s['train'][1]}, val {s['val']}, "
          f"test {s['test']} (never read)")
    plan = run_all.steps(refit=refit)
    if dry_run:
        print("dry run: would roll model.toml, rebuild the feature table, then:")
        run_all.show(plan)
        return 0
    # The steps read model.toml and data/ from disk, so they're written in place. On a failure both go back, so the
    # next cycle sees the block as new again instead of "nothing new" on a half-done cycle.
    # shortcut: a gate failure (a worse model) also restores, so the loop retrains the same block each time until a
    # newer one lands; same data, same result, wasted CPU only. Upgrade: tell the gate's exit apart if that costs.
    kept = {f: f.read_bytes() for f in (config, *(Path(data_dir) / n for n in TABLE)) if f.exists()}
    code = 1
    try:
        config.write_text(new, encoding="utf-8")
        build(["--config", str(config), "--published", str(published), "--out", str(data_dir)])
        code = run(plan)
    finally:
        if code != 0:
            for f, b in kept.items():
                f.write_bytes(b)
            print(f"failed (exit {code}): model.toml and the feature table are back as they were", flush=True)
    return code


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m model.refresh", description=__doc__.split("\n")[0])
    p.add_argument("--config", default=str(HERE / "model.toml"))
    p.add_argument("--published", default=None, help="folder with latest.json (default: $MERIDIAN_DATA/published)")
    p.add_argument("--refit", action="store_true", help="refit the committed best params instead of searching")
    p.add_argument("--every", type=float, help="loop: check again every this many hours")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    published = a.published or data.published_dir()
    while True:
        try:
            code = cycle(a.config, published, a.refit, a.dry_run)
        except SystemExit as e:          # a step that stops with a message: the loop says so and tries again later
            if not a.every:
                raise
            print(e, flush=True)
            code = 1
        if not a.every:
            return code
        if code not in (0, NOTHING_NEW):
            print(f"cycle failed (exit {code}); the champion is unchanged. Trying again in {a.every:g} h", flush=True)
        time.sleep(a.every * 3600)


if __name__ == "__main__":
    sys.exit(main())
