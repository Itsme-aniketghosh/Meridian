"""Check PLAN §5's retrain rules on real data, before the team signs them off. Run from Model-Deployment/.

    python -m monitor.check_thresholds              # prints the tables
    python -m monitor.check_thresholds --out f.json

Judged on val 2023 only (the vishwa-monitor packet): a rule that fires on a val month or quarter, where the model is
known to work, is a false alarm. 2025-26 shows what the rule says today. 2024 (test) judges nothing: its rows are
used only as the reference that production would hold for 2025-26 (features, never labels), and its quarters are
skipped. Nothing here changes deploy.toml.

1. Drift by month, the window production checks. Each rule as written, and the alternatives in the proposal:
   PSI against train or against the latest labelled year (2022 for val months, 2024 for 2025-26), at 0.2/0.25/0.3.
2. Decay by quarter and by half-year on val and 2025 (the windows closed at the snapshot): the champion's PR-AUC,
   lines x files', their paired difference, the bootstrap SD of one window's PR-AUC.
"""
import argparse
import json
import sys
import tomllib
from datetime import timedelta

import numpy as np
import pandas as pd

from monitor import drift
from model import evaluate, train

PIPELINE = drift.PIPELINE
VAL = 2023
THRESHOLDS = (0.2, 0.25, 0.3)


def latest_labelled_year(month):
    """The reference production would hold for a month: the newest labelled year before it, never test for val."""
    year = int(month[:4])
    return VAL - 1 if year <= VAL else 2024


def by_month(df, cfg):
    train_rows = df[df["split"] == cfg["reference"]]
    top = cfg["top_features"]
    rows = []
    for m in sorted(df["authored_at"].str[:7].unique()):
        if not (m.startswith(str(VAL)) or m >= "2025"):
            continue
        cur = df[df["authored_at"].str[:7] == m]
        r = drift.drift_report(train_rows, cur, cfg)
        prev = df[df["year"] == latest_labelled_year(m)]
        rows.append({"month": m, "period": "val" if m.startswith(str(VAL)) else "new", "n": len(cur),
                     "share": r["drifted_share"], "share_rule": r["dataset_drift"],
                     "psi_train": {c: round(r["features"][c]["psi"], 4) for c in top},
                     "psi_prev": {c: round(drift.psi(prev[c], cur[c], cfg["psi_bins"]), 4) for c in top}})
    return rows


def window_rows(cur, model, reps, seed):
    y = cur["label"].to_numpy()
    m = train.score_with(model, cur)
    lxf = train.baseline_scores(cur)["lines_x_files"]
    sd = float(np.std(evaluate.bootstrap(y, {"m": m}, reps, seed)["m"]))
    d, lo, hi = evaluate.paired_bootstrap(y, m, lxf, reps, seed)
    return {"n": len(cur), "positives": int(y.sum()), "positive_rate": round(float(y.mean()), 4),
            "pr_auc": round(evaluate.pr_auc(y, m), 4), "sd": round(sd, 4),
            "lxf_pr_auc": round(evaluate.pr_auc(y, lxf), 4), "vs_lxf": [round(d, 4), round(lo, 4), round(hi, 4)]}


def by_window(df, snapshot_at, window_days, freq, reps=1000, seed=0):
    """Quarters (freq "Q") or half-years ("H") of val and 2025 whose 365-day window has closed at the snapshot."""
    import joblib
    model = joblib.load(PIPELINE / "registry" / "model.joblib")
    snap = pd.Timestamp(snapshot_at).tz_localize(None)
    when = pd.to_datetime(df["authored_at"]).dt.tz_localize(None)
    q = when.dt.to_period("Q")
    key = (when.dt.year.astype(str) + "H" + np.where(when.dt.month <= 6, "1", "2")) if freq == "H" else q.astype(str)
    rows = []
    for k in sorted(key[df["year"].isin([VAL, 2025])].unique()):
        cur = df[key == k]
        end = q[key == k].max().end_time
        if end + timedelta(days=window_days) > snap:
            continue
        rows.append({"window": k, "period": "val" if k.startswith(str(VAL)) else "new",
                     **window_rows(cur, model, reps, seed)})
    return rows


def run():
    cfg = drift.load_config()["drift"]
    df = pd.read_parquet(drift.TABLE)
    schema = json.loads((PIPELINE / "data" / "features.schema.json").read_text())
    with open(PIPELINE / "model.toml", "rb") as f:
        window = tomllib.load(f)["data"]["window_days"]
    snap = schema["snapshot_at"]
    return {"block": schema["block_run_id"], "snapshot_at": snap, "judged_on": f"val {VAL}",
            "months": by_month(df, cfg), "quarters": by_window(df, snap, window, "Q"),
            "halves": by_window(df, snap, window, "H")}


def fires(months, key, t):
    """{period: (months fired, months)} for PSI >= t on any top feature, against `key`'s reference."""
    out = {}
    for p in ("val", "new"):
        ms = [m for m in months if m["period"] == p]
        out[p] = (sum(max(m[key].values()) >= t for m in ms), len(ms))
    return out


def show(r):
    months = r["months"]
    print(f"block {r['block']}, snapshot {r['snapshot_at']}, judged on {r['judged_on']}\n")
    print("1. Drift by month: months a rule fires on (val: false alarms; new: 2025-26)")
    print(f"{'rule':<44}{'val':>8}{'new':>8}")
    val = [m for m in months if m["period"] == "val"]
    new = [m for m in months if m["period"] == "new"]
    print(f"{'>= 50% of features drifted (as written)':<44}{sum(m['share_rule'] for m in val):>5}/{len(val):<2}"
          f"{sum(m['share_rule'] for m in new):>5}/{len(new):<2}")
    for key, name in (("psi_train", "train"), ("psi_prev", "latest labelled year")):
        for t in THRESHOLDS:
            f = fires(months, key, t)
            label = f"PSI >= {t} vs {name}" + (" (as written)" if key == "psi_train" and t == 0.2 else "")
            print(f"{label:<44}{f['val'][0]:>5}/{f['val'][1]:<2}{f['new'][0]:>5}/{f['new'][1]:<2}")
    print("\n   val months, top-feature PSI vs train, the largest:")
    for m in val:
        c = max(m["psi_train"], key=m["psi_train"].get)
        print(f"   {m['month']} n={m['n']:<4} {c} {m['psi_train'][c]:.3f}; vs 2022: "
              f"{max(m['psi_prev'], key=m['psi_prev'].get)} {max(m['psi_prev'].values()):.3f}")
    for title, rows in (("2. Decay by quarter", r["quarters"]), ("3. Decay by half-year", r["halves"])):
        print(f"\n{title} (champion vs lines x files, paired bootstrap)")
        print(f"{'window':<8}{'n':>6}{'pos':>5}{'rate':>7}{'PR-AUC':>8}{'sd':>7}{'LxF':>7}  model - LxF [95%]")
        for w in rows:
            d, lo, hi = w["vs_lxf"]
            print(f"{w['window']:<8}{w['n']:>6}{w['positives']:>5}{w['positive_rate']:>7.3f}{w['pr_auc']:>8.3f}"
                  f"{w['sd']:>7.3f}{w['lxf_pr_auc']:>7.3f}  {d:+.3f} [{lo:+.3f}, {hi:+.3f}]")


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
