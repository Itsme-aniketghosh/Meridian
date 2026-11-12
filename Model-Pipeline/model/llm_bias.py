"""LLM bias (step M, Chaitali): do the agents serve some repos, teams or task kinds worse? Run from Model-Pipeline/.

Two rates, sliced by decision 9 (packets/chaitali-bias/decisions.md) and judged by bias.py's rule (phase 1's: N at
least [bias] min_n, the whole Wilson interval more than [bias] max_gap worse than overall):

    fallback  the share of LLM texts the fact gate sent back to the template (harm: higher), per repo, owner team and
              task. One text per (agent, facts_hash) in meridian-agents' llm_calls table (tracking.log_llm_call): it
              fell back when none of its attempts shipped (source "llm"). A template with no LLM attempt behind it
              (the LLM off) is no text, so it's never counted as a fallback
    match     the share of drafted diff lines equal to the real after-line (harm: lower), per repo and task, from
              Utkarsh's published counts (packets/chaitali-bias/diff_match_by_repo.csv, each row with its source)

Writes reports/llm_bias.json and reports/llm_bias_match.png. `pass` is false while a flagged slice isn't in
[llm_bias] accepted (a fix tried and written down by its owner).

    python -m model.llm_bias                 # every meridian-agents run in the store, and the diff counts
    python -m model.llm_bias --run-id <id>   # one agents run
    python -m model.llm_bias --calls <jsonl>... --ship gemini-flash   # rows from `meridian.agents --calls`; the bar is
                                             # judged on the shipped model, every other model is listed for the record
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from model.bias import bias_settings, flagged, power, wilson

HERE = Path(__file__).resolve().parents[1]  # Model-Pipeline/
MODEL_TOML = HERE / "model.toml"
REPORTS = HERE / "reports"
DIFFS = HERE / "packets" / "chaitali-bias" / "diff_match_by_repo.csv"
TEXT_AGENTS = ("writer", "diff_drafter")  # Jev's own reasons pick a route; they never ship as a text
HARM = {"fallback": "fpr", "match": "recall"}  # bias.py's direction: fallback is worse higher, match lower
CALL_KEYS = ("agent", "facts_hash", "repo", "team", "task", "source")


def texts(calls):
    """One row per LLM text: agent, facts_hash, repo, team, task, fell_back."""
    cols = ["agent", "facts_hash", "repo", "team", "task", "fell_back"]
    if calls is None or len(calls) == 0:
        return pd.DataFrame(columns=cols)
    if missing := [k for k in CALL_KEYS if k not in calls]:
        raise ValueError(f"llm_calls has no {', '.join(missing)}")
    c = calls[calls["agent"].isin(TEXT_AGENTS)]
    if c.empty:
        return pd.DataFrame(columns=cols)
    g = c.groupby(["agent", "facts_hash"], dropna=False, sort=True)
    out = g.agg(repo=("repo", "first"), team=("team", "first"), task=("task", "first"),
                fell_back=("source", lambda s: not (s == "llm").any())).reset_index()
    return out[cols]


def rate(counts, rate_name, b, level=0.95):
    """{group: (k, n)} -> overall and every group's value, N, interval, judged, flagged, power."""
    metric = HARM[rate_name]
    k_all, n_all = sum(k for k, _ in counts.values()), sum(n for _, n in counts.values())
    overall = k_all / n_all if n_all else None
    groups = {}
    for g, (k, n) in sorted(counts.items()):
        if n == 0:
            groups[g] = {"value": None, "k": 0, "n": 0, "ci": None, "judged": False, "flagged": False, "power": None}
            continue
        lo, hi = (float(v) for v in wilson(k, n, level))
        judged = n >= b["min_n"] and overall is not None
        groups[g] = {"value": k / n, "k": int(k), "n": int(n), "ci": [lo, hi], "judged": judged,
                     "flagged": judged and flagged(metric, lo, hi, overall, b["max_gap"]),
                     "power": power(metric, n, overall, b, level) if judged else None}
    return {"overall": {"value": overall, "k": int(k_all), "n": int(n_all)}, "groups": groups}


def fallback(t, b):
    """The fallback rate per repo, owner team and task, from texts()."""
    by = {}
    for dim in ("repo", "team", "task"):
        counts = {}
        for g, rows in t.groupby(t[dim].fillna("(none)").astype(str)) if len(t) else []:
            counts[g] = (int(rows["fell_back"].sum()), len(rows))
        by[dim] = rate(counts, "fallback", b)
    note = ("no LLM texts: no logged call in meridian-agents yet (the committed cache is empty and CI has no key), "
            "so every slice is listed with N = 0 and none is judged") if len(t) == 0 else ""
    return {"texts": int(len(t)), "by": by, "note": note}


def match(diffs, b):
    """The diff match rate per repo and per task, from the published counts."""
    by = {}
    for dim in ("repo", "task"):
        counts = {g: (int(rows["matched"].sum()), int(rows["lines"].sum())) for g, rows in diffs.groupby(dim)}
        by[dim] = rate(counts, "match", b)
    return {"lines": int(diffs["lines"].sum()), "by": by,
            "team": "not judged: the published counts are per repo, not per owner team (decision 9)",
            "sources": sorted(set(diffs["source"]))}


def build(calls, diffs, b, accepted=(), ship=None):
    """The whole report as a dict. ship: the model whose texts the bar judges, when calls holds several (each run
    alone, as gap 2 runs them); the others go under "others", for the record only."""
    others = {}
    if ship is not None and calls is not None and len(calls):
        if ship not in set(calls["model"]):
            raise ValueError(f"no calls for the shipped model {ship!r} (models: {sorted(set(calls['model']))})")
        others = {m: fallback(texts(c), b) for m, c in sorted(calls.groupby("model")) if m != ship}
        calls = calls[calls["model"] == ship]
    rep = {"settings": {k: b[k] for k in ("min_n", "max_gap", "power_gap")}, "ship": ship,
           "fallback": fallback(texts(calls), b), "match": match(diffs, b)}
    if others:
        rep["others"] = others
    flags = [{"rate": r, "slice": f"{dim}={g}", "value": e["value"], "n": e["n"], "ci": e["ci"],
              "overall": rep[r]["by"][dim]["overall"]["value"]}
             for r in ("fallback", "match") for dim, s in rep[r]["by"].items()
             for g, e in s["groups"].items() if e["flagged"]]
    acc = set(accepted)
    open_flags = [f for f in flags if f"{f['rate']}:{f['slice']}" not in acc]
    rep.update(flags=flags, accepted=sorted(acc), open_flags=len(open_flags), **{"pass": not open_flags})
    return rep


def chart(rep, out):
    """Match rate per repo with its interval, the overall line and the flag line."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    s = rep["match"]["by"]["repo"]
    groups = list(s["groups"])
    v = [s["groups"][g]["value"] for g in groups]
    lo = [v[i] - s["groups"][g]["ci"][0] for i, g in enumerate(groups)]
    hi = [s["groups"][g]["ci"][1] - v[i] for i, g in enumerate(groups)]
    overall, gap = s["overall"]["value"], rep["settings"]["max_gap"]
    fig, ax = plt.subplots(figsize=(8, 4))
    colors = ["#c0392b" if s["groups"][g]["flagged"] else "#7f8c8d" if not s["groups"][g]["judged"] else "#2c7fb8"
              for g in groups]
    ax.bar(groups, v, yerr=[lo, hi], color=colors, capsize=3)
    ax.axhline(overall, color="black", lw=1, label=f"overall {overall:.3f}")
    ax.axhline(overall - gap, color="#c0392b", lw=1, ls="--", label=f"flag line (overall - {gap})")
    ax.set_xticks(range(len(groups)), [f"{g}\nN={s['groups'][g]['n']}" for g in groups], fontsize=8)
    ax.set_ylabel("diff lines matching the real after-line")
    ax.set_ylim(0, 1)
    ax.set_title("Diff match rate per repo (red: flagged, grey: N < 30, not judged)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = Path(out) / "llm_bias_match.png"
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def load_calls(run_ids=None):
    """meridian-agents' llm_calls rows, or (None, why) when the store has none."""
    from model import tracking
    try:
        calls = tracking.load_llm_calls(run_ids)
    except Exception as e:  # no agents experiment, or no table in it yet
        return None, f"{type(e).__name__}: {e}"
    return calls, ""


def main(argv=None):
    p = argparse.ArgumentParser(description="Chaitali: LLM bias by slice (step M). Run from Model-Pipeline/.")
    p.add_argument("--run-id", nargs="+", help="meridian-agents runs to read (default: all)")
    p.add_argument("--diffs", default=str(DIFFS), help="diff match counts per repo")
    p.add_argument("--config", default=str(MODEL_TOML))
    p.add_argument("--out", default=str(REPORTS))
    p.add_argument("--no-calls", action="store_true", help="don't read MLflow (as if no agent had run)")
    p.add_argument("--calls", nargs="+", help="call rows from meridian.agents --calls, instead of MLflow")
    p.add_argument("--ship", help="with --calls: the model the writer ships, which the bar judges")
    p.add_argument("--no-charts", action="store_true")
    a = p.parse_args(argv)
    from model.data import load_config
    cfg = load_config(a.config)
    try:
        b = bias_settings(cfg)
    except ValueError as e:
        sys.exit(str(e))
    if a.calls:
        calls, why = pd.concat([pd.read_json(f, lines=True) for f in a.calls], ignore_index=True), ""
    else:
        calls, why = (None, "--no-calls") if a.no_calls else load_calls(a.run_id)
    diffs = pd.read_csv(a.diffs)
    rep = build(calls, diffs, b, cfg.get("llm_bias", {}).get("accepted", []), a.ship)
    if why and not rep["fallback"]["note"]:
        rep["fallback"]["note"] = why
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "llm_bias.json"
    path.write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8", newline="\n")
    files = [path] + ([] if a.no_charts else [chart(rep, out)])
    f = rep["fallback"]
    print(f"fallback: {f['texts']} LLM texts" + (f" ({f['note']})" if f["note"] else ""))
    m = rep["match"]["by"]["repo"]
    print(f"match: {rep['match']['lines']:,} lines, overall {m['overall']['value']:.3f}")
    for x in rep["flags"]:
        print(f"  FLAG {x['rate']} {x['slice']} {x['value']:.3f} (n={x['n']}, interval {x['ci'][0]:.3f}-"
              f"{x['ci'][1]:.3f}) vs overall {x['overall']:.3f}")
    print(f"pass: {rep['pass']} ({rep['open_flags']} open flags); wrote {', '.join(p.name for p in files)}")
    return rep


if __name__ == "__main__":
    main()
