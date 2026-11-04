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
