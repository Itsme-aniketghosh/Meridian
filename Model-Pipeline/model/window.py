"""Decision 5, the training window (GAPS.md gap 12, Rachna): the candidate trained on 2019-2022 against 2015-2022.

    python -m model.window          # from Model-Pipeline/

The candidate's kind, params and seed (reports/candidate.json), refitted on train rows from 2019-2022 only, then
scored on val. The full window's val scores are the judged ones in its scores file (no refit, S6). Decision 5's rule,
written before any run: the shorter window wins only if the paired bootstrap interval of (2019-22 minus 2015-22) val
PR-AUC is above 0; tied, 2015-2022 (the plan's default, more data). Never reads test rows. Writes reports/window.json.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from model.train import HERE, check_table, fit_and_score, load_config, load_features, paired_bootstrap, pr_auc

REPORTS = HERE / "reports"
SHORT_FROM = 2019


def short_window(df, start=SHORT_FROM):
    """The same table, with train rows before `start` taken out of train (split 'none'): nothing else changes."""
    out = df.copy()
    out.loc[(out["split"] == "train") & (out["year"] < start), "split"] = "none"
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--candidate", default=str(REPORTS / "candidate.json"))
    p.add_argument("--config", default=str(HERE / "model.toml"))
    p.add_argument("--features", default=str(HERE / "data" / "features.parquet"))
    p.add_argument("--out", default=str(REPORTS / "window.json"))
    p.add_argument("--reps", type=int, default=None, help="bootstrap resamples (default: model.toml [eval])")
    a = p.parse_args(argv)
    cfg = load_config(a.config)
    sp = cfg["split"]
    df = load_features(a.features)
    check_table(df, {"train": sp["train"], "val": sp["val"], "test": sp["test"]})
    c = json.loads(Path(a.candidate).read_text())
    judged = pd.read_csv(Path(a.candidate).parent / c["scores_file"])
    reps = a.reps or int(cfg.get("eval", {}).get("bootstrap_reps", 1000))
    seed = int(c["seed"])

    short = short_window(df)
    _, s_short, scored = fit_and_score(short, c["kind"], seed, c["params"] or None)
    if not (scored["sha"].to_numpy() == judged["sha"].to_numpy()).all():
        raise SystemExit("the val rows don't match the judged scores file: same table as the tuned run?")
    y = judged["label"].to_numpy(dtype=int)
    full = judged[c["column"]].to_numpy(dtype=float)
    d, lo, hi = paired_bootstrap(y, s_short, full, reps=reps, seed=seed)
    picked = f"{SHORT_FROM}-2022" if lo > 0 else "2015-2022"
    report = {"decision": "rachna-5", "kind": c["kind"], "split": "val", "n": int(len(y)), "positives": int(y.sum()),
              "train_rows": {"2015-2022": int((df["split"] == "train").sum()),
                             f"{SHORT_FROM}-2022": int((short["split"] == "train").sum())},
              "pr_auc": {"2015-2022": pr_auc(y, full), f"{SHORT_FROM}-2022": pr_auc(y, s_short)},
              "short_minus_full": d, "interval": [lo, hi], "bootstrap_reps": reps,
              "rule": "the shorter window wins only if its paired interval is above 0; tied: 2015-2022",
              "picked": picked}
    Path(a.out).write_text(json.dumps(report, indent=2) + "\n")
    print(f"val N = {len(y):,}: 2015-2022 ({report['train_rows']['2015-2022']:,} train rows) PR-AUC "
          f"{report['pr_auc']['2015-2022']:.3f}; {SHORT_FROM}-2022 ({report['train_rows'][f'{SHORT_FROM}-2022']:,}) "
          f"{report['pr_auc'][f'{SHORT_FROM}-2022']:.3f}")
    print(f"  {SHORT_FROM}-22 minus 2015-22: {d:+.4f} [{lo:+.4f}, {hi:+.4f}] ({reps} paired resamples): "
          f"picked {picked}")
    print(f"wrote {a.out}")
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
