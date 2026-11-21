"""S5 decay: is the model still better than lines x files on the newest commits whose labels have settled?
(Model-Deployment/PLAN.md §5). Owner: Vishwa. Run from Model-Deployment/.

    python -m monitor.decay                       # the champion on the newest closed window
    python -m monitor.decay --out decay.json      # also write it (D2's "decay" object)

Labels arrive late: a commit is bug-inducing once a fix authored up to label_window_days later blames it. So decay is
measured only on a window whose last commit is label_window_days or more before the block's snapshot, and that has
at least min_positives positives. All the numbers are deploy.toml's [decay] (contract D4):

1. The window: the newest calendar quarter that has closed and has min_positives positives. [decay] window, when
   set, can make it "half" (half-years); it isn't set, so quarters, as PLAN §5 is written.
2. On its rows: the champion's PR-AUC, lines x files' PR-AUC, and their paired bootstrap difference (model.evaluate's
   paired_bootstrap, model.toml's [eval] reps and interval, [seed]).
3. Flag when the interval's lower bound is below min_paired_lower (the model no longer clearly beats the rule), or
   when PR-AUC is more than max_drop under the previous qualifying window's.

No window qualifies: no flag, and "why" says so. The champion is Model-Pipeline/registry/model.joblib unless --model.
"""
import argparse
import json
import sys
import tomllib
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from monitor import drift
from model import evaluate, train

PIPELINE = drift.PIPELINE
MODEL = PIPELINE / "registry" / "model.joblib"
MODEL_TOML = PIPELINE / "model.toml"


def window_keys(authored_at, kind):
    """One label per row: "2025Q3" for quarters, "2025H1" for half-years."""
    when = pd.to_datetime(authored_at).dt.tz_localize(None)
    if kind == "quarter":
        return when.dt.to_period("Q").astype(str)
    if kind == "half":
        return when.dt.year.astype(str) + "H" + np.where(when.dt.month <= 6, "1", "2")
    raise ValueError(f"[decay] window {kind!r}: quarter or half")


def window_end(key):
    """The first instant after the window: its labels are settled label_window_days after this."""
    year, part = int(key[:4]), key[4:]
    month = {"Q1": 4, "Q2": 7, "Q3": 10, "Q4": 13, "H1": 7, "H2": 13}[part]
    return pd.Timestamp(year + (month == 13), 1 if month == 13 else month, 1)


def closed_windows(df, snapshot_at, cfg):
    """[(key, rows)] newest first: windows whose labels have settled at the snapshot, with min_positives or more."""
    snap = pd.Timestamp(snapshot_at).tz_localize(None)
    keys = window_keys(df["authored_at"], cfg.get("window", "quarter"))
    out = []
    for key in sorted(keys.unique(), reverse=True):
        rows = df[keys == key]
        if window_end(key) + timedelta(days=cfg["label_window_days"]) <= snap and \
                rows["label"].sum() >= cfg["min_positives"]:
            out.append((key, rows))
    return out


def measure(rows, scores, reps, seed, level):
    y = rows["label"].to_numpy()
    lxf = np.asarray(train.BASELINES["lines_x_files"](rows), dtype=float)
    d, lo, hi = evaluate.paired_bootstrap(y, scores, lxf, reps, seed, level)
    return {"n": len(rows), "positives": int(y.sum()), "pr_auc": round(evaluate.pr_auc(y, scores), 4),
            "lines_x_files": round(evaluate.pr_auc(y, lxf), 4), "difference": round(d, 4),
            "paired": [round(lo, 4), round(hi, 4)]}


def decay_report(df, score, snapshot_at, cfg, reps=2000, seed=1, level=0.95):
    """D2's "decay" object. score(rows) gives the model's risk for those rows."""
    windows = closed_windows(df, snapshot_at, cfg)
    if not windows:
        return {"window": None, "flag": False, "kind": cfg.get("window", "quarter"),
                "why": f"no {cfg.get('window', 'quarter')} with settled labels and {cfg['min_positives']}+ positives"}
    (key, rows), prev = windows[0], windows[1] if len(windows) > 1 else None
    r = {"window": key, "kind": cfg.get("window", "quarter"), **measure(rows, score(rows), reps, seed, level)}
    reasons = []
    if r["paired"][0] < cfg["min_paired_lower"]:
        reasons.append(f"model - lines x files lower bound {r['paired'][0]:+.3f} < {cfg['min_paired_lower']} "
                       f"on {key}")
    if prev is not None:
        p = {"window": prev[0], **measure(prev[1], score(prev[1]), reps, seed, level)}
        r["previous"] = {k: p[k] for k in ("window", "n", "positives", "pr_auc")}
        if p["pr_auc"] - r["pr_auc"] > cfg["max_drop"]:
            reasons.append(f"PR-AUC {r['pr_auc']:.3f} on {key} is {p['pr_auc'] - r['pr_auc']:.3f} under "
                           f"{p['window']}'s {p['pr_auc']:.3f} (> {cfg['max_drop']})")
    r["flag"] = bool(reasons)
    r["why"] = "; ".join(reasons) or (f"model beats lines x files on {key}: {r['difference']:+.3f} "
                                      f"[{r['paired'][0]:+.3f}, {r['paired'][1]:+.3f}]")
    return r


def snapshot_of(table, df):
    """The block's snapshot from the table's schema next to it, else the newest commit."""
    schema = Path(table).with_name("features.schema.json")
    if schema.exists():
        return json.loads(schema.read_text(encoding="utf-8"))["snapshot_at"]
    return df["authored_at"].max()


def run(table=drift.TABLE, model_path=MODEL, config=drift.DEPLOY_TOML, model_toml=MODEL_TOML):
    import joblib
    cfg = drift.load_config(config)["decay"]
    with open(model_toml, "rb") as f:
        mt = tomllib.load(f)
    df = pd.read_parquet(table)
    model = joblib.load(model_path)
    return decay_report(df, lambda rows: train.score_with(model, rows), snapshot_of(table, df), cfg,
                        reps=int(mt.get("eval", {}).get("bootstrap_reps", 2000)), seed=int(mt["seed"]["value"]),
                        level=float(mt.get("eval", {}).get("interval", 0.95)))


def print_report(r):
    if r["window"] is None:
        print(f"decay: {r['why']}")
        return
    print(f"decay: {r['window']}, {r['n']:,} commits, {r['positives']} positives")
    print(f"  model PR-AUC {r['pr_auc']:.3f}, lines x files {r['lines_x_files']:.3f}, "
          f"difference {r['difference']:+.3f} [{r['paired'][0]:+.3f}, {r['paired'][1]:+.3f}]")
    if "previous" in r:
        print(f"  previous window {r['previous']['window']}: PR-AUC {r['previous']['pr_auc']:.3f}")
    print(f"flag: {'YES, ' if r['flag'] else 'no, '}{r['why']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", type=Path, default=drift.TABLE)
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--out", type=Path, help="write the report as JSON")
    args = ap.parse_args(argv)
    r = run(args.table, args.model)
    print_report(r)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(r, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
