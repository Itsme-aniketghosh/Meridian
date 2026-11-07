"""Evaluation (step D, Rochan): the numbers the pass bars, the gate and the registry read.

The scores come from the file train.py wrote: one row per row of the split, in the feature table's order, one column
per model and baseline (packets/README.md, Contract 4). Nothing here refits a model (S6). For the candidate and every
baseline, `reports/metrics.json` holds, each with N and the positive rate next to it:

- PR-AUC (average precision, as in train.py) with its bootstrap interval, and ROC-AUC
- precision, recall, F1 and the alert rate at a threshold chosen on `val` (decision 3); `test` reuses `val`'s
- recall at 20% of changed lines, reading commits by risk per line (decision 2)
- the PR-AUC difference against lines x files, with a paired bootstrap interval (decisions 4 and 5)

`paired_bootstrap` is the team's one copy (S3): train.py, bias.py and gate.py import it from here.

    python -m model.evaluate                          # the candidate named in reports/candidate.json, on val
    python -m model.evaluate --scores reports/untuned_val_scores.csv --column lgbm
    python -m model.evaluate --split test --scores <2024 scores>        # refused unless [split] test_allowed
    python -m model.evaluate --decide 1 2 3 4 5 --scores <val scores> --column lgbm    # the decision tests
"""
import argparse
import hashlib
import itertools
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

HERE = Path(__file__).resolve().parents[1]  # Model-Pipeline/
MODEL_TOML = HERE / "model.toml"
FEATURES = HERE / "data" / "features.parquet"
REPORTS = HERE / "reports"
CANDIDATE = REPORTS / "candidate.json"

BASELINES = ("lines_x_files", "churn", "bug_fixes")  # train.py's: a formula each, never fitted
REFERENCE = "lines_x_files"  # PLAN §4: the model ships only if it beats this one
RULES = ("best_f1", "precision", "alert_rate")  # decision 3
RANKINGS = ("risk", "risk_per_line")  # decision 2
EVAL_KEYS = ("bootstrap_reps", "interval", "effort", "effort_ranking", "threshold", "threshold_precision",
             "threshold_min_alerts", "threshold_alert_rate")  # model.toml [eval], all required


class ScoresError(ValueError):
    """The scores file doesn't hold the rows it should (Contract 4). The message says how."""


# ---------- metrics ----------

def _y(y):
    y = np.asarray(y)
    if y.ndim != 1 or len(y) == 0 or not np.isin(y, (0, 1)).all():
        raise ValueError("labels must be a non-empty list of 0 and 1")
    if y.min() == y.max():
        raise ValueError("the labels need both classes: with one, PR-AUC and ROC-AUC mean nothing")
    return y.astype(int)


def pr_auc(y, score):
    """Average precision, as in train.py: one definition of PR-AUC for the team."""
    return float(average_precision_score(_y(y), np.asarray(score, dtype=float)))


def roc_auc(y, score):
    return float(roc_auc_score(_y(y), np.asarray(score, dtype=float)))


def _tie_groups(key):
    """Rows by key, highest first, and where each run of equal keys starts. A tie is one step, as in scikit-learn's
    curves, so the order inside a tie never changes a number."""
    order = np.argsort(-key, kind="stable")
    k = key[order]
    return order, np.flatnonzero(np.r_[True, k[1:] != k[:-1]])


def _group_counts(y, order, starts, w):
    """Positives and negatives in each tie group, for each row of w (resamples x rows: how often each row was drawn)."""
    ws = w[:, order]
    pos = np.add.reduceat(ws * y[order], starts, axis=1)
    return pos, np.add.reduceat(ws, starts, axis=1) - pos


def _average_precision(pos, neg):
    """sum over thresholds of (recall gained) x (precision there): scikit-learn's average_precision_score."""
    tp, fp = np.cumsum(pos, axis=1), np.cumsum(neg, axis=1)
    precision = np.divide(tp, tp + fp, out=np.zeros_like(tp), where=tp + fp > 0)
    return (pos * precision).sum(axis=1) / tp[:, -1]


def _roc_auc(pos, neg):
    """Share of (positive, negative) pairs ordered right, a tie counting half: scikit-learn's roc_auc_score."""
    below = neg.sum(axis=1, keepdims=True) - np.cumsum(neg, axis=1)  # negatives scored lower than the group
    return (pos * (below + neg / 2)).sum(axis=1) / (pos.sum(axis=1) * neg.sum(axis=1))


def resamples(y, reps, seed, chunk=250):
    """Bootstrap resamples of the rows, as counts (how often each row is drawn), `chunk` resamples at a time.

    Drawn the way train.py's stand-in draws them: one rng.integers(0, n, n) per resample, and a resample with one
    class only is drawn again. So the same seed gives the same resamples, and the same numbers, as before."""
    y = _y(y)
    rng = np.random.default_rng(seed)
    n, made = len(y), 0
    while made < reps:
        w = np.empty((min(chunk, reps - made), n))
        j = 0
        while j < len(w):
            i = rng.integers(0, n, n)
            if 0 < y[i].sum() < n:
                w[j] = np.bincount(i, minlength=n)
                j += 1
        made += len(w)
        yield w


def bootstrap(y, scores, reps, seed, metric="pr_auc"):
    """{name: the metric on each of `reps` resamples}. Every score is measured on the same resamples, so the
    difference between two of them, resample by resample, is the paired bootstrap."""
    fn = {"pr_auc": _average_precision, "roc_auc": _roc_auc}[metric]
    y = _y(y).astype(float)
    groups = {name: _tie_groups(np.asarray(s, dtype=float)) for name, s in scores.items()}
    out = {name: [] for name in scores}
    for w in resamples(y, reps, seed):
        for name, (order, starts) in groups.items():
            out[name].append(fn(*_group_counts(y, order, starts, w)))
    return {name: np.concatenate(v) for name, v in out.items()}


def interval(values, level=0.95):
    """The central `level` interval of bootstrap values: [2.5%, 97.5%] at 0.95."""
    a = round((1 - level) / 2, 12)
    lo, hi = np.quantile(values, [a, 1 - a])
    return float(lo), float(hi)


def paired_bootstrap(y, a, b, reps=1000, seed=0, level=0.95):
    """PR-AUC of score a minus PR-AUC of score b on the same rows, with its `level` interval from resampling the rows
    (both scores on each resample). Returns (difference, low, high). The team's one copy (packets/README.md S3)."""
    d = bootstrap(y, {"a": a, "b": b}, reps, seed)
    lo, hi = interval(d["a"] - d["b"], level)
    return pr_auc(y, a) - pr_auc(y, b), lo, hi


def recall_at_effort(y, score, lines, effort=0.2, ranking="risk_per_line"):
    """The share of bug-inducing commits a reviewer finds reading commits in order until `effort` of all changed
    lines are read (decision 2).

    ranking "risk" reads the highest score first; "risk_per_line" the highest score per changed line first. A commit
    with no changed lines counts as 1 line. Commits with the same key are read as one group: the group the budget
    ends in is read in part, the share of its lines that fits, and finds that share of its bugs (the expected count
    if ties were read in random order)."""
    if ranking not in RANKINGS:
        raise ValueError(f"ranking {ranking!r}: one of {RANKINGS}")
    if not 0 < effort <= 1:
        raise ValueError(f"effort {effort!r}: a share of the lines, above 0 and at most 1")
    y = _y(y).astype(float)
    cost = np.maximum(np.asarray(lines, dtype=float), 1.0)
    key = np.asarray(score, dtype=float)
    if ranking == "risk_per_line":
        key = key / cost
    order, starts = _tie_groups(key)
    group_lines, group_bugs = np.add.reduceat(cost[order], starts), np.add.reduceat(y[order], starts)
    budget = effort * cost.sum()
    read = np.cumsum(group_lines)
    whole = int(np.searchsorted(read, budget, side="right"))  # groups read whole
    found = group_bugs[:whole].sum()
    if whole < len(read):
        found += group_bugs[whole] * (budget - (read[whole - 1] if whole else 0.0)) / group_lines[whole]
    return {"value": float(found / y.sum()), "found": float(found), "bugs": int(y.sum()),
            "lines_budget": float(budget), "commits_read_whole": int(starts[whole]) if whole < len(read) else len(y)}


def _flag_curve(y, score):
    """For each distinct score t, highest first: the alerts and true positives when commits scoring t or more are
    flagged."""
    order, starts = _tie_groups(score)
    ends = np.r_[starts[1:], len(score)]
    return score[order][starts], ends.astype(float), np.cumsum(y[order])[ends - 1]


def choose_threshold(y, score, rule, precision=0.8, min_alerts=30, alert_rate=0.05):
    """The score at or above which a commit is flagged, chosen by `rule` on these rows (decision 3). None when the
    rule can't be met.

    best_f1      the highest F1 (on a tie, the fewer alerts)
    precision    the lowest threshold whose precision is at least `precision`, on at least `min_alerts` alerts
    alert_rate   the highest threshold that flags at least `alert_rate` of the rows"""
    y = _y(y).astype(float)
    score = np.asarray(score, dtype=float)
    t, alerts, tp = _flag_curve(y, score)
    if rule == "best_f1":
        return float(t[np.argmax(2 * tp / (alerts + y.sum()))])
    if rule == "precision":
        ok = np.flatnonzero((tp / alerts >= precision) & (alerts >= min_alerts))
        return float(t[ok[-1]]) if len(ok) else None
    if rule == "alert_rate":
        if not 0 < alert_rate <= 1:
            raise ValueError(f"alert_rate {alert_rate!r}: a share of the rows, above 0 and at most 1")
        return float(t[np.searchsorted(alerts, math.ceil(alert_rate * len(score) - 1e-9))])
    raise ValueError(f"threshold rule {rule!r}: one of {RULES}")


def at_threshold(y, score, threshold):
    """Precision, recall, F1, alert rate and the confusion counts when commits scoring `threshold` or more are
    flagged. One push is one commit here, so alerts per push are 0 or 1 (decision 3)."""
    y = _y(y)
    flag = np.zeros(len(y), bool) if threshold is None else np.asarray(score, dtype=float) >= threshold
    tp, fp = int((flag & (y == 1)).sum()), int((flag & (y == 0)).sum())
    fn, tn = int((~flag & (y == 1)).sum()), int((~flag & (y == 0)).sum())
    return {"threshold": threshold, "alerts": tp + fp, "alert_rate": (tp + fp) / len(y),
            "median_alerts_per_push": float(np.median(flag)),
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn),
            "f1": 2 * tp / (2 * tp + fp + fn), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


# ---------- inputs ----------

def eval_settings(cfg):
    """model.toml [eval], every key present and in range."""
    s = cfg.get("eval", {})
    missing = [k for k in EVAL_KEYS if k not in s]
    if missing:
        raise ValueError(f"model.toml [eval] is missing {', '.join(missing)}")
    if s["threshold"] not in RULES:
        raise ValueError(f"model.toml [eval] threshold {s['threshold']!r}: one of {RULES}")
    if s["effort_ranking"] not in RANKINGS:
        raise ValueError(f"model.toml [eval] effort_ranking {s['effort_ranking']!r}: one of {RANKINGS}")
    if not (isinstance(s["bootstrap_reps"], int) and s["bootstrap_reps"] >= 100):
        raise ValueError("model.toml [eval] bootstrap_reps: a whole number, at least 100")
    if not 0 < s["interval"] < 1:
        raise ValueError("model.toml [eval] interval: between 0 and 1, such as 0.95")
    return {k: s[k] for k in EVAL_KEYS}


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rel(path):
    """Same text on every OS; outside Model-Pipeline/ only the file name, so no home folder lands in metrics.json."""
    path = Path(path).resolve()
    return path.relative_to(HERE).as_posix() if path.is_relative_to(HERE) else path.name


def read_candidate(path):
    """reports/candidate.json (Contract 4): kind, params, scores_file, column, seed. scores_file is read relative to
    candidate.json's folder, then to Model-Pipeline/."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("scores_file", "column") if k not in data]
    if missing:
        raise ScoresError(f"{path}: no {', '.join(missing)} (Contract 4: kind, params, scores_file, column, seed)")
    f = Path(data["scores_file"])
    if not f.is_absolute():
        f = path.parent / f if (path.parent / f).exists() else HERE / f
    return {**data, "scores_file": f}


def load_scores(table, scores_path, split, column):
    """The split's rows of the feature table and the scores file, checked against each other row by row.
    Returns (rows, {score column: float array}). Only the named split is ever taken from the table."""
    rows = table[table["split"] == split].reset_index(drop=True)
    if rows.empty:
        raise ScoresError(f"the feature table has no {split} rows")
    scores = pd.read_csv(scores_path)
    where = f"{scores_path} against the {split} rows of the feature table"
    for c in ("sha", "label", column, REFERENCE):
        if c not in scores.columns:
            raise ScoresError(f"{scores_path}: no {c!r} column (has {', '.join(scores.columns)})")
    if len(scores) != len(rows):
        raise ScoresError(f"{where}: {len(scores):,} rows against {len(rows):,}")
    off = np.flatnonzero(scores["sha"].to_numpy() != rows["sha"].to_numpy())
    if len(off):
        raise ScoresError(f"{where}: {len(off):,} rows differ (Contract 4: one row per {split} row, in the table's "
                          f"order), first at row {off[0]}: {scores['sha'].iloc[off[0]]}")
    if not (scores["label"].to_numpy() == rows["label"].to_numpy()).all():
        raise ScoresError(f"{where}: the labels differ")
    out = {}
    for c in scores.columns.drop(["sha", "label"]):
        v = pd.to_numeric(scores[c], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(v).all():
            raise ScoresError(f"{scores_path}: column {c!r} has {int((~np.isfinite(v)).sum())} empty or "
                              "non-numeric scores")
        out[c] = v
    return rows, out


# ---------- metrics.json ----------

def role(name, candidate):
    return "candidate" if name == candidate else "baseline" if name in BASELINES else "model"


def model_metrics(y, scores, lines, candidate, settings, seed, thresholds):
    """metrics.json's "models": every score, with N and the positive rate next to each number. thresholds: the one
    chosen on val for each score."""
    y = _y(y)
    base = {"n": len(y), "positive_rate": float(y.mean())}
    boot = bootstrap(y, scores, settings["bootstrap_reps"], seed)
    reference = pr_auc(y, scores[REFERENCE])
    out = {}
    for name, s in scores.items():
        value = pr_auc(y, s)
        m = {"role": role(name, candidate),
             "pr_auc": {"value": value, "interval": list(interval(boot[name], settings["interval"])), **base},
             "roc_auc": {"value": roc_auc(y, s), **base},
             "at_threshold": {**at_threshold(y, s, thresholds[name]), "rule": settings["threshold"],
                              "chosen_on": "val", **base},
             "recall_at_effort": {**recall_at_effort(y, s, lines, settings["effort"], settings["effort_ranking"]),
                                  "effort": settings["effort"], "ranking": settings["effort_ranking"], **base}}
        if name != REFERENCE:
            diff = boot[name] - boot[REFERENCE]
            m[f"vs_{REFERENCE}"] = {"pr_auc_difference": value - reference, "paired": True,
                                    "interval": list(interval(diff, settings["interval"])), **base}
        out[name] = m
    return out


def thresholds_on(y, scores, settings):
    return {name: choose_threshold(y, s, settings["threshold"], settings["threshold_precision"],
                                   settings["threshold_min_alerts"], settings["threshold_alert_rate"])
            for name, s in scores.items()}


def without_a_model(y, lines, effort):
    """What recall at `effort` of the lines is with no model: reading the smallest commits first, and reading in
    random order (the expected recall is the effort itself). The first is the number to beat (decision 2)."""
    smallest = recall_at_effort(y, -np.maximum(np.asarray(lines, dtype=float), 1.0), lines, effort, "risk")
    return {"smallest_first": smallest["value"], "random_order": effort, "effort": effort,
            "commits_read_whole": smallest["commits_read_whole"], "n": len(y), "positive_rate": float(np.mean(y))}


def metrics(split, rows, scores, candidate, settings, seed, thresholds, sources):
    y = rows["label"].to_numpy()
    lines = (rows["la"] + rows["ld"]).to_numpy()
    return {"split": split, "n": len(y), "positives": int(y.sum()), "positive_rate": float(y.mean()),
            "candidate": candidate, "reference": REFERENCE, **sources,
            "settings": {**settings, "bootstrap_seed": seed},
            "recall_at_effort_without_a_model": without_a_model(y, lines, settings["effort"]),
            "models": model_metrics(y, scores, lines, candidate["column"], settings, seed, thresholds)}


# ---------- charts ----------

SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, GRAY = "#2a78d6", "#eb6834", "#b4b2a9"  # candidate, lines x files, the rest
BLUES = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95")  # sequential, light to dark


def _axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(color=GRID, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)


def _title(fig, title, subtitle):
    fig.text(0.02, 0.97, title, ha="left", va="top", fontsize=12, color=INK, weight="bold")
    fig.text(0.02, 0.91, subtitle, ha="left", va="top", fontsize=9, color=INK2)


def charts(m, y, scores, out_dir):
    """PR-AUC bars with intervals, the candidate's confusion matrix, PR curves against lines x files. Returns the
    files written."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    split, cand, models = m["split"], m["candidate"]["column"], m["models"]
    n, pos, rate = m["n"], m["positives"], m["positive_rate"]
    color = {name: BLUE if name == cand else ORANGE if name == REFERENCE else GRAY for name in models}
    label = {name: name.replace("_x_", " × ").replace("_", " ") for name in models}
    files = []

    def save(fig, name):
        path = Path(out_dir) / f"{name}_{split}.png"
        fig.savefig(path, dpi=150, facecolor=SURFACE, metadata={"Software": None})
        plt.close(fig)
        files.append(path)

    # 1. PR-AUC with intervals, best first
    names = sorted(models, key=lambda k: models[k]["pr_auc"]["value"])
    height = 1.2 + 0.5 * len(names)
    fig, ax = plt.subplots(figsize=(9, height), facecolor=SURFACE)
    fig.subplots_adjust(left=0.15, right=0.76, top=1 - 0.75 / height, bottom=0.6 / height)
    _axes(ax)
    ax.grid(axis="y", visible=False)
    for i, name in enumerate(names):
        p = models[name]["pr_auc"]
        ax.barh(i, p["value"], height=0.42, color=color[name])
        ax.plot(p["interval"], [i, i], color=INK2, linewidth=1.5, solid_capstyle="butt")
        ax.text(p["interval"][1] + 0.01, i, f"{p['value']:.3f}  [{p['interval'][0]:.3f}, {p['interval'][1]:.3f}]",
                va="center", fontsize=8.5, color=INK2)
    ax.axvline(rate, color=MUTED, linewidth=1)
    ax.text(rate, len(names) - 0.45, f" chance = positive rate {rate:.3f}", fontsize=8, color=MUTED, va="bottom")
    ax.set_yticks(range(len(names)), [label[k] for k in names])
    ax.set_xlim(0, min(1, max(models[k]["pr_auc"]["interval"][1] for k in names) + 0.12))
    ax.set_xlabel("PR-AUC (average precision)", color=INK2, fontsize=9)
    ax.legend(handles=[Patch(color=BLUE, label="candidate"), Patch(color=ORANGE, label="lines × files (the bar)"),
                       Patch(color=GRAY, label="others")], loc="lower left", bbox_to_anchor=(1.02, 0.0),
              frameon=False, fontsize=8.5, labelcolor=INK2)
    _title(fig, f"PR-AUC on {split}, with 95% bootstrap intervals",
           f"N = {n:,} commits, {pos:,} bug-inducing ({rate:.1%}); {m['settings']['bootstrap_reps']:,} resamples")
    save(fig, "pr_auc")

    # 2. the candidate's confusion matrix at its threshold
    t = models[cand]["at_threshold"]
    cells = [[t["tp"], t["fn"]], [t["fp"], t["tn"]]]
    fig, ax = plt.subplots(figsize=(6.4, 4.2), facecolor=SURFACE)
    fig.subplots_adjust(left=0.27, right=0.95, top=0.72, bottom=0.14)
    ax.set_facecolor(SURFACE)
    for r, row in enumerate(cells):
        total = sum(row)
        for c, count in enumerate(row):
            share = count / total if total else 0.0
            step = min(len(BLUES) - 1, int(share * len(BLUES)))
            ax.add_patch(plt.Rectangle((c, r), 0.98, 0.98, color=BLUES[step]))
            ax.text(c + 0.49, r + 0.49, f"{count:,}\n{share:.1%} of the row", ha="center", va="center",
                    fontsize=10, color="#ffffff" if step >= 3 else INK)
    ax.set_xlim(0, 2)
    ax.set_ylim(2, 0)
    ax.set_xticks([0.49, 1.49], ["flagged", "not flagged"])
    ax.set_yticks([0.49, 1.49], [f"bug-inducing ({pos:,})", f"clean ({n - pos:,})"])
    ax.tick_params(length=0, colors=INK2, labelsize=9)
    for side in ax.spines.values():
        side.set_visible(False)
    thr = "none reached" if t["threshold"] is None else f"{t['threshold']:.4g}"
    prec = "n/a" if t["precision"] is None else f"{t['precision']:.2f}"
    _title(fig, f"{label[cand]} on {split}, threshold {thr} ({t['rule']}, chosen on val)",
           f"{t['alerts']:,} alerts ({t['alert_rate']:.1%} of commits): precision {prec}, "
           f"recall {t['recall']:.2f}, F1 {t['f1']:.2f}")
    save(fig, "confusion")

    # 3. PR curves: the candidate against lines x files
    fig, ax = plt.subplots(figsize=(7, 5), facecolor=SURFACE)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.84, bottom=0.11)
    _axes(ax)
    handles = []
    for name in (REFERENCE, cand):
        p, r, _ = precision_recall_curve(y, scores[name])
        # the last point is scikit-learn's (recall 0, precision 1) end marker, not a threshold: left out
        ax.step(r[:-1], p[:-1], where="post", color=color[name], linewidth=2, solid_joinstyle="round")
        handles.append(Line2D([], [], color=color[name], linewidth=2,
                              label=f"{label[name]}, PR-AUC {models[name]['pr_auc']['value']:.3f}"))
    if t["threshold"] is not None and t["alerts"]:
        ax.plot(t["recall"], t["precision"], "o", markersize=8, color=BLUE, markeredgecolor=SURFACE,
                markeredgewidth=2)
        ax.annotate(f"threshold: precision {t['precision']:.2f}, recall {t['recall']:.2f}",
                    (t["recall"], t["precision"]), xytext=(10, 6), textcoords="offset points", fontsize=8.5,
                    color=INK2)
    ax.axhline(rate, color=MUTED, linewidth=1)
    ax.text(0.01, rate, f"chance = positive rate {rate:.3f}", ha="left", va="bottom", fontsize=8, color=MUTED)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("recall", color=INK2, fontsize=9)
    ax.set_ylabel("precision", color=INK2, fontsize=9)
    ax.legend(handles=handles, loc="upper right", frameon=False, fontsize=9, labelcolor=INK2)
    _title(fig, f"Precision and recall on {split}: {label[cand]} against lines × files",
           f"N = {n:,} commits, {pos:,} bug-inducing")
    save(fig, "pr_curves")
    return files


# ---------- MLflow ----------

def log_run(m, files, config, features, name=None, decision=None):
    """One run in meridian-defect-risk through tracking.py (Contract 3): the settings as params, every number as a
    metric, metrics.json and the charts as artifacts. Returns the run id."""
    import mlflow

    from model import tracking
    split = m["split"]
    with tracking.start_run(name or f"evaluate-{split}", owner="rochan", decision=decision, config=config,
                            features=features) as run:
        mlflow.log_params({"split": split, "candidate": m["candidate"]["column"], "scores_md5": m["scores_md5"],
                           **{k: v for k, v in m["settings"].items()}})
        values = {f"{split}_n": m["n"], f"{split}_positives": m["positives"]}
        for name_, x in m["models"].items():
            values[f"{split}_pr_auc_{name_}"] = x["pr_auc"]["value"]
            values[f"{split}_roc_auc_{name_}"] = x["roc_auc"]["value"]
            values[f"{split}_recall_at_effort_{name_}"] = x["recall_at_effort"]["value"]
            values[f"{split}_recall_{name_}"] = x["at_threshold"]["recall"]
            values[f"{split}_f1_{name_}"] = x["at_threshold"]["f1"]
            if x["at_threshold"]["precision"] is not None:
                values[f"{split}_precision_{name_}"] = x["at_threshold"]["precision"]
            if f"vs_{REFERENCE}" in x:
                d = x[f"vs_{REFERENCE}"]
                values[f"{split}_pr_auc_diff_{name_}"] = d["pr_auc_difference"]
                values[f"{split}_pr_auc_diff_low_{name_}"] = d["interval"][0]
                values[f"{split}_pr_auc_diff_high_{name_}"] = d["interval"][1]
        mlflow.log_metrics(values)
        for f in files:
            mlflow.log_artifact(str(f))
        return run.info.run_id


# ---------- the decision tests (val only) ----------

def decision_1(y, scores, candidate, settings, seed):
    """Main metric. Both metrics on every score and a seeded random one, against chance; and how many standard errors
    each puts between the candidate and lines x files (paired bootstrap)."""
    rnd = np.random.default_rng(seed).random(len(y))
    table = {name: {"pr_auc": pr_auc(y, s), "roc_auc": roc_auc(y, s)} for name, s in {**scores, "random": rnd}.items()}
    pair = {}
    for metric in ("pr_auc", "roc_auc"):
        b = bootstrap(y, {k: scores[k] for k in (candidate, REFERENCE)}, settings["bootstrap_reps"], seed, metric)
        d = b[candidate] - b[REFERENCE]
        diff = table[candidate][metric] - table[REFERENCE][metric]
        pair[metric] = {"difference": diff, "sd": float(d.std(ddof=1)), "z": diff / float(d.std(ddof=1)),
                        "interval": list(interval(d, settings["interval"]))}
    pick = "roc_auc" if pair["roc_auc"]["z"] > 1.1 * pair["pr_auc"]["z"] else "pr_auc"
    return {"chance": {"pr_auc": float(np.mean(y)), "roc_auc": 0.5}, "scores": table, "candidate_vs_reference": pair,
            "picked": pick}


def decision_2(y, scores, lines, settings):
    """Effort-aware ranking: recall at the effort budget, by risk and by risk per line, for every score."""
    table = {name: {r: recall_at_effort(y, s, lines, settings["effort"], r)["value"] for r in RANKINGS}
             for name, s in scores.items()}
    return {"effort": settings["effort"], "recall": table, "picked": "risk_per_line"}


def decision_3(y, scores, candidate, settings, alert_rate):
    """Threshold rule: each rule's threshold on val, with what it flags. alert_rate: today's P9 share."""
    out = {}
    for name in (candidate, REFERENCE):
        out[name] = {}
        for rule in RULES:
            t = choose_threshold(y, scores[name], rule, settings["threshold_precision"],
                                 settings["threshold_min_alerts"], alert_rate)
            out[name][rule] = at_threshold(y, scores[name], t)
    ok = [r for r in RULES if out[candidate][r]["precision"] is not None
          and out[candidate][r]["precision"] >= settings["threshold_precision"]
          and out[candidate][r]["alerts"] >= settings["threshold_min_alerts"]]
    pick = max(ok, key=lambda r: out[candidate][r]["recall"]) if ok else "precision"
    return {"alert_rate_option": alert_rate, "rules": out, "meets_bar": ok, "picked": pick}


def decision_4(y, scores, candidate, settings, sizes=(500, 1000, 2000), seeds=range(1, 21), bar=0.005):
    """Resamples: the paired interval of the candidate against lines x files, at each size over 20 seeds. A 3-seed
    check passes when both ends move less than `bar`; the share of the 1,140 seed triples that pass, per size."""
    pair = {k: scores[k] for k in (candidate, REFERENCE)}
    out = {}
    for size in sizes:
        ends, took = [], []
        for seed in seeds:
            t0 = time.perf_counter()
            b = bootstrap(y, pair, size, seed)
            took.append(time.perf_counter() - t0)
            ends.append(interval(b[candidate] - b[REFERENCE], settings["interval"]))
        ends = np.array(ends)
        passes = [np.ptp(ends[list(tr), 0]) < bar and np.ptp(ends[list(tr), 1]) < bar
                  for tr in itertools.combinations(range(len(ends)), 3)]
        first3 = ends[:3]
        out[size] = {"low_sd": float(ends[:, 0].std(ddof=1)), "high_sd": float(ends[:, 1].std(ddof=1)),
                     "triples_passing": float(np.mean(passes)), "triples": len(passes),
                     "seeds_1_3_move": [float(np.ptp(first3[:, 0])), float(np.ptp(first3[:, 1]))],
                     "seconds": float(np.median(took))}
    ok = [s for s in sizes if out[s]["triples_passing"] >= 0.95]
    return {"bar": bar, "sizes": out, "picked": min(ok) if ok else max(sizes)}


def decision_5(y, scores, candidate, settings, seed):
    """Paired or unpaired: the width of each interval for the candidate against lines x files."""
    reps, level = settings["bootstrap_reps"], settings["interval"]
    b = bootstrap(y, {k: scores[k] for k in (candidate, REFERENCE)}, reps, seed)
    paired = interval(b[candidate] - b[REFERENCE], level)
    other = bootstrap(y, {REFERENCE: scores[REFERENCE]}, reps, seed + 10_000)[REFERENCE]  # its own resamples
    unpaired = interval(b[candidate] - other, level)
    width = {"paired": paired[1] - paired[0], "unpaired": unpaired[1] - unpaired[0]}
    return {"paired": list(paired), "unpaired": list(unpaired), "width": width,
            "picked": "paired" if width["paired"] < width["unpaired"] else "unpaired"}


# ---------- the command ----------

def _config(path):
    from model.data import load_config
    return load_config(path)


def _p9_alert_rate(rows):
    """The share of these commits today's P9 rule flags: lines x files above Data-Pipeline/settings.toml
    [push_check] defect_risk_threshold."""
    import tomllib
    settings = tomllib.loads((HERE.parent / "Data-Pipeline" / "settings.toml").read_text(encoding="utf-8"))
    limit = settings["push_check"]["defect_risk_threshold"]
    return float((((rows["la"] + rows["ld"]) * rows["nf"]) > limit).mean()), limit


def main(argv=None):
    p = argparse.ArgumentParser(description="Rochan: evaluate the candidate and every baseline (step D). "
                                            "Run from Model-Pipeline/.")
    p.add_argument("--split", choices=("val", "test"), default="val")
    p.add_argument("--candidate", default=str(CANDIDATE), help="Contract 4's candidate.json (default %(default)s)")
    p.add_argument("--scores", help="the scores file for --split; default: candidate.json's scores_file (val)")
    p.add_argument("--column", help="the candidate's column in the scores file; default: candidate.json's column")
    p.add_argument("--config", default=str(MODEL_TOML))
    p.add_argument("--features", default=str(FEATURES))
    p.add_argument("--out", default=str(REPORTS), help="where metrics.json and the charts go")
    p.add_argument("--no-mlflow", action="store_true", help="don't log a run (a quick look)")
    p.add_argument("--no-charts", action="store_true")
    p.add_argument("--decide", type=int, nargs="+", choices=(1, 2, 3, 4, 5),
                   help="run these decision tests on val (packets/rochan-tracking/decisions.md) instead")
    a = p.parse_args(argv)

    cfg = _config(a.config)
    try:
        settings = eval_settings(cfg)
    except ValueError as e:
        sys.exit(str(e))
    seed = int(cfg["seed"]["value"])
    if a.split == "test":
        if a.decide:
            sys.exit("refused: decision tests run on val only")
        if cfg["split"].get("test_allowed") is not True:
            sys.exit("refused: --split test needs model.toml [split] test_allowed = true. Nobody looks at 2024 "
                     "before the one run on Nov 12")
        from model import tracking
        if tracking.git_state()[1] == "true":
            sys.exit("refused: commit first. The 2024 run must come from committed code, so the pick, the threshold "
                     "and the bars are in git before 2024 is seen")
        if not a.scores:
            sys.exit("--split test needs --scores: the candidate's scores on the test rows")

    cand = {"column": a.column}
    if Path(a.candidate).exists():
        try:
            c = read_candidate(a.candidate)
        except (ScoresError, json.JSONDecodeError) as e:
            sys.exit(f"candidate: {e}")
        cand = {"column": a.column or c["column"], "kind": c.get("kind"), "params": c.get("params"),
                "seed": c.get("seed"), "run_id": c.get("run_id"), "val_scores": c["scores_file"]}
    if not cand["column"]:
        sys.exit(f"which column is the candidate? {a.candidate} doesn't exist: pass --column, or write candidate.json")
    val_scores = a.scores if a.split == "val" and a.scores else cand.get("val_scores")
    if not val_scores:
        sys.exit("no scores file: pass --scores, or name scores_file in candidate.json")

    table = pd.read_parquet(a.features) if str(a.features).endswith(".parquet") else pd.read_csv(a.features)
    try:
        val_rows, val = load_scores(table, val_scores, "val", cand["column"])
        thresholds = thresholds_on(val_rows["label"].to_numpy(), val, settings)
        rows, scores = (val_rows, val) if a.split == "val" else load_scores(table, a.scores, "test", cand["column"])
    except ScoresError as e:
        sys.exit(f"scores: {e}")
    if set(scores) != set(val):
        sys.exit(f"the test scores have columns {sorted(scores)}, the val scores {sorted(val)}: they must match")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.decide:
        return decide(a, rows, scores, cand["column"], settings, seed, out)

    t0 = time.perf_counter()
    scores_file = a.scores or val_scores
    sources = {"scores_file": _rel(scores_file), "scores_md5": file_md5(scores_file),
               "features_md5": file_md5(a.features)}
    if a.split == "test":
        sources.update(val_scores_file=_rel(val_scores), val_scores_md5=file_md5(val_scores))
    cand = {k: (_rel(v) if k == "val_scores" else v) for k, v in cand.items()}
    m = metrics(a.split, rows, scores, cand, settings, seed, thresholds, sources)
    path = out / "metrics.json"
    path.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8", newline="\n")
    files = [path]
    if not a.no_charts:
        files += charts(m, rows["label"].to_numpy(), scores, out)
    took = time.perf_counter() - t0
    print_summary(m)
    print(f"wrote {', '.join(_rel(f) for f in files)} in {took:.1f} s")
    if not a.no_mlflow:
        print(f"MLflow run {log_run(m, files, a.config, a.features)} in meridian-defect-risk")
    return m


def print_summary(m):
    print(f"{m['split']}: N = {m['n']:,}, positives = {m['positives']:,} ({m['positive_rate']:.1%}); "
          f"candidate {m['candidate']['column']}")
    print(f"{'':15}{'PR-AUC':>8} {'95% interval':>17} {'ROC-AUC':>8} {'recall@20%':>10} {'prec':>6} {'recall':>6}"
          f" {'alerts':>6}   vs lines x files (paired)")
    for name, x in m["models"].items():
        t, p = x["at_threshold"], x["pr_auc"]
        prec = "  n/a" if t["precision"] is None else f"{t['precision']:6.3f}"
        line = (f"{name:15}{p['value']:8.3f} [{p['interval'][0]:.3f}, {p['interval'][1]:.3f}] "
                f"{x['roc_auc']['value']:8.3f} {x['recall_at_effort']['value']:10.3f} {prec} {t['recall']:6.3f} "
                f"{t['alerts']:6d}")
        d = x.get(f"vs_{REFERENCE}")
        if d:
            line += f"   {d['pr_auc_difference']:+.3f} [{d['interval'][0]:+.3f}, {d['interval'][1]:+.3f}]"
        print(line)
    w = m["recall_at_effort_without_a_model"]
    print(f"recall at {w['effort']:.0%} of the lines with no model: {w['smallest_first']:.3f} reading the smallest "
          f"commits first, {w['random_order']:.3f} in random order")


def decide(a, rows, scores, candidate, settings, seed, out):
    """Run the decision tests asked for, print each, write decision_rochan-<n>.json and log a run tagged
    decision=rochan-<n>."""
    y, lines = rows["label"].to_numpy(), (rows["la"] + rows["ld"]).to_numpy()
    results = {}
    for n in a.decide:
        t0 = time.perf_counter()
        if n == 1:
            r = decision_1(y, scores, candidate, settings, seed)
        elif n == 2:
            r = decision_2(y, scores, lines, settings)
        elif n == 3:
            rate, limit = _p9_alert_rate(rows)
            r = decision_3(y, scores, candidate, settings, rate)
            r["p9_limit"] = limit
        elif n == 4:
            r = decision_4(y, scores, candidate, settings)
        else:
            r = decision_5(y, scores, candidate, settings, seed)
        r = {"decision": f"rochan-{n}", "split": "val", "n": len(y), "positives": int(y.sum()),
             "positive_rate": float(y.mean()), "candidate": candidate, "seed": seed, **r,
             "seconds": time.perf_counter() - t0}
        path = out / f"decision_rochan-{n}.json"
        path.write_text(json.dumps(r, indent=2, default=float) + "\n", encoding="utf-8", newline="\n")
        print(f"decision {n}: picked {r['picked']}  ({path})")
        print(json.dumps({k: v for k, v in r.items() if k not in ("decision", "seconds")}, indent=1, default=float))
        if not a.no_mlflow:
            import mlflow

            from model import tracking
            with tracking.start_run(f"decision-{n}", owner="rochan", decision=f"rochan-{n}", config=a.config,
                                    features=a.features):
                mlflow.log_params({"candidate": candidate, "picked": r["picked"], "n": len(y)})
                mlflow.log_artifact(str(path))
        results[n] = r
    return results


if __name__ == "__main__":
    main()
