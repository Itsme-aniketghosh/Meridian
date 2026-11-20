"""S5 data drift: new commits' features against train (Model-Deployment/PLAN.md §5). Owner: Vishwa. Run from Model-Deployment/.

    python -m monitor.drift                       # the last [drift] days days' commits against train
    python -m monitor.drift --days 0              # every commit outside a split (the 7,238 from 2025-26)
    python -m monitor.drift --simulate            # the same, with every commit 10x larger: must flag
    python -m monitor.drift --out report.json     # also write the report

Two signals, both from monitor.toml, and either one sets retrain:
- PSI per feature, binned on the reference's deciles. Retrain when PSI >= psi_threshold on any top-5 SHAP feature.
- Evidently's dataset drift. Evidently 0.7 doesn't import under our pinned pandas 3.1, so its default stattests are
  reimplemented here with scipy, the same choice per column: for a reference over 1,000 rows, numeric columns with
  more than 5 distinct values use the Wasserstein distance divided by the reference's std, the rest the
  Jensen-Shannon distance of the value frequencies, each drifted at >= 0.1. For a smaller reference, KS or
  chi-squared at p < 0.05. Retrain when the share of drifted columns is >= share_threshold.

The columns are the model's inputs: model.train's NUMERIC plus ticket_type. Labels are never read: drift is the
signal that needs none. Exit 0 either way; the report's "retrain" says what to do.
"""
import argparse
import json
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial import distance

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PIPELINE = ROOT / "Model-Pipeline"
sys.path.insert(0, str(PIPELINE))
from model.train import CATEGORICAL, NUMERIC  # noqa: E402  the model's inputs, from one place

CONFIG = HERE / "monitor.toml"
TABLE = PIPELINE / "data" / "features.parquet"
FEATURES = NUMERIC + CATEGORICAL
FMT = "%Y-%m-%dT%H:%M:%SZ"
EPS = 1e-4  # an empty bin's share, so PSI stays finite


def load_config(path=CONFIG):
    with open(path, "rb") as f:
        return tomllib.load(f)


def is_categorical(ref):
    """Evidently's rule: strings, and numbers with 5 or fewer distinct values, are compared as categories."""
    return not pd.api.types.is_numeric_dtype(ref) or ref.nunique() <= 5


def psi(ref, cur, bins=10):
    """Population stability index of cur against ref. Numbers: bins at ref's quantiles (ties merge bins).
    Categories: one bin per value seen in either."""
    ref, cur = pd.Series(ref).dropna(), pd.Series(cur).dropna()
    if is_categorical(ref):
        values = sorted(set(ref.astype(str)) | set(cur.astype(str)))
        p = ref.astype(str).value_counts(normalize=True).reindex(values, fill_value=0).to_numpy()
        q = cur.astype(str).value_counts(normalize=True).reindex(values, fill_value=0).to_numpy()
    else:
        edges = np.unique(np.quantile(ref.to_numpy(float), np.linspace(0, 1, bins + 1)[1:-1]))
        n = len(edges) + 1
        p = np.bincount(np.searchsorted(edges, ref.to_numpy(float), side="left"), minlength=n) / len(ref)
        q = np.bincount(np.searchsorted(edges, cur.to_numpy(float), side="left"), minlength=n) / len(cur)
    p, q = np.clip(p, EPS, None), np.clip(q, EPS, None)
    return float(np.sum((q - p) * np.log(q / p)))


def column_drift(ref, cur, cfg):
    """(method, score, drifted) for one column, by Evidently's default stattest for it."""
    ref, cur = pd.Series(ref).dropna(), pd.Series(cur).dropna()
    cat = is_categorical(ref)
    if len(ref) > 1000:
        if cat:
            values = sorted(set(ref.astype(str)) | set(cur.astype(str)))
            p = ref.astype(str).value_counts(normalize=True).reindex(values, fill_value=0)
            q = cur.astype(str).value_counts(normalize=True).reindex(values, fill_value=0)
            score = float(distance.jensenshannon(p.to_numpy(), q.to_numpy()))
            return "jensenshannon", score, score >= cfg["jensenshannon_threshold"]
        norm = max(float(np.std(ref.to_numpy(float))), 0.001)
        score = float(stats.wasserstein_distance(ref.to_numpy(float), cur.to_numpy(float)) / norm)
        return "wasserstein", score, score >= cfg["wasserstein_threshold"]
    if cat:
        values = sorted(set(ref.astype(str)) | set(cur.astype(str)))
        p = ref.astype(str).value_counts(normalize=True).reindex(values, fill_value=0).to_numpy()
        observed = cur.astype(str).value_counts().reindex(values, fill_value=0).to_numpy()
        expected = np.clip(p, EPS, None) / np.clip(p, EPS, None).sum() * observed.sum()
        score = float(stats.chisquare(observed, expected).pvalue)
        return "chisquare", score, score < cfg["p_value"]
    score = float(stats.ks_2samp(ref.to_numpy(float), cur.to_numpy(float)).pvalue)
    return "ks", score, score < cfg["p_value"]


def drift_report(ref, cur, cfg, features=FEATURES):
    """The drift report for cur against ref under [drift] cfg: per-feature PSI and stattest, and the retrain call."""
    rows = {}
    for col in features:
        method, score, drifted = column_drift(ref[col], cur[col], cfg)
        rows[col] = {"psi": round(psi(ref[col], cur[col], cfg["psi_bins"]), 4), "method": method,
                     "score": round(score, 4), "drifted": bool(drifted),
                     "ref_median": _median(ref[col]), "cur_median": _median(cur[col])}
    psi_flags = [c for c in cfg["top_features"] if rows[c]["psi"] >= cfg["psi_threshold"]]
    share = sum(r["drifted"] for r in rows.values()) / len(rows)
    dataset_drift = share >= cfg["share_threshold"]
    reasons = [f"PSI {rows[c]['psi']} >= {cfg['psi_threshold']} on {c}" for c in psi_flags]
    if dataset_drift:
        reasons.append(f"{share:.0%} of features drifted >= {cfg['share_threshold']:.0%}")
    return {"n_reference": len(ref), "n_current": len(cur), "features": rows,
            "psi_flags": psi_flags, "drifted_share": round(share, 4), "dataset_drift": bool(dataset_drift),
            "retrain": bool(psi_flags or dataset_drift), "reasons": reasons}


def _median(s):
    return float(s.median()) if pd.api.types.is_numeric_dtype(s) else None


def current_rows(df, days):
    """The commits to check: outside every split, and if days > 0 only the last `days` days of them."""
    cur = df[df["split"] == "none"]
    if days > 0:
        end = datetime.strptime(cur["authored_at"].max(), FMT).replace(tzinfo=timezone.utc)
        start = (end - timedelta(days=days)).strftime(FMT)
        cur = cur[cur["authored_at"] > start]
    return cur


def simulate(cur, sim):
    """A planted shift: every commit [simulate] scale times larger on [simulate] columns."""
    cur = cur.copy()
    for col in sim["columns"]:
        cur[col] = cur[col] * sim["scale"]
    return cur


def run(table=TABLE, days=None, simulated=False, config=CONFIG):
    cfg = load_config(config)
    days = cfg["drift"]["days"] if days is None else days
    df = pd.read_parquet(table)
    ref = df[df["split"] == cfg["drift"]["reference"]]
    cur = current_rows(df, days)
    if simulated:
        cur = simulate(cur, cfg["simulate"])
    report = drift_report(ref, cur, cfg["drift"])
    report["window"] = {"reference": cfg["drift"]["reference"], "days": days, "simulated": simulated,
                        "current_from": cur["authored_at"].min(), "current_to": cur["authored_at"].max()}
    return report


def print_report(r):
    w = r["window"]
    print(f"drift: {r['n_current']:,} commits {w['current_from']} to {w['current_to']}"
          f"{' (simulated 10x)' if w['simulated'] else ''} against {r['n_reference']:,} {w['reference']} rows")
    print(f"{'feature':<16}{'PSI':>8}  {'method':<14}{'score':>8}  drifted  median ref -> cur")
    for col, f in r["features"].items():
        med = "" if f["ref_median"] is None else f"{f['ref_median']:g} -> {f['cur_median']:g}"
        print(f"{col:<16}{f['psi']:>8.4f}  {f['method']:<14}{f['score']:>8.4f}  {'yes' if f['drifted'] else 'no':<7}  {med}")
    print(f"drifted share {r['drifted_share']:.0%}; PSI flags {r['psi_flags'] or 'none'}")
    print(f"retrain: {'YES, ' + '; '.join(r['reasons']) if r['retrain'] else 'no'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--table", type=Path, default=TABLE)
    ap.add_argument("--days", type=int, help="the last N days' commits; 0: all outside a split (default: monitor.toml)")
    ap.add_argument("--simulate", action="store_true", help="plant a 10x size shift")
    ap.add_argument("--out", type=Path, help="write the report as JSON")
    args = ap.parse_args(argv)
    report = run(args.table, args.days, args.simulate)
    print_report(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
