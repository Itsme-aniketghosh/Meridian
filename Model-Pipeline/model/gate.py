"""The gate (step H, Chaitali): may this candidate be registered? Run from Model-Pipeline/, after evaluate and bias.

Reads the four Contract 4 files (packets/README.md) and checks them against model.toml [gate]:

    inputs      metrics.json, bias_report.json and seed_noise.json are there with every key the gate reads
                (model/contracts.py). registry/champion.json may be missing: then this is the first model
    agree       they describe the same thing: the same split, the same N, the same candidate
    bar         the candidate's PR-AUC beats [gate] reference's by more than [gate] min_lift
    bias        bias_report.json passes (no flagged judged slice outside [bias] accepted)
    champion    the candidate isn't below the champion's PR-AUC by more than [gate] noise_sds seed-noise standard
                deviations (decision 6), and both are on the same feature table

Writes reports/gate.json with every check, its numbers and why. A pass exits 0. A fail exits 1 and sends one alert
with every failed check, through Data-Pipeline's meridian.alert (Slack and email as settings.toml [alerts] says).
An alert that can't be sent is logged and never changes the exit code. Every number the gate uses is in [gate].

    python -m model.gate                                  # reports/ and registry/champion.json
    python -m model.gate --reports <dir> --champion <path> --config <toml>   # run_all --fake's temp folder
    python -m model.gate --decide 6                       # decision 6: which tolerance, on seeds and planted models
"""
import argparse
import importlib
import json
import logging
import math
import sys
from pathlib import Path

import numpy as np

from model import contracts, registry

HERE = Path(__file__).resolve().parents[1]  # Model-Pipeline/
ROOT = HERE.parent
MODEL_TOML = HERE / "model.toml"
REPORTS = HERE / "reports"
FEATURES = HERE / "data" / "features.parquet"
GATE_KEYS = ("split", "reference", "min_lift", "noise_sds")
FILES = {"metrics": "metrics.json", "bias_report": "bias_report.json", "seed_noise": "seed_noise.json"}
LOG = logging.getLogger("meridian.gate")


def gate_settings(cfg):
    """model.toml [gate], every key there and in range."""
    g = cfg.get("gate")
    if not isinstance(g, dict):
        raise ValueError("model.toml has no [gate] section")
    if missing := [k for k in GATE_KEYS if k not in g]:
        raise ValueError(f"model.toml [gate] has no {', '.join(missing)}")
    if g["noise_sds"] < 0:
        raise ValueError(f"[gate] noise_sds is {g['noise_sds']}, below 0")
    return {k: g[k] for k in GATE_KEYS}


def below_champion(value, champion, sd, noise_sds):
    """True when value is below the champion's by more than noise_sds seed-noise standard deviations (decision 6)."""
    return value < champion - noise_sds * sd


def read_inputs(reports, champion_path):
    """(docs, problems): each Contract 4 file read and checked, or a problem naming the file and what's wrong.
    A missing champion is no problem: docs["champion"] is None, the first model."""
    docs, problems = {}, []
    for kind, name in FILES.items():
        try:
            docs[kind] = contracts.read(Path(reports) / name, kind)
        except ValueError as e:
            problems.append(str(e))
    if "seed_noise" in docs:
        try:
            registry.read_seed_noise(Path(reports) / FILES["seed_noise"])
        except registry.ContractError as e:
            problems.append(str(e))
    docs["champion"] = None
    if Path(champion_path).exists():
        try:
            docs["champion"] = registry.read_champion(champion_path)
        except registry.ContractError as e:
            problems.append(str(e))
    return docs, problems


def check(docs, g):
    """Every check on read inputs, in order: [{"check", "ok", "why", ...numbers}]."""
    m, b, s, champ = docs["metrics"], docs["bias_report"], docs["seed_noise"], docs["champion"]
    cand = m["candidate"]["column"]
    out = []

    def add(name, ok, why, **numbers):
        out.append({"check": name, "ok": bool(ok), "why": why, **numbers})

    differ = []
    for what, values in (("split", (g["split"], m["split"], b["split"], s.get("split", g["split"]))),
                         ("n", (m["n"], b["n"], s["n"])), ("candidate", (cand, b["candidate"]))):
        if len(set(values)) > 1:
            differ.append(f"{what} {' / '.join(map(str, values))}")
    kind = m["candidate"].get("kind")
    if kind is not None and kind != s["kind"]:
        differ.append(f"seed noise is for {s['kind']}, the candidate is {kind}")
    add("agree", not differ, "; ".join(differ) or f"{g['split']}, N = {m['n']:,}, candidate {cand}")

    value, ref = m["models"][cand]["pr_auc"]["value"], m["models"][g["reference"]]["pr_auc"]["value"]
    lift = value - ref
    add("bar", lift > g["min_lift"],
        f"PR-AUC {value:.4f} vs {g['reference']} {ref:.4f}: {lift:+.4f}, must be above {g['min_lift']:+.4f}",
        value=value, reference=ref, lift=lift)

    accepted = set(b.get("accepted", []))
    still = [f"{f['slice']}:{f['metric']}" for f in b["flags"] if f"{f['slice']}:{f['metric']}" not in accepted]
    add("bias", b["pass"] is True, f"{b['open_flags']} open flags of {len(b['flags'])}"
        + ("" if b["pass"] is True else ": " + (", ".join(still) or "the report says it fails")))

    if champ is None:
        add("champion", True, "no champion yet: the first model")
    elif champ["metrics"]["features_md5"] != m.get("features_md5"):
        add("champion", False, f"the champion's numbers are on another feature table "
            f"({champ['metrics']['features_md5'][:8]}, this run {str(m.get('features_md5'))[:8]}): "
            "re-score it on this table first (Rochan's decision 7)")
    else:
        top, sd = champ["metrics"]["pr_auc"]["value"], s["sd"]
        floor = top - g["noise_sds"] * sd
        add("champion", not below_champion(value, top, sd, g["noise_sds"]),
            f"PR-AUC {value:.4f} vs champion {champ['version'][:8]} {top:.4f}, floor {floor:.4f} "
            f"({g['noise_sds']} x seed-noise sd {sd:.4f})", value=value, champion=top, floor=floor)
    return out


def alert(subject, lines):
    """One alert through Data-Pipeline's meridian.alert. Any failure to send is logged, never raised."""
    try:
        if str(ROOT / "Data-Pipeline") not in sys.path:
            sys.path.append(str(ROOT / "Data-Pipeline"))
        return importlib.import_module("meridian.alert").send(subject, lines, event="failure")
    except Exception as e:  # an alert must never change the gate's answer
        LOG.error(f"gate: alert not sent: {e!r}")
        return []


def run(reports=REPORTS, champion=registry.CHAMPION, config=MODEL_TOML, out=None, send=alert):
    """The gate. Returns (exit code, gate.json as a dict); writes it to out (default: reports/gate.json)."""
    from model.data import load_config
    problems, checks, docs = [], [], {}
    try:
        g = gate_settings(load_config(config))
    except (ValueError, OSError, SystemExit) as e:  # load_config exits on a broken model.toml
        g, problems = None, [f"{config}: {e}"]
    if g:
        docs, problems = read_inputs(reports, champion)
        if not problems:
            try:
                checks = check(docs, g)
            except (KeyError, TypeError) as e:  # a key the contract has, holding the wrong thing
                problems.append(f"inputs: can't read {e!r}")
    checks = [{"check": "inputs", "ok": not problems, "why": "; ".join(problems) or "all read"}, *checks]
    failed = [c for c in checks if not c["ok"]]
    cand = docs.get("metrics", {}).get("candidate", {}).get("column", "?")
    result = {"pass": not failed, "candidate": cand, "settings": g, "first_model": docs.get("champion") is None,
              "checks": checks}
    path = Path(out) if out else Path(reports) / "gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    for c in checks:
        print(f"{'ok  ' if c['ok'] else 'FAIL'} {c['check']}: {c['why']}", flush=True)
    if failed:
        result["alerted"] = send(f"model gate failed: {cand} not registered",
                                 [f"{c['check']}: {c['why']}" for c in failed])
        print(f"gate: FAIL ({len(failed)} of {len(checks)} checks); alert sent to {result['alerted'] or 'no channel'}")
        return 1, result
    print(f"gate: pass ({len(checks)} checks); {path}")
    return 0, result


# ---------- decision 6: the tolerance ----------

OPTIONS = {"0": 0.0, "2 sd": 2.0, "2.83 sd (2 sd of a difference)": 2 * math.sqrt(2)}


def planted():
    """The planted bad models of decision 6: name -> (how its train rows change, the columns it drops)."""
    from model.train import NUMERIC, SIZE
    return {"shuffled labels": ("shuffle", ()),
            "size only": (None, (*[c for c in NUMERIC if c not in SIZE], "ticket_type")),
            "half the features": (None, tuple(NUMERIC[1::2]))}


def _planted_score(df, kind, params, seed, change, drop):
    """One planted model's val PR-AUC, fit by train.fit_and_score (top-level, so pmap can pickle it)."""
    from model.train import fit_and_score, pr_auc
    if change == "shuffle":
        df = df.copy()
        train = df["split"] == "train"
        df.loc[train, "label"] = np.random.default_rng(seed).permutation(df.loc[train, "label"].to_numpy())
    _, scores, scored = fit_and_score(df, kind, seed, params, drop=drop)
    return pr_auc(scored["label"].astype(int).to_numpy(), scores)


def decision_6(table, noise, kind, params, seed, reference):
    """Every option on Rachna's seeds (every ordered pair) and the planted models (against the candidate's seed)."""
    from model.train import pmap
    values, sd = dict(zip(noise["seeds"], noise["pr_auc"])), noise["sd"]
    champion = values[seed]
    plants = planted()
    scores = dict(zip(plants, pmap(_planted_score, [(table, kind, params, seed, ch, dr)
                                                    for ch, dr in plants.values()])))
    pairs = [(a, b) for a in values for b in values if a != b]
    options = {}
    for name, k in OPTIONS.items():
        seeds_pass = sum(not below_champion(values[c], values[h], sd, k) for h, c in pairs)
        bad_fail = {p: bool(below_champion(v, champion, sd, k)) for p, v in scores.items()}
        options[name] = {"noise_sds": k, "tolerance": k * sd, "seed_pairs_pass": seeds_pass,
                         "seed_pairs": len(pairs), "planted_fail": sum(bad_fail.values()),
                         "planted": len(bad_fail), "planted_failed": bad_fail}
    full = [n for n, o in options.items() if o["seed_pairs_pass"] == len(pairs) and o["planted_fail"] == len(plants)]
    if full:
        picked, why = min(full, key=lambda n: options[n]["noise_sds"]), "the smallest with 20 of 20 and 3 of 3"
    else:
        caught = [n for n, o in options.items() if o["planted_fail"] == len(plants)] or list(options)
        picked = max(caught, key=lambda n: options[n]["seed_pairs_pass"])
        why = "none gets both: the one catching every planted model with the most seed pairs passing"
    return {"kind": kind, "params": params, "seed": seed, "split": noise["split"], "n": noise["n"],
            "positives": noise.get("positives"), "seed_pr_auc": values, "sd": sd, "champion": champion,
            "planted_pr_auc": scores, "reference": reference, "options": options, "picked": picked, "why": why}


def main(argv=None):
    p = argparse.ArgumentParser(description="Chaitali: the model gate (step H). Run from Model-Pipeline/.")
    p.add_argument("--reports", default=str(REPORTS), help="where metrics, bias_report and seed_noise.json are")
    p.add_argument("--champion", default=str(registry.CHAMPION), help="registry/champion.json (missing: first model)")
    p.add_argument("--config", default=str(MODEL_TOML))
    p.add_argument("--out", help="gate.json (default: <reports>/gate.json)")
    p.add_argument("--decide", type=int, choices=(6,), help="run decision 6's test instead")
    p.add_argument("--features", default=str(FEATURES), help="decision 6: the table the planted models fit on")
    p.add_argument("--no-mlflow", action="store_true", help="decision 6: don't log a run")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if a.decide != 6:
        return run(a.reports, a.champion, a.config, a.out)[0]

    import pandas as pd

    from model.data import load_config
    cfg = load_config(a.config)
    noise = registry.read_seed_noise(Path(a.reports) / FILES["seed_noise"])
    cand = json.loads((Path(a.reports) / "candidate.json").read_text(encoding="utf-8"))
    r = decision_6(pd.read_parquet(a.features), noise, cand["kind"], cand["params"], int(cand["seed"]),
                   cfg.get("gate", {}).get("reference", "lines_x_files"))
    m = Path(a.reports) / FILES["metrics"]
    if m.exists():
        models = json.loads(m.read_text(encoding="utf-8"))["models"]
        r["reference_pr_auc"] = models[r["reference"]]["pr_auc"]["value"]
    path = Path(a.reports) / "decision_chaitali-6.json"
    path.write_text(json.dumps(r, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(r, indent=2))
    if not a.no_mlflow:
        import mlflow

        from model import tracking
        with tracking.start_run("gate-decision-6", owner="chaitali", decision="chaitali-6", config=a.config,
                                features=a.features) as run_:
            mlflow.log_params({"picked": r["picked"], "kind": r["kind"]})
            mlflow.log_metrics({"sd": r["sd"], **{f"planted_{k.replace(' ', '_')}": v
                                                  for k, v in r["planted_pr_auc"].items()}})
            mlflow.log_artifact(str(path))
            print(f"MLflow run {run_.info.run_id} in meridian-defect-risk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
