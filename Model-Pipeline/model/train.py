"""Baselines, models and tuning for the defect-risk model (AI 1, step C).

Reads the feature table of Contract 1 (Model-Pipeline/packets/README.md). Every fit goes through `fit_rows`,
which only ever returns `train` rows, and `rows` refuses `test` outright: nobody looks at 2024 until Nov 12.
"""
import numpy as np
from sklearn.metrics import average_precision_score

# Contract 1: every column the table must have.
ID_COLUMNS = ["sha", "authored_at", "year", "split", "label"]
SIZE = ["la", "ld", "nf", "nd", "ns", "entropy"]
HISTORY = ["ndev", "age", "nuc", "exp", "sexp", "prior_bug_fixes"]
TICKET = ["fix", "has_ticket"]
OTHER = ["is_minor"]
NUMERIC = SIZE + HISTORY + TICKET + OTHER
CATEGORICAL = ["ticket_type"]
SLICES = ["module", "component", "exp_bucket", "size_bucket"]   # AI 3's bias slices: never model inputs
CONTRACT_COLUMNS = ID_COLUMNS + NUMERIC + CATEGORICAL + SLICES

SPLITS = {"train", "val", "test", "none"}
LABELLED = {"train", "val", "test"}


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
        raise PermissionError("train.py never reads test rows; the one 2024 run is AI 2's, on Nov 12")
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
    A ticket_type not in `categories` gets all zeros. Slice columns are never included."""
    numeric = [c for c in NUMERIC if c not in drop and (ticket_features or c not in TICKET)]
    X = df[numeric].astype(float)
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
        rf = dict(n_estimators=300, min_samples_leaf=20, max_features="sqrt", n_jobs=-1)
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
    # Decision 9: deterministic trees, one fixed thread count, so laptops, the image and CI agree.
    return lightgbm.LGBMClassifier(class_weight=weight, random_state=seed, deterministic=True,
                                   force_row_wise=True, n_jobs=1, verbose=-1, **gbm)


def fit_and_score(df, kind, seed, params=None, imbalance="none", ticket_features=True, drop=(),
                  score_split="val", model=None):
    """Fit on train rows only, score `score_split`. Returns (fitted model, scores, scored rows).
    `model` lets a test pass its own estimator in place of build_model's."""
    train = fit_rows(df)
    scored = rows(df, score_split)
    cats = ticket_categories(train)
    X_fit = feature_matrix(train, cats, ticket_features, drop)
    X_score = feature_matrix(scored, cats, ticket_features, drop)
    y_fit = train["label"].astype(int).to_numpy()
    if model is None:
        model = build_model(kind, seed, params, imbalance, pos_rate=float(y_fit.mean()))
    model.fit(X_fit, y_fit)
    return model, model.predict_proba(X_score)[:, 1], scored


def chance_ceiling(y, q=0.99, reps=300, seed=0):
    """The q-quantile of PR-AUC that purely random scores reach on these labels. A model trained on shuffled
    labels should stay below it: at a few thousand rows, chance alone lands up to ~0.02 above the positive rate."""
    rng = np.random.default_rng(seed)
    y = np.asarray(y, dtype=int)
    return float(np.quantile([pr_auc(y, rng.random(len(y))) for _ in range(reps)], q))
