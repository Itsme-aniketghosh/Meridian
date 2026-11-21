"""Check PLAN §5's retrain rules on real data, before the team signs them off. Run from Model-Deployment/.

    python -m monitor.check_thresholds              # prints the tables
    python -m monitor.check_thresholds --out f.json

Nothing here changes a threshold: it shows how each rule as written in monitor.toml behaves.
1. Drift by year: each year's commits against train (2015-22). Years inside train are in-sample, shown as a floor.
2. Drift by month: every calendar month from val on, the window production checks ([drift] days). How often
   each rule fires on months where the model is known to work (2023 val, 2024 test) is its false-alarm rate.
3. Decay by quarter: positives per quarter, which quarters' 365-day windows have closed at the snapshot, the
   champion's PR-AUC, lines x files', their paired difference, and the bootstrap SD of one quarter's PR-AUC (is
   "falls more than 0.05" above one quarter's noise?).
"""
import argparse
import json
import sys
import tomllib
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from monitor import drift
from model import evaluate, train

FMT = drift.FMT
PIPELINE = drift.PIPELINE


def by_year(df, ref, cfg):
    rows = []
    for year in sorted(df["year"].unique()):
        cur = df[df["year"] == year]
        r = drift.drift_report(ref, cur, cfg)
        rows.append({"year": int(year), "split": cur["split"].iloc[0], "n": len(cur),
                     **{f"psi_{c}": r["features"][c]["psi"] for c in cfg["top_features"]},
                     "psi_flags": r["psi_flags"], "drifted_share": r["drifted_share"],
                     "dataset_drift": r["dataset_drift"], "retrain": r["retrain"]})
    return rows


def by_month(df, ref, cfg, start="2023-01"):
    rows = []
    months = pd.period_range(start, df["authored_at"].max()[:7], freq="M")
    for m in months:
        cur = df[df["authored_at"].str[:7] == str(m)]
        if len(cur) == 0:
            continue
        r = drift.drift_report(ref, cur, cfg)
        rows.append({"month": str(m), "split": cur["split"].mode().iloc[0], "n": len(cur),
                     "psi_flags": r["psi_flags"], "drifted_share": r["drifted_share"],
                     "psi_rule": bool(r["psi_flags"]), "dataset_drift": r["dataset_drift"]})
    return rows


def by_quarter(df, snapshot_at, window_days, reps=1000, seed=0, start="2023Q1"):
    import joblib
    model = joblib.load(PIPELINE / "registry" / "model.joblib")
    snap = datetime.strptime(snapshot_at, FMT).replace(tzinfo=timezone.utc)
    rows = []
    q = pd.PeriodIndex(df["authored_at"].str[:10], freq="Q")
    for period in pd.period_range(start, q.max(), freq="Q"):
        cur = df[q == period]
        end = period.end_time.to_pydatetime().replace(tzinfo=timezone.utc)
        closed = end + timedelta(days=window_days) <= snap
        row = {"quarter": str(period), "n": len(cur), "positives": int(cur["label"].sum()), "window_closed": closed}
        if closed and cur["label"].sum() > 0:
            y = cur["label"].to_numpy()
            m = train.score_with(model, cur)
            lxf = train.baseline_scores(cur)["lines_x_files"]
            boot = evaluate.bootstrap(y, {"m": m}, reps, seed)["m"]
            d, lo, hi = evaluate.paired_bootstrap(y, m, lxf, reps, seed)
            row.update({"pr_auc": round(evaluate.pr_auc(y, m), 4), "pr_auc_sd": round(float(np.std(boot)), 4),
                        "lxf_pr_auc": round(evaluate.pr_auc(y, lxf), 4),
                        "vs_lxf": [round(d, 4), round(lo, 4), round(hi, 4)]})
        rows.append(row)
    return rows


def run():
    cfg = drift.load_config()["drift"]
    df = pd.read_parquet(drift.TABLE)
    schema = json.loads((PIPELINE / "data" / "features.schema.json").read_text())
    with open(PIPELINE / "model.toml", "rb") as f:
        window = tomllib.load(f)["data"]["window_days"]
    ref = df[df["split"] == cfg["reference"]]
    return {"block": schema["block_run_id"], "snapshot_at": schema["snapshot_at"],
            "years": by_year(df, ref, cfg), "months": by_month(df, ref, cfg),
            "quarters": by_quarter(df, schema["snapshot_at"], window)}


def show(r):
    print(f"block {r['block']}, snapshot {r['snapshot_at']}\n\n1. Drift by year, against train")
    print(f"{'year':<6}{'split':<7}{'n':>6}  {'la':>6}{'nf':>6}{'pbf':>6}{'nd':>6}{'ld':>6}  {'drifted':>7}  retrain")
    for y in r["years"]:
        print(f"{y['year']:<6}{y['split']:<7}{y['n']:>6}  {y['psi_la']:>6.3f}{y['psi_nf']:>6.3f}"
              f"{y['psi_prior_bug_fixes']:>6.3f}{y['psi_nd']:>6.3f}{y['psi_ld']:>6.3f}  {y['drifted_share']:>7.0%}  "
              f"{'PSI ' if y['psi_flags'] else ''}{'share' if y['dataset_drift'] else ''}")
    print("\n2. Drift by month, against train: how often each rule fires")
    ms = pd.DataFrame(r["months"])
    later = np.where(ms["month"] < "2025", "2024 (test)", "2025-26")
    ms["period"] = np.where(ms["month"] < "2024", "2023 (val)", later)
    s = ms.groupby("period").agg(months=("month", "size"), median_n=("n", "median"),
                                 psi_rule=("psi_rule", "sum"), share_rule=("dataset_drift", "sum"))
    print(s.to_string())
    flagged = ms[ms["psi_rule"]]
    for _, m in flagged.iterrows():
        print(f"  PSI fired {m['month']} (n={m['n']}): {', '.join(m['psi_flags'])}")
    print("\n3. Decay by quarter (champion vs lines x files, paired)")
    print(f"{'quarter':<8}{'n':>6}{'pos':>5}  closed  {'PR-AUC':>7}{'sd':>7}{'LxF':>7}  model - LxF [95%]")
    for q in r["quarters"]:
        extra = ""
        if "pr_auc" in q:
            d, lo, hi = q["vs_lxf"]
            extra = f"{q['pr_auc']:>7.3f}{q['pr_auc_sd']:>7.3f}{q['lxf_pr_auc']:>7.3f}  {d:+.3f} [{lo:+.3f}, {hi:+.3f}]"
        print(f"{q['quarter']:<8}{q['n']:>6}{q['positives']:>5}  {'yes' if q['window_closed'] else 'no':<6}  {extra}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    r = run()
    show(r)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(r, f, indent=1, default=str)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
