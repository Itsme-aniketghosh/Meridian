"""Baselines, models and tuning for the defect-risk model (Rachna, step C).

Reads the feature table of Contract 1 (Model-Pipeline/packets/README.md). Every fit goes through `fit_rows`,
which only ever returns `train` rows, and `rows` refuses `test` outright: nobody looks at 2024 until Nov 12.
"""
import argparse
import hashlib
import json
import os
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

HERE = Path(__file__).resolve().parents[1]   # Model-Pipeline/

# Contract 2 defaults, used only when model.toml isn't there yet.
DEFAULT_CONFIG = {"split": {"train": [2015, 2022], "val": 2023, "test": 2024, "test_allowed": False},
                  "seed": {"value": 1}}

# Contract 1: every column the table must have.
ID_COLUMNS = ["sha", "authored_at", "year", "split", "label"]
SIZE = ["la", "ld", "nf", "nd", "ns", "entropy"]
HISTORY = ["ndev", "age", "nuc", "exp", "sexp", "prior_bug_fixes"]
TICKET = ["fix", "has_ticket"]
OTHER = ["is_minor"]
NUMERIC = SIZE + HISTORY + TICKET + OTHER
# Every numeric input except age is a count, a 0/1 flag or an entropy: never below 0. age can be: when a commit's
# author date is earlier than the last change to its files (rebases, cherry-picks), "days since" comes out a little
# negative (8 of 41,163 rows on c5118, min -1.94 days). feature_matrix reads those as 0, "changed just before".
CAN_BE_NEGATIVE = ("age",)
CATEGORICAL = ["ticket_type"]
SLICES = ["module", "component", "exp_bucket", "size_bucket"]   # Chaitali's bias slices: never model inputs
CONTRACT_COLUMNS = ID_COLUMNS + NUMERIC + CATEGORICAL + SLICES

SPLITS = {"train", "val", "test", "none"}
LABELLED = {"train", "val", "test"}


def threads():
    """Threads for one forest or LightGBM fit: $MERIDIAN_THREADS (pmap sets each worker's share), else up to 8.
    Neither changes a seeded model's predictions (tests/test_parallel.py), only how fast it fits."""
    return int(os.environ.get("MERIDIAN_THREADS", min(8, os.cpu_count() or 1)))


def _set_threads(n):
    os.environ["MERIDIAN_THREADS"] = n  # top level: Windows starts workers by pickling the initializer


def pmap(fn, args):
    """fn(*a) for each a in args, in separate processes, results in order. Each call seeds its own model, so the
    result is the same as a loop (tests/test_parallel.py). MERIDIAN_WORKERS caps the processes; 1 runs the loop."""
    args = [tuple(a) for a in args]
    workers = min(len(args), int(os.environ.get("MERIDIAN_WORKERS", os.cpu_count() or 1)))
    if workers <= 1:
        return [fn(*a) for a in args]
    from concurrent.futures import ProcessPoolExecutor
    share = str(max(1, min(8, (os.cpu_count() or 1) // workers)))  # so the processes don't fight for cores
    with ProcessPoolExecutor(workers, initializer=_set_threads, initargs=(share,)) as ex:
        return list(ex.map(fn, *zip(*args)))


class FeatureError(ValueError):
    """The feature table breaks Contract 1. The message says how."""


def check_table(df, split_years=None):
    """Raise FeatureError unless df keeps Contract 1. split_years: {"train": [2015, 2022], "val": 2023, ...}."""
    missing = [c for c in CONTRACT_COLUMNS if c not in df.columns]
    if missing:
        raise FeatureError("feature table is missing column(s): " + ", ".join(missing))
    if df.empty:
        raise FeatureError("feature table has no rows")
    bad_split = sorted(set(df["split"]) - SPLITS)
    if bad_split:
        raise FeatureError(f"unknown split value(s): {bad_split}")
    for col in NUMERIC:
        n = int(df[col].isna().sum())
        if n:
            raise FeatureError(f"{col}: {n} missing values (Contract 1 has none)")
    for col in NUMERIC:
        if col in CAN_BE_NEGATIVE:
            continue
        n = int((df[col] < 0).sum())
        if n:
            raise FeatureError(f"{col}: {n} negative values, min {df[col].min()} (a count or flag can't be below 0)")
    labelled = df[df["split"].isin(LABELLED)]
    bad_label = sorted(set(labelled["label"].dropna().astype(int)) - {0, 1})
    if labelled["label"].isna().any() or bad_label:
        raise FeatureError("label must be 0 or 1 on every train, val and test row")
    if df["sha"].duplicated().any():
        raise FeatureError(f"{int(df['sha'].duplicated().sum())} duplicate sha values")
    if split_years:
        for split, years in split_years.items():
            lo, hi = (years, years) if isinstance(years, int) else years
            got = df.loc[df["split"] == split, "year"]
            off = got[(got < lo) | (got > hi)]
            if len(off):
                raise FeatureError(f"{len(off)} {split} rows outside {lo}-{hi}, e.g. year {int(off.iloc[0])}")


def rows(df, *splits):
    """The rows of the named splits. `test` is refused: train.py never sees 2024."""
    if "test" in splits:
        raise PermissionError("train.py never reads test rows; the one 2024 run is Rochan's, on Nov 12")
    unknown = set(splits) - SPLITS
    if unknown:
        raise ValueError(f"unknown split(s): {sorted(unknown)}")
    return df[df["split"].isin(splits)]


def fit_rows(df):
    """The only rows any fit, transformer or calibrator may see."""
    return rows(df, "train")


# Baselines: scores, no training. Higher means riskier.
BASELINES = {
    "lines_x_files": lambda d: (d["la"] + d["ld"]) * d["nf"],
    "churn": lambda d: d["la"] + d["ld"],
    "bug_fixes": lambda d: d["prior_bug_fixes"],
}


def baseline_scores(df):
    """{name: float array} for every baseline, one score per row of df."""
    return {name: np.asarray(f(df), dtype=float) for name, f in BASELINES.items()}


def time_folds(train, n_folds=3):
    """Expanding-window folds inside train: each of the last n_folds years is scored by a fit on every
    earlier year. Returns [(fit_index, score_index, score_year)]. No fold fits on a year it scores, or a later one."""
    years = sorted(int(y) for y in train["year"].unique())
    if len(years) < n_folds + 1:
        raise ValueError(f"{len(years)} train years can't make {n_folds} time folds (need {n_folds + 1})")
    folds = []
    for year in years[-n_folds:]:
        fit = train.index[train["year"] < year]
        score = train.index[train["year"] == year]
        folds.append((fit, score, year))
    return folds


def pr_auc(y, score):
    """Average precision (PR-AUC). Refuses a set with only one class, where it means nothing."""
    y = np.asarray(y, dtype=int)
    if y.min() == y.max():
        raise ValueError("PR-AUC needs both classes in y")
    return float(average_precision_score(y, np.asarray(score, dtype=float)))


# ---------- models (step C) ----------
MODEL_KINDS = ("logreg", "forest", "lgbm")
IMBALANCE = ("none", "balanced", "scale_pos_weight")


def ticket_categories(train):
    """ticket_type values seen in train, sorted. Learned from train only, so val can't shape the columns."""
    return sorted(str(v) for v in train["ticket_type"].unique())


def feature_matrix(df, categories, ticket_features=True, drop=()):
    """The model inputs for df: numeric columns, plus ticket_type one-hot over `categories`.
    A ticket_type not in `categories` gets all zeros. Slice columns are never included. age below 0 is read as 0."""
    numeric = [c for c in NUMERIC if c not in drop and (ticket_features or c not in TICKET)]
    X = df[numeric].astype(float)
    for col in CAN_BE_NEGATIVE:
        if col in X:
            X[col] = X[col].clip(lower=0)   # see CAN_BE_NEGATIVE
    if ticket_features and "ticket_type" not in drop:
        for cat in categories:
            X[f"ticket_type={cat}"] = (df["ticket_type"].astype(str) == cat).astype(float)
    return X


def build_model(kind, seed, params=None, imbalance="none", pos_rate=None):
    """An unfitted estimator. params override the defaults below; seed is always applied."""
    if kind not in MODEL_KINDS:
        raise ValueError(f"unknown model kind {kind!r}; one of {MODEL_KINDS}")
    if imbalance not in IMBALANCE:
        raise ValueError(f"unknown imbalance option {imbalance!r}; one of {IMBALANCE}")
    if imbalance == "scale_pos_weight" and kind != "lgbm":
        raise ValueError("scale_pos_weight is a LightGBM option")
    params = dict(params or {})
    weight = "balanced" if imbalance == "balanced" else None
    if kind == "logreg":
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import FunctionTransformer, StandardScaler
        lr = dict(C=1.0, max_iter=2000)
        lr.update(params)
        return make_pipeline(FunctionTransformer(np.log1p), StandardScaler(),
                             LogisticRegression(class_weight=weight, random_state=seed, **lr))
    if kind == "forest":
        from sklearn.ensemble import RandomForestClassifier
        rf = dict(n_estimators=300, min_samples_leaf=20, max_features="sqrt", n_jobs=threads())
        rf.update(params)
        return RandomForestClassifier(class_weight=weight, random_state=seed, **rf)
    import lightgbm
    gbm = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=50,
               subsample=0.8, subsample_freq=1, colsample_bytree=0.8)
    gbm.update(params)
    if imbalance == "scale_pos_weight":
        if not pos_rate or not 0 < pos_rate < 1:
            raise ValueError("scale_pos_weight needs the train positive rate")
        gbm["scale_pos_weight"] = (1 - pos_rate) / pos_rate
    # Decision 9: deterministic trees, so laptops, the image and CI agree. With deterministic and force_row_wise,
    # the thread count doesn't change a prediction (max |diff| 0.0 at 1, 4, 8, 16 threads), so use the cores:
    # 3x faster at 4 threads than at 1.
    return lightgbm.LGBMClassifier(class_weight=weight, random_state=seed, deterministic=True,
                                   force_row_wise=True, n_jobs=threads(), verbose=-1, **gbm)


def fit_predict(fit, score, kind, seed, params=None, imbalance="none", ticket_features=True, drop=(),
                model=None):
    """Fit on the `fit` rows, return (model, scores on the `score` rows). Ticket categories come from `fit`."""
    cats = ticket_categories(fit)
    X_fit = feature_matrix(fit, cats, ticket_features, drop)
    X_score = feature_matrix(score, cats, ticket_features, drop)
    y_fit = fit["label"].astype(int).to_numpy()
    if model is None:
        model = build_model(kind, seed, params, imbalance, pos_rate=float(y_fit.mean()))
    model.fit(X_fit, y_fit)
    return model, model.predict_proba(X_score)[:, 1]


def fit_and_score(df, kind, seed, params=None, imbalance="none", ticket_features=True, drop=(),
                  score_split="val", model=None):
    """Fit on train rows only, score `score_split`. Returns (fitted model, scores, scored rows).
    `model` lets a test pass its own estimator in place of build_model's."""
    train = fit_rows(df)
    scored = rows(df, score_split)
    model, scores = fit_predict(train, scored, kind, seed, params, imbalance, ticket_features, drop, model)
    return model, scores, scored


def chance_ceiling(y, q=0.99, reps=300, seed=0):
    """The q-quantile of PR-AUC that purely random scores reach on these labels. A model trained on shuffled
    labels should stay below it: at a few thousand rows, chance alone lands up to ~0.02 above the positive rate."""
    rng = np.random.default_rng(seed)
    y = np.asarray(y, dtype=int)
    return float(np.quantile([pr_auc(y, rng.random(len(y))) for _ in range(reps)], q))


def paired_bootstrap(y, a, b, reps=1000, seed=0):
    """PR-AUC of a minus PR-AUC of b on the same rows, with a 95% interval from resampling rows.
    Stands in for Rochan's helper until it's merged; same method."""
    y, a, b = (np.asarray(v) for v in (y, a, b))
    rng = np.random.default_rng(seed)
    diffs = []
    while len(diffs) < reps:
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        diffs.append(pr_auc(y[i], a[i]) - pr_auc(y[i], b[i]))
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return pr_auc(y, a) - pr_auc(y, b), float(lo), float(hi)


# ---------- tuning (step C, decisions 6 and 12) ----------
# Search spaces. model.toml [search.<kind>] overrides these, one table per parameter.
# A choice of "none" means Python None.
DEFAULT_SEARCH = {
    "logreg": {"C": {"type": "float", "low": 1e-3, "high": 100.0, "log": True}},
    "forest": {
        "n_estimators": {"type": "int", "low": 100, "high": 600, "step": 100},
        "min_samples_leaf": {"type": "int", "low": 5, "high": 200, "log": True},
        "max_features": {"type": "categorical", "choices": ["sqrt", 0.3, 0.5]},
        "max_depth": {"type": "categorical", "choices": ["none", 8, 16]},
    },
    "lgbm": {
        "learning_rate": {"type": "float", "low": 0.01, "high": 0.2, "log": True},
        "num_leaves": {"type": "int", "low": 8, "high": 128, "log": True},
        "min_child_samples": {"type": "int", "low": 10, "high": 300, "log": True},
        "n_estimators": {"type": "int", "low": 100, "high": 1000, "step": 50},
        "subsample": {"type": "float", "low": 0.5, "high": 1.0},
        "colsample_bytree": {"type": "float", "low": 0.5, "high": 1.0},
        "reg_lambda": {"type": "float", "low": 1e-3, "high": 10.0, "log": True},
    },
}


def suggest(trial, space):
    """One value per parameter of `space`, from an Optuna trial."""
    params = {}
    for name, s in space.items():
        kind = s["type"]
        if kind == "float":
            params[name] = trial.suggest_float(name, s["low"], s["high"], log=s.get("log", False))
        elif kind == "int":
            if s.get("log"):
                params[name] = trial.suggest_int(name, s["low"], s["high"], log=True)
            else:
                params[name] = trial.suggest_int(name, s["low"], s["high"], step=s.get("step", 1))
        elif kind == "categorical":
            params[name] = trial.suggest_categorical(name, s["choices"])
        else:
            raise ValueError(f"search {name}: unknown type {kind!r}")
    return {k: (None if v == "none" else v) for k, v in params.items()}


def fold_pr_auc(train, kind, seed, params, n_folds=3, imbalance="none"):
    """Mean PR-AUC over the time folds inside train (step C's objective). Never sees val or test."""
    if set(train["split"]) != {"train"}:
        raise PermissionError("tuning folds take train rows only")
    values = []
    for fit_idx, score_idx, _ in time_folds(train, n_folds):
        fit, score = train.loc[fit_idx], train.loc[score_idx]
        _, s = fit_predict(fit, score, kind, seed, params, imbalance)
        values.append(pr_auc(score["label"], s))
    return float(np.mean(values)), values


def best_so_far(values):
    """The running maximum: the curve decision 6 reads."""
    return [float(v) for v in np.maximum.accumulate(np.asarray(values, dtype=float))]


def curve_is_flat(values, window=30, gain=0.002):
    """Decision 6: the last `window` trials raised the best by less than `gain`."""
    if len(values) <= window:
        return False
    curve = best_so_far(values)
    return curve[-1] - curve[-1 - window] < gain


def tune(df, kind, seed, search=None, start=50, step=50, max_trials=200, window=30, gain=0.002, n_folds=3,
         on_trial=None):
    """Optuna TPE on the time folds inside train, in batches (decision 6): `start` trials, then `step` more
    while the curve isn't flat, up to `max_trials`. Returns a dict with every trial, the curve and the best."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    space = (search or DEFAULT_SEARCH)[kind]
    train = fit_rows(df)

    def objective(trial):
        params = suggest(trial, space)
        value, folds = fold_pr_auc(train, kind, seed, params, n_folds)
        trial.set_user_attr("folds", folds)
        if on_trial:
            on_trial(trial.number, params, value, folds)
        return value

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=start)
    while len(study.trials) < max_trials and not curve_is_flat([t.value for t in study.trials], window, gain):
        study.optimize(objective, n_trials=min(step, max_trials - len(study.trials)))
    values = [t.value for t in study.trials]
    stop = "flat" if curve_is_flat(values, window, gain) else "max_trials"
    best = study.best_trial
    return {
        "kind": kind, "seed": seed, "n_trials": len(values), "stopped": stop, "window": window, "gain": gain,
        "best_value": float(best.value), "best_trial": best.number,
        "best_params": {k: (None if v == "none" else v) for k, v in best.params.items()},
        "best_folds": best.user_attrs["folds"],
        "curve": best_so_far(values),
        "trials": [{"number": t.number, "value": t.value, "params": t.params, "folds": t.user_attrs["folds"]}
                   for t in study.trials],
    }


# ---------- seed noise (decision 7) ----------
def seed_noise(df, kind, params, out_dir, seeds=(1, 2, 3, 4, 5)):
    """The chosen config refitted with each seed, scored on val. Writes reports/seed_noise.json
    (Contract 5: mean and standard deviation of val PR-AUC) and every seed's val scores beside it.
    Any gap smaller than this noise isn't real: Rochan's promotion rule and Chaitali's gate read it."""
    if len(seeds) < 2 or len(set(seeds)) != len(seeds):
        raise ValueError("seed noise needs at least two different seeds")
    val = rows(df, "val")
    y = val["label"].astype(int).to_numpy()
    values, scores = [], {}
    for seed, (_, s, scored) in zip(seeds, pmap(fit_and_score, [(df, kind, seed, params) for seed in seeds])):
        assert scored.index.equals(val.index)
        values.append(pr_auc(y, s))
        scores[f"seed_{seed}"] = s
    sd = float(np.std(values, ddof=1))
    result = {"kind": kind, "params": params, "split": "val", "n": int(len(y)), "positives": int(y.sum()),
              "seeds": list(seeds), "pr_auc": values, "mean": float(np.mean(values)), "sd": sd, "two_sd": 2 * sd}
    if sd == 0:
        result["note"] = f"{kind} doesn't depend on the seed with these params, so its noise is 0"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "seed_noise.json").write_text(json.dumps(result, indent=2) + "\n")
    pd.DataFrame({"sha": val["sha"].to_numpy(), "label": y, **scores}).to_csv(
        out_dir / "seed_noise_val_scores.csv", index=False)
    return result


def read_params(path):
    """(kind, params) from a tuning_<kind>.json (best_params) or a candidate.json (params), so seed noise uses
    exactly the config that was tuned or picked."""
    data = json.loads(Path(path).read_text())
    params_key = "best_params" if "best_params" in data else "params"
    if "kind" not in data or params_key not in data:
        raise ValueError(f"{path} isn't a tuning_<kind>.json or a candidate.json (no kind, or no params)")
    return data["kind"], data[params_key] or {}


# ---------- the candidate (decision 1, Contract 4) ----------
SIMPLEST_FIRST = ("logreg", "forest", "lgbm")


def pick_candidate(out_dir, tag, seed, reps=1000):
    """Decision 1's rule on <tag>_val.json and <tag>_val_scores.csv, read from out_dir (no refit, S6): the model with
    the highest val PR-AUC wins; if a simpler model's paired bootstrap interval against it contains 0, the simplest
    such model wins. Writes out_dir/candidate.json (Contract 4) and returns it."""
    out_dir = Path(out_dir)
    summary_path, scores_path = out_dir / f"{tag}_val.json", out_dir / f"{tag}_val_scores.csv"
    for f in (summary_path, scores_path):
        if not f.exists():
            raise FileNotFoundError(f"{f} not found: run --untuned or --tune first")
    summary = json.loads(summary_path.read_text())
    scores = pd.read_csv(scores_path)
    kinds = [k for k in SIMPLEST_FIRST if k in summary.get("params", {}) and k in scores.columns]
    if not kinds:
        raise ValueError(f"{summary_path} names no model to pick from")
    y = scores["label"].to_numpy(dtype=int)
    pr = {k: pr_auc(y, scores[k]) for k in kinds}
    best = max(kinds, key=lambda k: (pr[k], -SIMPLEST_FIRST.index(k)))
    compared = {}
    for k in kinds:
        if k == best:
            compared[k] = {"pr_auc": pr[k], "tied_with_best": True}
            continue
        d, lo, hi = paired_bootstrap(y, scores[best].to_numpy(), scores[k].to_numpy(), reps=reps, seed=seed)
        compared[k] = {"pr_auc": pr[k], "best_minus_this": d, "interval": [lo, hi], "tied_with_best": lo <= 0}
    winner = next(k for k in SIMPLEST_FIRST if k in compared and compared[k]["tied_with_best"])
    candidate = {
        "kind": winner,
        "params": summary["params"][winner] or {},
        "scores_file": scores_path.name,
        "column": winner,
        "seed": seed,
        "tag": tag,
        "decision": "rachna-1",
        "rule": "highest val PR-AUC; a simpler model tied with it (paired bootstrap interval contains 0) wins",
        "best_by_pr_auc": best,
        "val_n": int(len(y)),
        "val_positives": int(y.sum()),
        "bootstrap_reps": reps,
        "compared": compared,
    }
    (out_dir / "candidate.json").write_text(json.dumps(candidate, indent=2) + "\n")
    return candidate


def print_pick(c):
    print(f"candidate: {c['kind']} (decision 1 on {c['tag']}; best by PR-AUC: {c['best_by_pr_auc']}, "
          f"val N = {c['val_n']:,})")
    for k, r in c["compared"].items():
        extra = "" if k == c["best_by_pr_auc"] else (
            f"   best - this {r['best_minus_this']:+.3f} [{r['interval'][0]:+.3f}, {r['interval'][1]:+.3f}]"
            f" {'tied' if r['tied_with_best'] else 'worse'}")
        print(f"  {k:8}{r['pr_auc']:8.3f}{extra}")


# ---------- the model file (registry, P9) ----------
MODEL_FILE = "model.joblib"
SAME_MODEL = 1e-9   # decision 9's bar: a refit is the same model only if every val prediction is within this


def file_md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def save_model(df, candidate_path):
    """Refit the candidate on train (its kind, params and seed), check that its val predictions equal the ones in its
    scores file (S6 and decision 9: within 1e-9, so it's the model that was judged), then save it with joblib next to
    candidate.json and record model_file and model_md5 in candidate.json. The registry pushes this file; P9 loads it."""
    candidate_path = Path(candidate_path)
    c = json.loads(candidate_path.read_text())
    model, s, scored = fit_and_score(df, c["kind"], int(c["seed"]), c["params"] or None)
    scores = pd.read_csv(candidate_path.parent / c["scores_file"])
    if len(scores) != len(scored) or not (scores["sha"].to_numpy() == scored["sha"].to_numpy()).all():
        raise ValueError(f"{c['scores_file']} isn't one row per val row of this table, in order (Contract 4)")
    diff = float(np.max(np.abs(scores[c["column"]].to_numpy(dtype=float) - s)))
    if diff > SAME_MODEL:
        raise ValueError(f"the refit {c['kind']} differs from {c['scores_file']} by up to {diff:.2e} "
                         f"(bar {SAME_MODEL}): not the model that was judged. Same table, config and code as that run?")
    import joblib
    path = candidate_path.parent / MODEL_FILE
    joblib.dump(model, path)
    c.update(model_file=MODEL_FILE, model_md5=file_md5(path), refit_max_diff=diff)
    candidate_path.write_text(json.dumps(c, indent=2) + "\n")
    return path, c


def input_columns(model):
    """The columns a saved model was fitted on, in order (scikit-learn and LightGBM both record them)."""
    cols = getattr(model, "feature_names_in_", None)
    if cols is None:
        raise ValueError("this model doesn't record its input columns; was it fitted on a DataFrame?")
    return [str(c) for c in cols]


def inputs_for(model, df):
    """The exact input matrix a saved model was fitted on, for df's rows. Ticket categories come from the model's own
    columns, so a type it never saw is all zeros, as in training."""
    cols = input_columns(model)
    cats = [c.split("=", 1)[1] for c in cols if c.startswith("ticket_type=")]
    X = feature_matrix(df, cats, ticket_features=any(c in TICKET for c in cols))
    missing = [c for c in cols if c not in X.columns]
    if missing:
        raise ValueError(f"the model needs column(s) the rows don't give: {', '.join(missing)}")
    return X[cols]


def score_with(model, df):
    """Risk scores for df's rows from a saved model (P9's loader)."""
    return model.predict_proba(inputs_for(model, df))[:, 1]


def bootstrap_reps(cfg, default):
    """Rochan's model.toml [eval] bootstrap_reps (his decision 4) when set, so the pick and evaluate.py agree."""
    return int(cfg.get("eval", {}).get("bootstrap_reps", default))


# ---------- the command ----------
def load_config(path):
    path = Path(path)
    if not path.exists():
        print(f"note: {path} not found, using Contract 2 defaults (seed 1, train 2015-2022, val 2023)")
        return DEFAULT_CONFIG
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    if cfg.get("split", {}).get("test_allowed"):
        print("note: test_allowed is true, but train.py never reads test rows anyway")
    return cfg


def load_features(path):
    path = Path(path)
    if not path.exists():
        raise FeatureError(f"{path} not found: Utkarsh's table goes in Model-Pipeline/data/")
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def evaluate(df, seed, out_dir, models, tag, reps=1000):
    """Baselines and each model in `models` ({kind: params or None}), fitted on train, on val.
    Writes <tag>_val.json (PR-AUC with N, paired bootstrap against lines x files) and <tag>_val_scores.csv."""
    val = rows(df, "val")
    y = val["label"].astype(int).to_numpy()
    scores = dict(baseline_scores(val))
    fits = pmap(fit_and_score, [(df, kind, seed, params) for kind, params in models.items()])
    for kind, (_, s, scored) in zip(models, fits):
        assert scored.index.equals(val.index)
        scores[kind] = s
    ref = scores["lines_x_files"]
    summary = {"split": "val", "tag": tag, "n": int(len(y)), "positives": int(y.sum()),
               "positive_rate": float(y.mean()), "seed": seed, "bootstrap_reps": reps,
               "params": {k: v for k, v in models.items()}, "results": {}}
    for name, s in scores.items():
        row = {"pr_auc": pr_auc(y, s)}
        if name != "lines_x_files":
            d, lo, hi = paired_bootstrap(y, s, ref, reps=reps, seed=seed)
            row.update(vs_lines_x_files=d, interval=[lo, hi])
        summary["results"][name] = row
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{tag}_val.json").write_text(json.dumps(summary, indent=2) + "\n")
    pd.DataFrame({"sha": val["sha"].to_numpy(), "label": y, **scores}).to_csv(
        out_dir / f"{tag}_val_scores.csv", index=False)
    return summary


def untuned(df, seed, out_dir, reps=1000, kinds=MODEL_KINDS):
    return evaluate(df, seed, out_dir, {k: None for k in kinds}, "untuned", reps)


def search_space(cfg):
    """DEFAULT_SEARCH with model.toml [search.<kind>.<param>] tables laid over it."""
    merged = {k: dict(v) for k, v in DEFAULT_SEARCH.items()}
    for kind, params in cfg.get("search", {}).items():
        if kind not in merged:
            raise ValueError(f"model.toml [search.{kind}]: unknown model kind")
        merged[kind].update(params)
    return merged


def log_to_mlflow(summary, out_dir, decision, config, features):
    """Through Rochan's tracking.py (Contract 3), tagged with the config and table this command was given, so a run
    on another table never carries the real table's md5. With no model.toml there's no config to hash: say so."""
    try:
        from model import tracking
    except ImportError:
        print("not logged to MLflow: model/tracking.py (Rochan) isn't merged yet")
        return
    if not Path(config).exists():
        print(f"not logged to MLflow: {config} doesn't exist, so the run has no config to carry (Contract 2)")
        return
    import mlflow
    with tracking.start_run(summary["tag"], owner="rachna", decision=decision, config=config, features=features):
        mlflow.log_param("seed", summary["seed"])
        for name, r in summary["results"].items():
            mlflow.log_metric(f"val_pr_auc_{name}", r["pr_auc"])
        mlflow.log_metric("val_n", summary["n"])
        mlflow.log_artifacts(str(out_dir))


def print_summary(s):
    print(f"val: N = {s['n']:,}, positives = {s['positives']:,} ({s['positive_rate']:.1%})")
    print(f"{'':16}{'PR-AUC':>8}   vs lines x files (95% paired bootstrap, {s['bootstrap_reps']} reps)")
    for name, r in s["results"].items():
        extra = ""
        if "interval" in r:
            lo, hi = r["interval"]
            extra = f"   {r['vs_lines_x_files']:+.3f} [{lo:+.3f}, {hi:+.3f}]"
        print(f"{name:16}{r['pr_auc']:8.3f}{extra}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Rachna: baselines and models on val. Run from Model-Pipeline/.")
    p.add_argument("--config", default=str(HERE / "model.toml"))
    p.add_argument("--features", default=str(HERE / "data" / "features.parquet"))
    p.add_argument("--out", default=str(HERE / "reports"))
    p.add_argument("--reps", type=int, default=1000, help="bootstrap resamples")
    p.add_argument("--untuned", action="store_true", help="baselines and the three models at their defaults")
    p.add_argument("--models", nargs="+", choices=MODEL_KINDS, default=list(MODEL_KINDS))
    p.add_argument("--tune", nargs="+", choices=MODEL_KINDS, help="tune these on the folds inside train, "
                   "then score the best of each on val")
    p.add_argument("--retrain", nargs="+", choices=MODEL_KINDS, help="--tune without the search: fit each kind with "
                   "the best params in its tuning_<kind>.json in --out, score val, pick (CI, decision chaitali-7)")
    p.add_argument("--start", type=int, default=50, help="first batch of trials (decision 6)")
    p.add_argument("--step", type=int, default=50, help="trials added while the curve isn't flat")
    p.add_argument("--max-trials", type=int, default=200)
    p.add_argument("--seed-noise", metavar="TUNING_JSON", help="refit the config in this tuning_<kind>.json "
                   "with seeds 1-5 and write seed_noise.json (decision 7)")
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    p.add_argument("--check", action="store_true", help="only check the feature table against Contract 1")
    p.add_argument("--pick", metavar="TAG", help="write reports/candidate.json from <TAG>_val.json and its scores "
                   "(decision 1; --tune does this itself on the tuned run)")
    p.add_argument("--save-model", action="store_true", help="refit reports/candidate.json's model, check it's the "
                   "judged one, save reports/model.joblib (--tune and --pick do this themselves)")
    a = p.parse_args(argv)
    if not (a.untuned or a.tune or a.retrain or a.seed_noise or a.check or a.pick or a.save_model):
        p.error("nothing to do: pass --check, --untuned, --tune, --retrain, --pick, --save-model or --seed-noise")
    cfg = load_config(a.config)
    try:
        df = load_features(a.features)
        sp = cfg["split"]
        check_table(df, {"train": sp["train"], "val": sp["val"], "test": sp["test"]})
    except FeatureError as e:
        sys.exit(f"feature table: {e}")
    if a.check:
        counts = df["split"].value_counts()
        parts = ", ".join(f"{s} {int(counts.get(s, 0)):,}" for s in ("train", "val", "test", "none"))
        print(f"feature table ok: {len(df):,} rows: {parts}")
        for col in CAN_BE_NEGATIVE:
            neg = df[col] < 0
            if neg.any():
                print(f"note: {col} is below 0 on {int(neg.sum())} rows (min {df[col].min():.2f}); "
                      f"the models read those as 0")
    seed = int(cfg["seed"]["value"])
    out = Path(a.out)
    if a.untuned:
        summary = untuned(df, seed, out, a.reps, a.models)
        print_summary(summary)
        log_to_mlflow(summary, out, "rachna-1", a.config, a.features)
        print(f"wrote {out / 'untuned_val.json'} and untuned_val_scores.csv")
    if a.tune or a.retrain:
        best = {}
        if a.tune:
            space = search_space(cfg)
            results = pmap(tune, [(df, kind, seed, space, a.start, a.step, a.max_trials) for kind in a.tune])
            for kind, result in zip(a.tune, results):
                out.mkdir(parents=True, exist_ok=True)
                (out / f"tuning_{kind}.json").write_text(json.dumps(result, indent=2) + "\n")
                print(f"{kind}: {result['n_trials']} trials, stopped {result['stopped']}, "
                      f"best fold PR-AUC {result['best_value']:.3f} (trial {result['best_trial']})")
                best[kind] = result["best_params"]
        else:  # the same fits on this machine, so the scores and the saved model's check agree (S8)
            for kind in a.retrain:
                best[kind] = read_params(out / f"tuning_{kind}.json")[1]
                print(f"{kind}: best params from tuning_{kind}.json, no search")
        summary = evaluate(df, seed, out, best, "tuned", a.reps)
        print_summary(summary)
        log_to_mlflow(summary, out, "rachna-6" if a.tune else None, a.config, a.features)
        print(f"wrote {out / 'tuned_val.json'}, tuned_val_scores.csv and tuning_<kind>.json")
        print_pick(pick_candidate(out, "tuned", seed, bootstrap_reps(cfg, a.reps)))
        print(f"wrote {out / 'candidate.json'}")
    if a.pick:
        print_pick(pick_candidate(out, a.pick, seed, bootstrap_reps(cfg, a.reps)))
        print(f"wrote {out / 'candidate.json'}")
    if a.tune or a.retrain or a.pick or a.save_model:
        path, c = save_model(df, out / "candidate.json")
        print(f"saved {path} ({path.stat().st_size / 1e6:.1f} MB, md5 {c['model_md5']}); the refit matches the judged "
              f"scores to {c['refit_max_diff']:.1e}")
    if a.seed_noise:
        kind, params = read_params(a.seed_noise)
        r = seed_noise(df, kind, params, out, tuple(a.seeds))
        print(f"seed noise, {kind}, val N = {r['n']:,}: PR-AUC mean {r['mean']:.4f}, sd {r['sd']:.4f} "
              f"over seeds {r['seeds']}")
        print(f"wrote {out / 'seed_noise.json'} and seed_noise_val_scores.csv")


if __name__ == "__main__":
    main()
