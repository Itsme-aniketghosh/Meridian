"""S5's monitoring report, contract D2 (docs/deployment/README.md): drift + decay -> retrain. Owner: Vishwa.
Run from Model-Deployment/.

    python -m monitor.report --out report.json                 # the block the table was built from
    python -m monitor.report --table t/features.parquet --out report.json
    python -m monitor.report --simulate --out report.json      # a planted shift: every commit 10x larger

The refresh job puts the file at gs://<buckets.monitor>/reports/<run_id>.json, reads "retrain", and passes "why" on
as D3's reason. The dashboard reads the rest. Fields, exactly D2's, plus three:

    {"run_id", "at", "n_new",
     "drift": {"psi": {feature: psi}, "evidently_share", "flag", "why", "window"},
     "decay": {"window", "n", "positives", "pr_auc", "lines_x_files", "paired": [lo, hi], "flag", "why", ...},
     "retrain": drift.flag or decay.flag,
     "why": the reasons for retrain, or why not,        # added: D3's reason and the Slack message
     "simulated": --simulate was on,                     # added: a planted shift is never mistaken for a real one
     "thresholds": deploy.toml's [drift] and [decay]}    # added: the rules this report was judged by

--simulate plants the shift in the drift check only. Decay reads labels, which a planted shift can't change, so it
is measured as it is. Exit 0 either way.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from monitor import decay, drift
from deploy_config import load

FMT = drift.FMT


def build(run_id, at, drift_raw, decay_raw, thresholds, simulated=False):
    """The D2 report from monitor.drift's and monitor.decay's own reports. Pure: no I/O, so it's tested on its own."""
    d = {"psi": {c: f["psi"] for c, f in drift_raw["features"].items()},
         "evidently_share": drift_raw["drifted_share"], "flag": bool(drift_raw["retrain"]),
         "why": "; ".join(drift_raw["reasons"]) or "no drift threshold crossed",
         "window": drift_raw.get("window")}
    reasons = ([f"drift: {d['why']}"] if d["flag"] else []) + \
              ([f"decay: {decay_raw['why']}"] if decay_raw["flag"] else [])
    retrain = bool(reasons)
    why = "; ".join(reasons) if retrain else f"no retrain: {d['why']}; decay: {decay_raw['why']}"
    if simulated:
        why = f"SIMULATED ({drift.SIMULATE['scale']}x larger commits). {why}"
    return {"run_id": run_id, "at": at, "n_new": drift_raw["n_current"], "drift": d, "decay": decay_raw,
            "retrain": retrain, "why": why, "simulated": bool(simulated), "thresholds": thresholds}


def run_id_of(table):
    """The block the table was built from (its schema next to it), else "unknown"."""
    schema = Path(table).with_name("features.schema.json")
    if schema.exists():
        return json.loads(schema.read_text(encoding="utf-8"))["block_run_id"]
    return "unknown"


def run(table=drift.TABLE, model=decay.MODEL, simulated=False, run_id=None, at=None, config=drift.DEPLOY_TOML):
    cfg = load(config, use_env=False)
    drift_raw = drift.run(table, simulated=simulated, config=config)
    decay_raw = decay.run(table, model, config=config)
    at = at or datetime.now(timezone.utc).strftime(FMT)
    return build(run_id or run_id_of(table), at, drift_raw, decay_raw,
                 {"drift": dict(cfg["drift"]), "decay": dict(cfg["decay"])}, simulated)


def print_report(r):
    print(f"D2 {r['run_id']} at {r['at']}: {r['n_new']:,} new commits{' (SIMULATED)' if r['simulated'] else ''}")
    print(f"  drift: {'FLAG' if r['drift']['flag'] else 'ok'}, {r['drift']['why']}")
    print(f"  decay: {'FLAG' if r['decay']['flag'] else 'ok'}, {r['decay']['why']}")
    print(f"retrain: {'YES' if r['retrain'] else 'no'}; {r['why']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", type=Path, default=drift.TABLE)
    ap.add_argument("--model", type=Path, default=decay.MODEL, help="the champion (default: the registry's)")
    ap.add_argument("--simulate", action="store_true", help="plant a 10x size shift in the drift check")
    ap.add_argument("--run-id", help="the block's run id (default: the table's schema)")
    ap.add_argument("--at", help="the report's time, %%Y-%%m-%%dT%%H:%%M:%%SZ (default: now)")
    ap.add_argument("--out", type=Path, help="write the report as JSON")
    args = ap.parse_args(argv)
    r = run(args.table, args.model, args.simulate, args.run_id, args.at)
    print_report(r)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(r, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
