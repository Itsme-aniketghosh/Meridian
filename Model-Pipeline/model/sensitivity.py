"""Sensitivity (step E, Rachna): what the candidate learned, and how much its score hangs on its settings.

    python -m model.sensitivity                    # everything below, on reports/candidate.json's saved model, on val
    python -m model.sensitivity --parts shap sweep

Four parts, each written to reports/sensitivity.json and charted in reports/:

    shap         SHAP values on every val row (guideline §5): a beeswarm, dependence plots for the top 5, the share
                 of the model's attention on size features (decision 2's question), SHAP inside each slice bias.py
                 flagged, and decision 10: the smallest sample whose top-10 ranking matches all of val (Spearman >= 0.9)
    permutation  decision 11: permutation importance with la/ld and nf/nd/ns permuted as groups, against SHAP's
                 ranking of the same groups. SHAP splits credit between correlated features; permuting them together
                 doesn't
    hparams      Optuna's parameter importance (fANOVA, seeded) from the candidate's tuning study, and each
                 parameter's trials against their fold PR-AUC
    sweep        decision 12: the 2 most important numeric parameters one at a time, at -50%, -25%, +25%, +50% and
                 the ends of their search range, everything else as tuned. "Safe to retrain" if val PR-AUC moves by
                 at most 2 x seed-noise SD across +-50%. Each categorical parameter's choices are refitted too and
                 their spread reported: +-% isn't defined for them

Reads the saved model (train.py --save-model) and checks its md5 against candidate.json first, so every number here
is about the judged model. Never reads test rows. Run from Model-Pipeline/.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from model.train import (HERE, SIZE, check_table, file_md5, fit_and_score, inputs_for, load_config, load_features,
                         pmap, pr_auc, rows, search_space)

REPORTS = HERE / "reports"
PARTS = ("shap", "permutation", "hparams", "sweep")
GROUPS = {"lines (la+ld)": ["la", "ld"], "spread (nf+nd+ns)": ["nf", "nd", "ns"]}  # decision 11's groups
SAMPLE_SIZES = (2000, 1000, 500)        # decision 10: tried against all of val, smallest kept if Spearman >= 0.9
STABLE = 0.9
TOP = 10
SWEEP_FACTORS = (0.5, 0.75, 1.25, 1.5)  # decision 12: +-25% and +-50% of the tuned value


# ---------- the judged model ----------
def load_candidate(path):
    """(candidate dict, the saved model). Refuses a model file whose md5 isn't the one candidate.json records."""
    import joblib
    path = Path(path)
    c = json.loads(path.read_text())
    if "model_file" not in c:
        raise SystemExit(f"{path} has no model_file: run python -m model.train --save-model first")
    model_path = path.parent / c["model_file"]
    if not model_path.exists():
        raise SystemExit(f"{model_path} not found: run python -m model.train --save-model first")
    if file_md5(model_path) != c["model_md5"]:
        raise SystemExit(f"{model_path}'s md5 isn't candidate.json's: not the judged model. Rerun --save-model")
    return c, joblib.load(model_path)


# ---------- SHAP ----------
def shap_values(model, X, kind):
    """(values n x features for the positive class, base value, units). Trees: TreeExplainer, in probability for a
    forest and log-odds for LightGBM. Logistic regression: LinearExplainer on its scaled inputs, in log-odds."""
    import shap
    if kind == "logreg":
        pre, lr = model[:-1], model[-1]
        Z = pre.transform(X)
        ex = shap.LinearExplainer(lr, Z)
        return np.asarray(ex.shap_values(Z)), float(np.ravel(ex.expected_value)[-1]), "log-odds"
    ex = shap.TreeExplainer(model)
    v = ex.shap_values(X)
    base = np.ravel(ex.expected_value)
    if isinstance(v, list):
        v = v[-1]
    v = np.asarray(v)
    if v.ndim == 3:
        v = v[:, :, -1]
    return v, float(base[-1]), "probability" if kind == "forest" else "log-odds"


def additivity_error(model, X, values, base, kind):
    """max |sum of SHAP + base - the model's output| over the rows: SHAP's own guarantee, checked."""
    if kind == "forest":
        out = model.predict_proba(X)[:, 1]
    elif kind == "lgbm":
        out = model.predict(X, raw_score=True)
    else:
        out = model.decision_function(X)
    return float(np.max(np.abs(values.sum(axis=1) + base - out)))


def mean_abs(values, columns):
    return pd.Series(np.abs(values).mean(axis=0), index=columns).sort_values(ascending=False)


def smallest_stable_sample(values, columns, seed, sizes=SAMPLE_SIZES, top=TOP, bar=STABLE):
    """Decision 10. The top-`top` features by mean |SHAP| on all rows; for each sample size, the Spearman correlation
    of those features' mean |SHAP| on a seeded sample against all rows. The smallest size with Spearman >= bar."""
    full = mean_abs(values, columns)
    keep = list(full.index[:top])
    rng = np.random.default_rng(seed)
    tried = {}
    for n in sorted(sizes, reverse=True):
        if n >= len(values):
            continue
        idx = rng.choice(len(values), size=n, replace=False)
        part = mean_abs(values[idx], columns)[keep]
        tried[n] = float(spearmanr(full[keep], part).statistic)
    stable = [n for n, r in tried.items() if r >= bar]
    return {"all": len(values), "top": keep, "spearman": tried, "bar": bar,
            "smallest_stable": min(stable) if stable else len(values)}


def size_share(importance):
    """The share of the total mean |SHAP| on decision 2's size features."""
    size = sum(v for k, v in importance.items() if k in SIZE)
    return float(size / importance.sum()) if importance.sum() else 0.0


def slice_shap(values, columns, val, flags, top=5):
    """For each slice bias.py flagged ("module=core"), its top features by mean |SHAP| next to overall's."""
    overall = mean_abs(values, columns)
    out = {}
    for name in sorted({f["slice"] for f in flags}):
        col, _, value = name.partition("=")
        if col not in val.columns:
            continue
        mask = (val[col].astype(str) == value).to_numpy()
        if not mask.any():
            continue
        imp = mean_abs(values[mask], columns)
        out[name] = {"n": int(mask.sum()), "top": {k: float(imp[k]) for k in imp.index[:top]},
                     "overall_same_features": {k: float(overall[k]) for k in imp.index[:top]}}
    return out


# ---------- grouped permutation (decision 11) ----------
def units(columns):
    """The things to permute: decision 11's groups, ticket_type's one-hot columns together, everything else alone."""
    grouped = {c for cols in GROUPS.values() for c in cols}
    out = {name: [c for c in cols if c in columns] for name, cols in GROUPS.items()}
    tickets = [c for c in columns if c.startswith("ticket_type=")]
    if tickets:
        out["ticket_type"] = tickets
    for c in columns:
        if c not in grouped and not c.startswith("ticket_type="):
            out[c] = [c]
    return {k: v for k, v in out.items() if v}


def permutation_importance(model, X, y, seed, repeats=5):
    """{unit: (mean, sd) drop in val PR-AUC} when the unit's columns are shuffled together, rows kept aligned."""
    base = pr_auc(y, model.predict_proba(X)[:, 1])
    rng = np.random.default_rng(seed)
    out = {}
    for name, cols in units(list(X.columns)).items():
        drops = []
        for _ in range(repeats):
            Xp = X.copy()
            order = rng.permutation(len(X))
            Xp[cols] = X[cols].to_numpy()[order]
            drops.append(base - pr_auc(y, model.predict_proba(Xp)[:, 1]))
        out[name] = (float(np.mean(drops)), float(np.std(drops, ddof=1)))
    return base, out


def unit_shap(importance, columns):
    """SHAP's importance for each permutation unit: the sum of its columns' mean |SHAP|."""
    return {name: float(sum(importance[c] for c in cols)) for name, cols in units(columns).items()}


def compare_rankings(perm, shap_units, top=3):
    """Decision 11's rule: a group in permutation's top 3 but not SHAP's is reported with the grouped ranking."""
    by_perm = sorted(perm, key=lambda k: -perm[k][0])
    by_shap = sorted(shap_units, key=lambda k: -shap_units[k])
    common = [k for k in by_perm if k in shap_units]
    rho = float(spearmanr([perm[k][0] for k in common], [shap_units[k] for k in common]).statistic)
    differ = [g for g in GROUPS if g in by_perm[:top] and g not in by_shap[:top]]
    return {"permutation_top": by_perm[:top], "shap_top": by_shap[:top], "spearman": rho,
            "groups_shap_underrates": differ}


# ---------- hyperparameters ----------
def rebuilt_study(trials, space):
    """An Optuna study holding the saved trials, so Optuna's importance can read them."""
    import optuna
    from optuna.distributions import CategoricalDistribution, FloatDistribution, IntDistribution

    def dist(s):
        if s["type"] == "float":
            return FloatDistribution(s["low"], s["high"], log=s.get("log", False))
        if s["type"] == "int":
            if s.get("log"):
                return IntDistribution(s["low"], s["high"], log=True)
            return IntDistribution(s["low"], s["high"], step=s.get("step", 1))
        return CategoricalDistribution(s["choices"])

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize")
    dists = {name: dist(s) for name, s in space.items()}
    for t in trials:
        study.add_trial(optuna.trial.create_trial(params=t["params"], distributions=dists, value=t["value"]))
    return study


def param_importance(trials, space, seed):
    import optuna
    evaluator = optuna.importance.FanovaImportanceEvaluator(seed=seed)
    imp = optuna.importance.get_param_importances(rebuilt_study(trials, space), evaluator=evaluator)
    return {k: float(v) for k, v in imp.items()}


# ---------- sweep (decision 12) ----------
def sweep_points(name, best, spec):
    """[(label, value)] for one parameter: the tuned value, it scaled by SWEEP_FACTORS (-50%, -25%, +25%, +50%), and
    the search range's ends. Kept inside the range, rounded for ints, without repeats (the first label wins).
    A categorical parameter: each choice, the tuned one labelled "tuned"."""
    if spec["type"] == "categorical":
        choices = [None if ch == "none" else ch for ch in spec["choices"]]
        return [("tuned" if ch == best else "choice", ch) for ch in choices]
    lo, hi = spec["low"], spec["high"]
    out = [("tuned", best)]
    out += [(f"-{(1 - f) * 100:.0f}%" if f < 1 else f"+{(f - 1) * 100:.0f}%", best * f) for f in SWEEP_FACTORS]
    out += [("low", lo), ("high", hi)]
    seen, kept = set(), []
    for label, v in out:
        v = min(max(v, lo), hi)
        v = int(round(v)) if spec["type"] == "int" else float(v)
        if v not in seen:
            seen.add(v)
            kept.append((label, v))
    return kept


def sweep(df, kind, params, seed, space, names):
    """{param: [{label, value, pr_auc}]}: each named parameter moved alone, refitted on train, scored on val."""
    jobs, where = [], []
    for name in names:
        for label, value in sweep_points(name, params[name], space[name]):
            jobs.append((df, kind, seed, {**params, name: value}))
            where.append((name, label, value))
    results = pmap(fit_and_score, jobs)
    out = {n: [] for n in names}
    for (name, label, value), (_, s, scored) in zip(where, results):
        out[name].append({"label": label, "value": value, "pr_auc": pr_auc(scored["label"], s)})
    return out


def sweep_verdict(points, sd):
    """Decision 12: the spread of val PR-AUC within +-25%, within +-50%, and over the whole range; safe if +-50%'s
    spread is at most 2 x seed-noise SD. A categorical parameter has no +-%: its spread over the choices is reported,
    with no verdict."""
    def spread(labels):
        vals = [p["pr_auc"] for p in points if p["label"] in labels]
        return float(max(vals) - min(vals)) if len(vals) > 1 else 0.0
    full = float(max(p["pr_auc"] for p in points) - min(p["pr_auc"] for p in points))
    if not any(p["label"].endswith("%") for p in points):     # categorical: +-% isn't defined, so no verdict
        return {"spread_25": None, "spread_50": None, "spread_full": full, "bar": 2 * sd, "safe_to_retrain": None}
    within25 = spread({"tuned", "-25%", "+25%"})
    within50 = spread({"tuned", "-25%", "+25%", "-50%", "+50%"})
    return {"spread_25": within25, "spread_50": within50, "spread_full": full, "bar": 2 * sd,
            "safe_to_retrain": within50 <= 2 * sd}


# ---------- charts ----------
def charts(out, values=None, X=None, importance=None, perm=None, hparams=None, trials=None, sweeps=None, sd=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    written = []

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(out / name, dpi=120)
        plt.close(fig)
        written.append(name)

    if values is not None:
        top = list(importance.index[:15])[::-1]
        fig, ax = plt.subplots(figsize=(8, 0.4 * len(top) + 1.5))
        rng = np.random.default_rng(0)
        for i, f in enumerate(top):
            j = list(X.columns).index(f)
            col = X[f].to_numpy(dtype=float)
            rank = pd.Series(col).rank(pct=True).to_numpy()
            ax.scatter(values[:, j], i + rng.uniform(-0.3, 0.3, len(col)), c=rank, cmap="coolwarm", s=4, alpha=0.6)
        ax.set_yticks(range(len(top)), top)
        ax.axvline(0, color="grey", lw=0.8)
        ax.set_xlabel("SHAP value (effect on the predicted risk); colour: the feature's value, low blue to high red")
        ax.set_title(f"SHAP on val, N = {len(X):,}")
        save(fig, "shap_beeswarm.png")
        for f in importance.index[:5]:
            j = list(X.columns).index(f)
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.scatter(X[f], values[:, j], s=4, alpha=0.4)
            if (X[f] > 0).all() and X[f].max() / max(X[f].min(), 1e-9) > 100:
                ax.set_xscale("log")
            ax.axhline(0, color="grey", lw=0.8)
            ax.set_xlabel(f)
            ax.set_ylabel(f"SHAP value of {f}")
            save(fig, f"shap_dependence_{f.replace('=', '_')}.png")
    if perm is not None:
        names = sorted(perm, key=lambda k: perm[k][0])
        fig, ax = plt.subplots(figsize=(7, 0.35 * len(names) + 1.5))
        ax.barh(names, [perm[k][0] for k in names], xerr=[perm[k][1] for k in names])
        ax.set_xlabel("drop in val PR-AUC when shuffled (groups shuffled together)")
        save(fig, "permutation_importance.png")
    if hparams:
        names = sorted(hparams, key=hparams.get)
        fig, ax = plt.subplots(figsize=(6, 0.4 * len(names) + 1.5))
        ax.barh(names, [hparams[k] for k in names])
        ax.set_xlabel("Optuna parameter importance (fANOVA)")
        save(fig, "hparam_importance.png")
        fig, axes = plt.subplots(1, len(hparams), figsize=(3.2 * len(hparams), 3), squeeze=False)
        for ax, name in zip(axes[0], sorted(hparams, key=lambda k: -hparams[k])):
            raw = [t["params"][name] for t in trials]
            xs = [str(v) for v in raw] if any(isinstance(v, str) or v is None for v in raw) else raw
            ax.scatter(xs, [t["value"] for t in trials], s=8)
            ax.set_xlabel(name)
        axes[0][0].set_ylabel("mean fold PR-AUC")
        save(fig, "hparam_slices.png")
    if sweeps:
        for name, points in sweeps.items():
            fig, ax = plt.subplots(figsize=(6, 4))
            pts = sorted(points, key=lambda p: (p["value"] is None, p["value"] if not isinstance(p["value"], str)
                                                else 0))
            ax.plot([str(p["value"]) for p in pts], [p["pr_auc"] for p in pts], marker="o")
            tuned = next(p["pr_auc"] for p in points if p["label"] == "tuned")
            if sd is not None:
                ax.axhspan(tuned - 2 * sd, tuned + 2 * sd, alpha=0.15, label="tuned +- 2 x seed-noise SD")
                ax.legend()
            ax.set_xlabel(name)
            ax.set_ylabel("val PR-AUC")
            save(fig, f"sweep_{name}.png")
    return written


# ---------- the command ----------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--candidate", default=str(REPORTS / "candidate.json"))
    p.add_argument("--config", default=str(HERE / "model.toml"))
    p.add_argument("--features", default=str(HERE / "data" / "features.parquet"))
    p.add_argument("--out", default=str(REPORTS))
    p.add_argument("--parts", nargs="+", choices=PARTS, default=list(PARTS))
    p.add_argument("--no-charts", action="store_true")
    p.add_argument("--no-mlflow", action="store_true")
    a = p.parse_args(argv)
    start = time.monotonic()
    cfg = load_config(a.config)
    seed = int(cfg["seed"]["value"])
    df = load_features(a.features)
    sp = cfg["split"]
    check_table(df, {"train": sp["train"], "val": sp["val"], "test": sp["test"]})
    c, model = load_candidate(a.candidate)
    kind, params = c["kind"], c["params"] or {}
    val = rows(df, "val").reset_index(drop=True)
    X = inputs_for(model, val)
    y = val["label"].to_numpy(dtype=int)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "sensitivity.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    report.update(candidate=kind, params=params, model_md5=c["model_md5"], split="val", n=int(len(y)),
                  positives=int(y.sum()))
    draw = {}

    if "shap" in a.parts:
        values, base, unit = shap_values(model, X, kind)
        err = additivity_error(model, X, values, base, kind)
        imp = mean_abs(values, list(X.columns))
        flags = []
        bias_path = out / "bias_report.json"
        if bias_path.exists():
            b = json.loads(bias_path.read_text())
            if b.get("candidate") == c.get("column", kind):
                flags = b.get("flags", [])
        report["shap"] = {"units": unit, "base": base, "additivity_max_error": err,
                          "mean_abs": {k: float(v) for k, v in imp.items()}, "size_share": size_share(imp),
                          "sample": smallest_stable_sample(values, list(X.columns), seed),
                          "slices": slice_shap(values, list(X.columns), val, flags)}
        draw.update(values=values, X=X, importance=imp)
        print(f"SHAP on val N = {len(y):,} ({unit}): additivity error {err:.1e}; top 5: "
              + ", ".join(f"{k} {v:.4f}" for k, v in imp.head(5).items()))
        print(f"  size features carry {report['shap']['size_share']:.0%} of the mean |SHAP| (decision 2's question)")
        s = report["shap"]["sample"]
        print(f"  decision 10: smallest sample matching all of val: {s['smallest_stable']:,} "
              f"(Spearman {', '.join(f'{n:,}: {r:.3f}' for n, r in s['spearman'].items())})")

    if "permutation" in a.parts:
        base_auc, perm = permutation_importance(model, X, y, seed)
        report["permutation"] = {"base_pr_auc": base_auc, "repeats": 5,
                                 "drop": {k: {"mean": m, "sd": s} for k, (m, s) in perm.items()}}
        if "shap" in report:
            imp = pd.Series(report["shap"]["mean_abs"])
            report["permutation"]["vs_shap"] = compare_rankings(perm, unit_shap(imp, list(X.columns)))
        draw["perm"] = perm
        top = sorted(perm, key=lambda k: -perm[k][0])[:5]
        print("permutation (decision 11), drop in val PR-AUC: " + ", ".join(f"{k} {perm[k][0]:+.4f}" for k in top))
        if "vs_shap" in report["permutation"]:
            v = report["permutation"]["vs_shap"]
            print(f"  vs SHAP: Spearman {v['spearman']:.2f}; groups SHAP ranks lower: "
                  f"{v['groups_shap_underrates'] or 'none'}")

    space = search_space(cfg)[kind]
    tuning_path = out / f"tuning_{kind}.json"
    if "hparams" in a.parts or "sweep" in a.parts:
        if not tuning_path.exists():
            raise SystemExit(f"{tuning_path} not found: the hyperparameter parts read the candidate's tuning study")
        trials = json.loads(tuning_path.read_text())["trials"]
    if "hparams" in a.parts:
        hp = param_importance(trials, space, seed)
        report["hparams"] = {"evaluator": "fANOVA", "trials": len(trials), "importance": hp}
        draw.update(hparams=hp, trials=trials)
        print("parameter importance (fANOVA): " + ", ".join(f"{k} {v:.2f}" for k, v in hp.items()))

    if "sweep" in a.parts:
        hp = report.get("hparams", {}).get("importance")
        if not hp:
            raise SystemExit("the sweep sweeps the 2 most important parameters: run --parts hparams first")
        ranked = [k for k in sorted(hp, key=lambda k: -hp[k]) if k in params]
        numeric = [k for k in ranked if space[k]["type"] != "categorical"][:2]   # decision 12's rule: +-25%, +-50%
        choices = [k for k in ranked if space[k]["type"] == "categorical"]       # reported, no +-% to apply
        noise_path = out / "seed_noise.json"
        sd = json.loads(noise_path.read_text())["sd"] if noise_path.exists() else None
        sw = sweep(df, kind, params, seed, space, numeric + choices)
        report["sweep"] = {"params": numeric, "choices": choices, "seed_noise_sd": sd, "points": sw,
                           "verdict": {n: sweep_verdict(sw[n], sd or 0.0) for n in numeric + choices}}
        draw.update(sweeps=sw, sd=sd)
        for n in numeric + choices:
            v = report["sweep"]["verdict"][n]
            if v["safe_to_retrain"] is None:
                print(f"choices of {n}: spread {v['spread_full']:.4f} (2 x seed SD {v['bar']:.4f}), for the report")
                continue
            print(f"sweep {n} (decision 12): spread +-25% {v['spread_25']:.4f}, +-50% {v['spread_50']:.4f}, "
                  f"full range {v['spread_full']:.4f}; bar {v['bar']:.4f}: "
                  + ("safe to retrain" if v["safe_to_retrain"] else "the result hangs on this setting"))

    report_path.write_text(json.dumps(report, indent=2) + "\n")
    written = [] if a.no_charts else charts(out, **draw)
    print(f"wrote {report_path}" + (f" and {', '.join(written)}" if written else "") +
          f" in {time.monotonic() - start:.0f} s")
    if not a.no_mlflow:
        log(report, out, written, a.config, a.features)
    return report


def log(report, out, written, config, features):
    """One run through Rochan's tracking.py, decision rachna-10 (with 11 and 12 in it)."""
    if not Path(config).exists():
        print(f"not logged to MLflow: {config} doesn't exist (Contract 2)")
        return
    import mlflow
    from model import tracking
    with tracking.start_run("sensitivity", owner="rachna", decision="rachna-10", config=config, features=features):
        m = {}
        if "shap" in report:
            m.update(shap_size_share=report["shap"]["size_share"],
                     shap_additivity_error=report["shap"]["additivity_max_error"],
                     shap_smallest_stable_sample=report["shap"]["sample"]["smallest_stable"])
        if "permutation" in report and "vs_shap" in report["permutation"]:
            m["perm_vs_shap_spearman"] = report["permutation"]["vs_shap"]["spearman"]
        for n, v in report.get("sweep", {}).get("verdict", {}).items():
            m[f"sweep_{n}_spread"] = v["spread_50"] if v["spread_50"] is not None else v["spread_full"]
        mlflow.log_metrics(m)
        mlflow.log_artifact(str(out / "sensitivity.json"))
        for name in written:
            mlflow.log_artifact(str(out / name))


if __name__ == "__main__":
    main(sys.argv[1:])
