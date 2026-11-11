"""Recall-first models: the three model kinds, tuned to catch bugs, not to rank them. Run from Model-Pipeline/.

    python -m model.recall                      # tune logreg, forest, lgbm; compare with the PR-AUC champion on val
    python -m model.recall --trials 20 --kinds forest

Why: when an LLM agent reviews every flagged item and throws out false alarms, a false positive costs one agent call,
but a missed bug is gone. So the target is recall, and the cost is how much the agents must read.

The objective, on the time folds inside train only (never val): the share of commits that must be flagged, highest
score first, to catch `target` of the bugs (default 90%). Lower is better: the same recall for less agent work.
The search covers each kind's parameters (train.DEFAULT_SEARCH) and how the loss weighs the rare bug class:
none, or class_weight="balanced" (and LightGBM's scale_pos_weight).

Then each kind's best config is fitted on train and scored on val, next to the baselines and the PR-AUC champion's
saved val scores (reports/tuned_val_scores.csv). Writes reports/recall_val.json and reports/recall_val_scores.csv.

It promotes nothing: the registry still picks the champion by its written rule (decision 6). Whether recall becomes
that rule is a team decision. shortcut: a fixed number of trials, not decision 6's batches; MLflow logging is left out.
Upgrade both if these models are adopted.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from model import train as T

HERE = Path(__file__).resolve().parents[1]


def share_for_recall(y, score, target=0.9):
    """The smallest share of rows, highest score first, whose flags catch `target` of the positives."""
    y = np.asarray(y, dtype=int)
    if y.sum() == 0:
        raise ValueError("no positives: recall means nothing")
    order = np.argsort(-np.asarray(score, dtype=float), kind="mergesort")
    caught = np.cumsum(y[order])
    k = int(np.searchsorted(caught, np.ceil(target * y.sum()))) + 1
    return k / len(y)


def recall_at_share(y, score, share):
    """Recall when the top `share` of rows are flagged."""
    y = np.asarray(y, dtype=int)
    k = int(round(share * len(y)))
    order = np.argsort(-np.asarray(score, dtype=float), kind="mergesort")
    return float(y[order[:k]].sum() / y.sum())


def imbalances(kind):
    return ["none", "balanced"] + (["scale_pos_weight"] if kind == "lgbm" else [])


def fold_share(train, kind, seed, params, imbalance, target, n_folds=3):
    """Mean share_for_recall over the time folds inside train."""
    if set(train["split"]) != {"train"}:
        raise PermissionError("tuning folds take train rows only")
    values = []
    for fit_idx, score_idx, _ in T.time_folds(train, n_folds):
        _, s = T.fit_predict(train.loc[fit_idx], train.loc[score_idx], kind, seed, params, imbalance)
        values.append(share_for_recall(train.loc[score_idx, "label"], s, target))
    return float(np.mean(values))


def tune(df, kind, seed, trials, target):
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    train = T.fit_rows(df)
    space = T.DEFAULT_SEARCH[kind]

    def objective(trial):
        params = T.suggest(trial, space)
        imbalance = trial.suggest_categorical("imbalance", imbalances(kind))
        return fold_share(train, kind, seed, params, imbalance, target)

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=trials)
    best = dict(study.best_params)
    imbalance = best.pop("imbalance")
    return {k: (None if v == "none" else v) for k, v in best.items()}, imbalance, float(study.best_value)


def row(y, s, target, budget):
    return {"share_for_recall": share_for_recall(y, s, target), "recall_at_budget": recall_at_share(y, s, budget),
            "pr_auc": T.pr_auc(y, s), "roc_auc": float(roc_auc_score(y, s))}


def bootstrap_share(y, a, b, target, reps=500, seed=0):
    """share_for_recall of a minus b on the same resampled rows, with a 95% interval. Below 0: a needs less."""
    y, a, b = (np.asarray(v) for v in (y, a, b))
    rng = np.random.default_rng(seed)
    diffs = []
    while len(diffs) < reps:
        i = rng.integers(0, len(y), len(y))
        if y[i].sum() == 0:
            continue
        diffs.append(share_for_recall(y[i], a[i], target) - share_for_recall(y[i], b[i], target))
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return share_for_recall(y, a, target) - share_for_recall(y, b, target), float(lo), float(hi)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m model.recall", description=__doc__.split("\n")[0])
    p.add_argument("--features", default=str(HERE / "data" / "features.parquet"))
    p.add_argument("--out", default=str(HERE / "reports"))
    p.add_argument("--champion-scores", default=str(HERE / "reports" / "tuned_val_scores.csv"))
    p.add_argument("--kinds", nargs="+", default=list(T.MODEL_KINDS), choices=T.MODEL_KINDS)
    p.add_argument("--trials", type=int, default=50)
    p.add_argument("--target", type=float, default=0.9, help="recall to reach")
    p.add_argument("--budget", type=float, default=0.3, help="share of commits flagged, for recall_at_budget")
    p.add_argument("--seed", type=int, default=1)
    a = p.parse_args(argv)

    df = T.load_features(a.features)
    val = T.rows(df, "val")
    y = val["label"].astype(int).to_numpy()
    scores = dict(T.baseline_scores(val))
    champ = Path(a.champion_scores)
    if champ.exists():
        c = pd.read_csv(champ)
        if c["sha"].tolist() == val["sha"].tolist():
            scores["champion_forest_pr_auc"] = c["forest"].to_numpy()
        else:
            print(f"note: {champ} is on other val rows; the PR-AUC champion is left out")
    configs = {}
    for kind in a.kinds:
        params, imbalance, fold = tune(df, kind, a.seed, a.trials, a.target)
        _, s = T.fit_predict(T.fit_rows(df), val, kind, a.seed, params, imbalance)
        scores[f"recall_{kind}"] = s
        configs[kind] = {"params": params, "imbalance": imbalance, "fold_share_for_recall": fold}
        print(f"{kind}: best fold share {fold:.3f} with imbalance={imbalance}, {params}", flush=True)

    ref = "champion_forest_pr_auc" if "champion_forest_pr_auc" in scores else "lines_x_files"
    out = {"split": "val", "n": int(len(y)), "positives": int(y.sum()), "target_recall": a.target,
           "budget": a.budget, "trials": a.trials, "seed": a.seed, "reference": ref, "configs": configs,
           "results": {}}
    print(f"\nval N {len(y):,}, {y.sum()} bugs. Share flagged to catch {a.target:.0%} (lower is better); "
          f"recall at {a.budget:.0%} flagged; difference in share against {ref} (95% bootstrap)")
    for name, s in scores.items():
        r = row(y, s, a.target, a.budget)
        if name != ref:
            r["share_vs_ref"] = list(bootstrap_share(y, s, scores[ref], a.target, seed=a.seed))
        out["results"][name] = r
        vs = "" if name == ref else "  {:+.3f} [{:+.3f}, {:+.3f}]".format(*r["share_vs_ref"])
        print(f"{name:24} share {r['share_for_recall']:.3f}  recall@{a.budget:.0%} {r['recall_at_budget']:.3f}  "
              f"PR-AUC {r['pr_auc']:.3f}  ROC-AUC {r['roc_auc']:.3f}{vs}")
    o = Path(a.out)
    o.mkdir(parents=True, exist_ok=True)
    (o / "recall_val.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame({"sha": val["sha"].to_numpy(), "label": y, **scores}).to_csv(o / "recall_val_scores.csv", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
