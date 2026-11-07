"""Bias (step F, Chaitali): does the model serve some modules, components, newcomers or commit sizes worse?

Reads the scores train.py wrote (Contract 4, S6: never refits) and joins the slice columns from the feature table on
`sha`. Slices with Fairlearn's MetricFrame by each of model.toml [bias] slices. Per slice, three metrics:

- recall: of the bug-inducing commits, the share flagged. A miss reaches users
- false-positive rate: of the clean commits, the share flagged. A false alarm costs a reviewer's time
- PR-AUC, with a bootstrap interval. It also moves with a slice's positive rate, so on its own it isn't a harm

Phase 1's rule (Data-Pipeline/meridian/bias.py): a slice is judged when the count its metric is a share of reaches
min_n (positives for recall and PR-AUC, negatives for the false-positive rate), and flagged when its whole 95% interval
is more than max_gap worse than overall. Wilson intervals for the two shares, the bootstrap for PR-AUC. Each judged
share also carries its power: the exact chance the rule flags a true gap of power_gap at that N, so "no flag" isn't
read as "no bias". A Bonferroni count (the same rule, 95% split over every judged test) is reported beside it.

Recall and the false-positive rate need a threshold (decision 10): [eval]'s rule when it flags at least
threshold_min_alerts val commits, else the share of commits today's P9 rule flags, for the model and the baseline
alike. `pass` is false while a flagged slice isn't in [bias] accepted (a mitigation tried and written down).

    python -m model.bias                                            # the candidate in reports/candidate.json, on val
    python -m model.bias --scores reports/untuned_val_scores.csv --column lgbm
    python -m model.bias --decide 1 3 --scores reports/untuned_val_scores.csv --column lgbm

Writes reports/bias_report.json and reports/bias_<slice>.png. Run from Model-Pipeline/.
"""
import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from fairlearn.metrics import MetricFrame
from scipy.stats import binom, norm
from sklearn.metrics import average_precision_score

from model.evaluate import (CANDIDATE, FEATURES, MODEL_TOML, REPORTS, ScoresError, _rel, bootstrap, choose_threshold,
                            eval_settings, file_md5, interval, load_scores, read_candidate)

BIAS_KEYS = ("slices", "min_n", "max_gap", "power_gap", "pool_below", "baseline", "accepted")
METRICS = ("recall", "fpr", "pr_auc")
HARM = {"recall": -1, "fpr": 1, "pr_auc": -1}   # which way is worse: lower recall and PR-AUC, higher false alarms
OTHER = "(other)"


# ---------- the rule ----------

def wilson(k, n, level=0.95):
    """Wilson score interval for k of n, vectorised. The same numbers as scipy's
    binomtest(k, n).proportion_ci(method="wilson"), which phase 1's bias.py uses (test_bias checks to 1e-12)."""
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    z = norm.ppf(1 - (1 - level) / 2)
    p = k / n
    centre = (k + z * z / 2) / (n + z * z)
    half = z * np.sqrt(n) / (n + z * z) * np.sqrt(p * (1 - p) + z * z / (4 * n))
    return np.clip(centre - half, 0, 1), np.clip(centre + half, 0, 1)


def flagged(metric, low, high, overall, max_gap):
    """Phase 1's flag: the whole interval more than max_gap on the harmful side of overall."""
    if HARM[metric] < 0:
        return bool(high < overall - max_gap)
    return bool(low > overall + max_gap)


def power(metric, n, overall, b, level=0.95):
    """The chance, exactly, that the rule flags a slice of n whose true share is power_gap worse than overall.
    None when that share would fall outside 0..1, or for PR-AUC (no closed form)."""
    if metric == "pr_auc" or n < b["min_n"]:
        return None
    true = overall + HARM[metric] * b["power_gap"]
    if not 0 <= true <= 1:
        return None
    k = np.arange(n + 1)
    lo, hi = wilson(k, n, level)
    hit = np.array([flagged(metric, a, c, overall, b["max_gap"]) for a, c in zip(lo, hi)])
    return float(binom.pmf(k, n, true)[hit].sum())


# ---------- settings and inputs ----------

def bias_settings(cfg):
    """model.toml [bias], every key present and in range."""
    s = cfg.get("bias", {})
    missing = [k for k in BIAS_KEYS if k not in s]
    if missing:
        raise ValueError(f"model.toml [bias] is missing {', '.join(missing)}")
    if not (isinstance(s["min_n"], int) and s["min_n"] >= 1):
        raise ValueError("model.toml [bias] min_n: a whole number, at least 1")
    for k in ("max_gap", "power_gap"):
        if not 0 < s[k] < 1:
            raise ValueError(f"model.toml [bias] {k}: between 0 and 1")
    return {k: s[k] for k in BIAS_KEYS}


def threshold_for(y, score, settings):
    """(threshold, rule, why) by decision 10: [eval]'s rule when it flags at least threshold_min_alerts commits,
    else the share of commits today's P9 rule flags."""
    rule = settings["threshold"]
    t = choose_threshold(y, score, rule, settings["threshold_precision"], settings["threshold_min_alerts"],
                         settings["threshold_alert_rate"])
    alerts = 0 if t is None else int((np.asarray(score) >= t).sum())
    if alerts >= settings["threshold_min_alerts"]:
        return t, rule, f"[eval] threshold = {rule!r}"
    t = choose_threshold(y, score, "alert_rate", alert_rate=settings["threshold_alert_rate"])
    return t, "alert_rate", (f"[eval] threshold = {rule!r} flags {alerts} val commits, under "
                             f"threshold_min_alerts = {settings['threshold_min_alerts']}: the P9 alert rate instead")


def pooled(groups, below):
    """Groups under `below` rows merged into OTHER (decision 1). Returns (groups, {OTHER: sorted members})."""
    groups = groups.astype(str)
    if not below:
        return groups, {}
    sizes = groups.value_counts()
    small = sorted(sizes[sizes < below].index)
    if not small:
        return groups, {}
    return groups.where(~groups.isin(small), OTHER), {OTHER: small}


# ---------- slicing ----------

def counts(y, flag, groups):
    """MetricFrame of the confusion counts by group, and overall. Counts, not rates, so a group with no positives
    or no negatives is never a divide-by-zero inside Fairlearn."""
    c = {"tp": lambda t, p: int(((t == 1) & (p == 1)).sum()), "fn": lambda t, p: int(((t == 1) & (p == 0)).sum()),
         "fp": lambda t, p: int(((t == 0) & (p == 1)).sum()), "tn": lambda t, p: int(((t == 0) & (p == 0)).sum())}
    return MetricFrame(metrics=c, y_true=pd.Series(y), y_pred=pd.Series(flag.astype(int)),
                       sensitive_features=pd.Series(np.asarray(groups), name="group"))


def share(metric, c, overall, b, level, level_b):
    """One share (recall or fpr) for one group: value, N, Wilson interval, judged, flagged, the Bonferroni flag
    (filled in later) and power."""
    k, n = (c["tp"], c["tp"] + c["fn"]) if metric == "recall" else (c["fp"], c["fp"] + c["tn"])
    k, n = int(k), int(n)
    if n == 0:
        return {"value": None, "n": 0, "ci": None, "judged": False, "flagged": False, "power": None}
    lo, hi = (float(v) for v in wilson(k, n, level))
    judged = n >= b["min_n"]
    return {"value": k / n, "n": n, "ci": [lo, hi], "judged": judged,
            "flagged": judged and flagged(metric, lo, hi, overall, b["max_gap"]),
            "power": power(metric, n, overall, b, level), "_k": k}


def pr_auc(y, s, overall, b, reps, seed, level):
    """PR-AUC for one group with its bootstrap interval. Judged at min_n positives."""
    pos = int(y.sum())
    if pos == 0 or pos == len(y):
        return {"value": None, "n": pos, "ci": None, "judged": False, "flagged": False, "power": None}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        values = bootstrap(y, {"m": s}, reps, seed)["m"]
    lo, hi = interval(values, level)
    judged = pos >= b["min_n"]
    return {"value": float(average_precision_score(y, s)), "n": pos, "ci": [lo, hi], "judged": judged,
            "flagged": judged and flagged("pr_auc", lo, hi, overall, b["max_gap"]), "power": None, "_boot": values}


def report(rows, y, score, flag, b, reps, seed, level=0.95):
    """{overall, slices, flags, ...} for one score at one threshold."""
    flag = np.asarray(flag, dtype=bool)
    total = counts(y, flag, np.zeros(len(y))).overall
    overall = {"recall": total["tp"] / (total["tp"] + total["fn"]), "fpr": total["fp"] / (total["fp"] + total["tn"]),
               "pr_auc": float(average_precision_score(y, score))}
    slices, members = {}, {}
    for dim in b["slices"]:
        groups, m = pooled(rows[dim], b["pool_below"])
        members[dim] = m
        by = counts(y, flag, groups).by_group
        slices[dim] = {}
        for g, c in by.iterrows():
            mask = (groups == g).to_numpy()
            ys = y[mask]
            entry = {"n": int(mask.sum()), "positives": int(ys.sum())}
            for metric in ("recall", "fpr"):
                entry[metric] = share(metric, c, overall[metric], b, level, None)
            entry["pr_auc"] = pr_auc(ys, score[mask], overall["pr_auc"], b, reps, seed, level)
            if g in m:
                entry["members"] = m[g]
            slices[dim][str(g)] = entry

    # Bonferroni: the same rule with 95% split over every judged test
    tests = [(d, g, m) for d, gs in slices.items() for g, e in gs.items() for m in METRICS if e[m]["judged"]]
    level_b = 1 - (1 - level) / max(1, len(tests))
    for d, g, m in tests:
        e = slices[d][g][m]
        if m == "pr_auc":
            lo, hi = interval(e["_boot"], level_b)
        else:
            lo, hi = (float(v) for v in wilson(e["_k"], e["n"], level_b))
        e["bonferroni_flagged"] = flagged(m, lo, hi, overall[m], b["max_gap"])
    for gs in slices.values():
        for e in gs.values():
            for m in METRICS:
                e[m].pop("_k", None)
                e[m].pop("_boot", None)
                e[m].setdefault("bonferroni_flagged", False)

    flags = [{"slice": f"{d}={g}", "metric": m, "value": slices[d][g][m]["value"], "n": slices[d][g][m]["n"],
              "ci": slices[d][g][m]["ci"], "overall": overall[m],
              "gap": HARM[m] * (slices[d][g][m]["value"] - overall[m]),   # how much worse, always positive
              "bonferroni": slices[d][g][m]["bonferroni_flagged"]}
             for d, g, m in tests if slices[d][g][m]["flagged"]]
    return {"overall": overall, "slices": slices, "tests": len(tests), "bonferroni_level": level_b, "flags": flags}


def newcomers(rows, y, flags_by_name, group="0"):
    """Decision 4: recall for exp_bucket = group, for each named score at its own threshold."""
    mask = (rows["exp_bucket"].astype(str) == group).to_numpy()
    pos = int(y[mask].sum())
    out = {"group": f"exp_bucket={group}", "n": int(mask.sum()), "positives": pos}
    for name, flag in flags_by_name.items():
        tp = int((flag[mask] & (y[mask] == 1)).sum())
        out[name] = {"recall": tp / pos if pos else None, "caught": tp}
    return out


def _round(x):
    """Floats to 6 decimals, so the file reads easily and is the same on every run."""
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_round(v) for v in x]
    return x


def build(rows, scores, column, b, settings, seed, sources=None):
    """The whole bias_report.json for one candidate column, as a dict."""
    y = rows["label"].to_numpy()
    if b["baseline"] not in scores:
        raise ScoresError(f"the scores have no {b['baseline']!r} column ([bias] baseline)")
    names = (column, b["baseline"])
    th = {n: threshold_for(y, scores[n], settings) for n in names}
    flag = {n: np.asarray(scores[n]) >= th[n][0] for n in names}
    r = report(rows, y, np.asarray(scores[column]), flag[column], b, settings["bootstrap_reps"], seed,
               settings["interval"])
    acc = set(b["accepted"])
    open_flags = [f for f in r["flags"] if f"{f['slice']}:{f['metric']}" not in acc]
    t, rule, why = th[column]
    out = {"split": "val", "candidate": column, "n": len(y), "positives": int(y.sum()),
           "threshold": {"value": t, "rule": rule, "why": why, "alerts": int(flag[column].sum()),
                         "alert_rate": float(flag[column].mean())},
           "settings": {**b, "bootstrap_reps": settings["bootstrap_reps"], "interval": settings["interval"],
                        "seed": seed},
           **r,
           "newcomers": newcomers(rows, y, {column: flag[column], b["baseline"]: flag[b["baseline"]]}),
           "accepted": sorted(acc), "open_flags": len(open_flags), "pass": not open_flags}
    out["newcomers"]["judged"] = out["newcomers"]["positives"] >= b["min_n"]
    if sources:
        out.update(sources)
    return _round(out)


# ---------- charts ----------

def charts(rep, out_dir, top=16):
    """One figure per slice: recall, false-positive rate and PR-AUC by group, with intervals, the overall line and
    the flag line. Groups by size, the largest `top`; unjudged groups in grey. Returns the files written."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from model.evaluate import AXIS, GRID, INK, INK2, MUTED, SURFACE
    flag_c, ok_c = "#c0392b", "#2f5d8a"
    names = {"recall": "Recall (higher is better)", "fpr": "False-positive rate (lower is better)",
             "pr_auc": "PR-AUC (higher is better)"}
    files = []
    for dim, groups in rep["slices"].items():
        order = sorted(groups, key=lambda g: (-groups[g]["n"], g))[:top]
        fig, axes = plt.subplots(1, 3, figsize=(13, 0.42 * len(order) + 1.8), sharey=True)
        fig.patch.set_facecolor(SURFACE)
        for ax, m in zip(axes, METRICS):
            ax.set_facecolor(SURFACE)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            for side in ("left", "bottom"):
                ax.spines[side].set_color(AXIS)
            ax.tick_params(colors=INK2, labelsize=8)
            ax.grid(axis="x", color=GRID)
            ax.set_axisbelow(True)
            o = rep["overall"][m]
            for i, g in enumerate(order):
                e = groups[g][m]
                if e["value"] is None:
                    continue
                c = flag_c if e["flagged"] else ok_c if e["judged"] else MUTED
                ax.barh(i, e["value"], color=c, alpha=0.85 if e["judged"] else 0.4, height=0.6)
                if e["ci"]:
                    ax.plot(e["ci"], [i, i], color=INK, linewidth=1)
            ax.axvline(o, color=INK, linewidth=1.2)
            ax.axvline(o + HARM[m] * rep["settings"]["max_gap"], color=flag_c, linewidth=1, linestyle="--")
            ax.set_title(names[m], fontsize=9, color=INK, loc="left")
            ax.set_xlim(0, 1)
        axes[0].set_yticks(range(len(order)))
        axes[0].set_yticklabels([f"{g} (n={groups[g]['n']}, +{groups[g]['positives']})" for g in order], fontsize=8)
        axes[0].invert_yaxis()
        fig.suptitle(f"Bias by {dim}: {rep['candidate']} on val, threshold by {rep['threshold']['rule']}. Black line: "
                     f"overall; dashed: the flag line ({rep['settings']['max_gap']} worse). Grey: not judged",
                     x=0.01, ha="left", fontsize=9, color=INK2)
        fig.tight_layout()
        path = Path(out_dir) / f"bias_{dim}.png"
        fig.savefig(path, dpi=110, facecolor=SURFACE)
        plt.close(fig)
        files.append(path)
    return files


# ---------- decisions ----------

def decision_1(rows, scores, column, b, settings, seed):
    """Judged slices per metric with groups as they are, and with groups under min_n rows merged."""
    out = {}
    for below in (0, b["min_n"]):
        r = build(rows, scores, column, {**b, "pool_below": below}, settings, seed)
        out[f"pool_below={below}"] = {d: {m: sum(e[m]["judged"] for e in gs.values()) for m in METRICS}
                                      for d, gs in r["slices"].items()}
    return out


def decision_3(b):
    """The planted slices of PASS-BARS §6.1 on exact counts, through the same rule the report uses."""
    def one(metric, k, n, overall):
        lo, hi = (float(v) for v in wilson(k, n))
        return {"k": k, "n": n, "overall": overall, "ci": [round(lo, 4), round(hi, 4)],
                "flagged": flagged(metric, lo, hi, overall, b["max_gap"])}
    return {"recall gap 0.15": one("recall", 175, 500, 0.50), "recall no gap": one("recall", 250, 500, 0.50),
            "fpr gap 0.15": one("fpr", 125, 500, 0.10), "fpr no gap": one("fpr", 50, 500, 0.10)}


# ---------- run ----------

def log_run(rep, files, config, features, name="bias-val", decision=None, extra=None):
    import mlflow

    from model import tracking
    with tracking.start_run(name, owner="chaitali", decision=decision, config=config, features=features) as run:
        mlflow.log_params({"candidate": rep.get("candidate"), "threshold_rule": rep.get("threshold", {}).get("rule"),
                           **{k: str(v) for k, v in rep.get("settings", {}).items()}})
        if "flags" in rep:
            mlflow.log_metrics({"flags": len(rep["flags"]), "open_flags": rep["open_flags"], "tests": rep["tests"],
                                "bonferroni_flags": sum(f["bonferroni"] for f in rep["flags"]),
                                "pass": int(rep["pass"])})
        for f in files:
            mlflow.log_artifact(str(f))
        if extra:
            mlflow.log_dict(extra, f"{decision}.json")
        return run.info.run_id


def main(argv=None):
    p = argparse.ArgumentParser(description="Chaitali: bias by slice (step F). Run from Model-Pipeline/.")
    p.add_argument("--candidate", default=str(CANDIDATE))
    p.add_argument("--scores", help="val scores; default: candidate.json's scores_file")
    p.add_argument("--column", help="the candidate's column; default: candidate.json's column")
    p.add_argument("--config", default=str(MODEL_TOML))
    p.add_argument("--features", default=str(FEATURES))
    p.add_argument("--out", default=str(REPORTS))
    p.add_argument("--no-mlflow", action="store_true")
    p.add_argument("--no-charts", action="store_true")
    p.add_argument("--decide", type=int, nargs="+", choices=(1, 3), help="run these decision tests instead")
    a = p.parse_args(argv)

    from model.data import load_config
    cfg = load_config(a.config)
    try:
        settings, b = eval_settings(cfg), bias_settings(cfg)
    except ValueError as e:
        sys.exit(str(e))
    seed = int(cfg["seed"]["value"])
    column, scores_path = a.column, a.scores
    if Path(a.candidate).exists() and not (column and scores_path):
        try:
            c = read_candidate(a.candidate)
        except (ScoresError, json.JSONDecodeError) as e:
            sys.exit(f"candidate: {e}")
        column, scores_path = column or c["column"], scores_path or c["scores_file"]
    if not (column and scores_path):
        sys.exit(f"which scores? {a.candidate} doesn't exist: pass --scores and --column")
    table = pd.read_parquet(a.features)
    try:
        rows, scores = load_scores(table, scores_path, "val", column)
    except ScoresError as e:
        sys.exit(f"scores: {e}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.decide:
        for n in a.decide:
            r = decision_1(rows, scores, column, b, settings, seed) if n == 1 else decision_3(b)
            path = out / f"decision_chaitali-{n}.json"
            path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"decision {n}: {json.dumps(r, indent=2)}")
            if not a.no_mlflow:
                log_run({}, [path], a.config, a.features, name=f"bias-decision-{n}", decision=f"chaitali-{n}")
        return None

    t0 = time.perf_counter()
    sources = {"scores_file": _rel(scores_path), "scores_md5": file_md5(scores_path),
               "features_md5": file_md5(a.features)}
    try:
        rep = build(rows, scores, column, b, settings, seed, sources)
    except ScoresError as e:
        sys.exit(f"scores: {e}")
    path = out / "bias_report.json"
    path.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8", newline="\n")
    files = [path] + ([] if a.no_charts else charts(rep, out))
    summary(rep)
    print(f"wrote {', '.join(_rel(f) for f in files)} in {time.perf_counter() - t0:.1f} s")
    if not a.no_mlflow:
        print(f"MLflow run {log_run(rep, files, a.config, a.features)} in meridian-defect-risk")
    return rep


def summary(rep):
    t = rep["threshold"]
    print(f"val: N = {rep['n']:,}, positives = {rep['positives']:,}; candidate {rep['candidate']}; threshold by "
          f"{t['rule']} ({t['alerts']} alerts, {t['alert_rate']:.1%})")
    o = rep["overall"]
    print(f"overall: recall {o['recall']:.3f}, false-positive rate {o['fpr']:.3f}, PR-AUC {o['pr_auc']:.3f}; "
          f"{rep['tests']} judged tests")
    for f in rep["flags"]:
        print(f"  FLAG {f['slice']} {f['metric']} {f['value']:.3f} (n={f['n']}, interval {f['ci'][0]:.3f}-"
              f"{f['ci'][1]:.3f}) vs overall {f['overall']:.3f}{'' if f['bonferroni'] else ', not under Bonferroni'}")
    nc = rep["newcomers"]
    print(f"newcomers {nc['group']}: n={nc['n']}, positives={nc['positives']}, judged={nc['judged']}: "
          + ", ".join(f"{k} recall {v['recall']:.3f}" for k, v in nc.items() if isinstance(v, dict)))
    print(f"pass: {rep['pass']} ({rep['open_flags']} open flags)")


if __name__ == "__main__":
    main()
