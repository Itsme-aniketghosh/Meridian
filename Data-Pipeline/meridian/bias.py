"""Bias · does the pipeline serve every team, module and repo equally? Data slicing with Fairlearn's MetricFrame.

    python -m meridian.bias --run-id <run_id> --config meridian.toml      # after join

There is no trained model yet: the pipeline's "models" are its rules. Three of them decide what a team sees, and
each can be checked slice by slice:

1. Owner rule (the dataset whose teams are people's ticket components: Spark, the only one with real teams). Week
   1's test, one window back: does the file's owner team predict the team of the next author? Scored only where the
   next author has a team (an unassigned one is wrong by construction, see owner_report). Sliced by top-level module
   (sql/, core/, python/, ...), by the next author's team and by how many commits they made before the cut. Coverage
   by module: how often the rule owns a file at all (the rest is 'unowned'), how often the next author has a team.
2. Ticket -> code mapping (Jira). The share of tickets mapped to code, by component, issue type and year filed: an
   unmapped team's work is invisible to every view.
3. Resolver (Django and the packages). The share of edit-role candidates kept as call sites, by repo.

Every metric is a share, so each slice gets a Wilson 95% interval. A slice is judged when it has at least [bias] min_n
rows, and flagged when its whole interval is more than max_gap below the overall value. Each slice carries its flag,
so a view can caveat a low-coverage module, team or component. For the owner rule, mitigations are scored the same
way, so the trade-off is on record: lower min_shares, longer lookbacks for a person's team, and falling back to the
last toucher's team where the rule says 'unowned'. The best one is picked one window earlier and confirmed on the
current cut (held_out). They are proposals: applying one changes meridian.toml (or owners.py), which means a new run_id.

Reads out/<run_id>/ tables and settings.toml [bias] (and [validate.dates], what a valid date is). Writes
out/<run_id>/bias.json. Never writes a table.
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from fairlearn.metrics import MetricFrame, count, selection_rate
from scipy.stats import binomtest
from sklearn.metrics import accuracy_score

import meridian.owners as O
from meridian.common import (CONFIG, data_root, days_before, handoff, load_config, load_settings, log, parser,
                             repo_checkout)


def author_dataset(cfg):
    """(dataset, its repos): the dataset whose team is a person's ticket component ([teams] rule person_component).
    Spark today: the only dataset with real teams. None: no owner-rule slicing."""
    for ds, rule in sorted(cfg["teams"].items()):
        if rule.get("rule") == "person_component":
            return ds, sorted(r["repo"] for r in cfg["datasets"][ds]["repos"])
    return None, []


def stream(path, keep=lambda r: True):
    with open(path, encoding="utf-8", newline="") as f:
        for line in f:
            if line.strip() and keep(r := json.loads(line)):
                yield r


def module(path):
    """Top-level folder, the closest thing Spark has to a location: sql/, core/, python/, connector/, ...
    'repo:path' (a dataset with several repos) keeps its repo."""
    if ":" in path:
        repo, path = path.split(":", 1)
        return f"{repo}:{module(path)}"
    return path.split("/", 1)[0] if "/" in path else "(root)"


def owner_rows(cfg, out, root, run_id, variants, earlier=0):
    """(rows, unassigned, cut): one row per Spark source file with commits before and after the cut (week 1's test,
    owners.py). For each variant {name: (min_share, team lookback days)}: the owner team, the next author's team, the
    last toucher's team. Also the next author's commits before the cut, binned. earlier = k moves the cut k windows back
    and keeps next authors before the old cut: what is picked there never sees the current cut's answers."""
    run = next(stream(out / "run.jsonl"))
    fmt, w = cfg["run"]["time_format"], cfg["ownership"]["window_days"]
    back = lambda t, d=w: days_before(t, d, fmt)
    until = back(run["snapshot_at"], earlier * w)
    cut = back(until)
    ds, repos = author_dataset(cfg)
    in_ds = lambda r: r["repo"] in repos
    commits = list(stream(out / "git_commit.jsonl", in_ds))
    files = list(stream(out / "commit_file.jsonl", in_ds))
    keys = defaultdict(list)
    for r in stream(out / "commit_ticket.jsonl", in_ds):
        keys[r["sha"]].append(r["key"])
    components = {t["key"]: json.loads(t["components"])
                  for t in stream(out / "ticket.jsonl", lambda t: t["tracker"] == "jira")}
    pid = {a["email"]: a["person_id"] for a in stream(out / "person_alias.jsonl")}
    bots = {p["person_id"] for p in stream(out / "person.jsonl") if p["is_bot"]}
    unassigned = cfg["teams"][ds]["unassigned"] if ds else "unassigned"
    teams = {d: O.person_teams(commits, keys, components, pid, back(cut, d), cut, unassigned)
             for d in sorted({d for _, d in variants.values()})}
    prior = Counter(pid[c["author_email"]] for c in commits if not c["is_merge"] and c["authored_at"] <= cut)
    src, test = (re.compile(cfg["checks"][k]) for k in ("source_files", "test_files"))
    touched = {}
    for repo in repos:   # a file's history is per repo: renames are followed inside one
        co = repo_checkout(root / "snapshots" / run_id, repo)
        skip = O.ignore_revs(co, json.loads(run["repo_shas"])[repo],
                             cfg["ownership"]["ignore_revs_file"]) if co.exists() else set()
        mine = lambda r: r["repo"] == repo
        for path, cs in O.touches([c for c in commits if mine(c)], [f for f in files if mine(f)], skip).items():
            touched[path if len(repos) == 1 else f"{repo}:{path}"] = cs
    rows = []
    for path, cs in sorted(touched.items()):
        ts = [(c["authored_at"], pid[c["author_email"]]) for c in cs if pid[c["author_email"]] not in bots]
        if not src.search(path) or test.search(path):
            continue
        before = [x for x in ts if x[0] <= cut]
        after = [x for x in ts if cut < x[0] <= until]
        if not before or not after:
            continue
        window = [x for x in before if back(cut) < x[0]]
        k = prior[after[0][1]]
        row = {"path": path, "module": module(path),
               "prior": "0" if not k else "1" if k == 1 else "2-10" if k <= 10 else ">10"}
        for name, (share, d) in variants.items():
            team_of = lambda p: teams[d].get(p, unassigned)
            row[f"owner|{name}"] = O.team_owner(window, team_of, unassigned, share)[0]
            row[f"next|{name}"], row[f"last|{name}"] = team_of(after[0][1]), team_of(before[-1][1])
        rows.append(row)
    return pd.DataFrame(rows), unassigned, cut


num = lambda v: None if pd.isna(v) else round(float(v), 4)   # JSON has no NaN


def frame(metrics, y_true, y_pred, by, b):
    """MetricFrame -> {overall, by_group, difference, ratio, tested, flagged}, rounded, ready for JSON.

    metrics = {share metric, "n": count}. Each slice gets its Wilson 95% interval (ci_low, ci_high) and is judged when
    n >= min_n: flagged when ci_high < overall - max_gap, a gap its own noise can't explain. Fairlearn's difference
    (max - min) and ratio (min / max) are over the judged slices only: an N = 1 slice would make both meaningless.
    None when there are no rows: MetricFrame refuses an empty slicing."""
    if not len(y_true):
        return None
    mf = MetricFrame(metrics=metrics, y_true=y_true, y_pred=y_pred, sensitive_features=by)
    metric = next(m for m in metrics if m != "n")
    line = mf.overall[metric] - b["max_gap"]
    by_group = {}
    for k, row in mf.by_group.iterrows():
        n = int(row["n"])
        ci = binomtest(round(row[metric] * n), n).proportion_ci(method="wilson")
        by_group[str(k)] = {metric: num(row[metric]), "n": n, "ci_low": num(ci.low), "ci_high": num(ci.high),
                            "flagged": bool(n >= b["min_n"] and ci.high < line)}
    # Series, not arrays: Fairlearn squeezes a 1-row array to 0-d and refuses it
    d = pd.DataFrame({"t": np.asarray(y_true), "p": np.asarray(y_pred), "s": np.asarray(by)})
    d = d[d["s"].map(d["s"].value_counts()) >= b["min_n"]]
    spread = MetricFrame(metrics=metrics[metric], y_true=d["t"], y_pred=d["p"],
                         sensitive_features=d["s"]) if len(d) else None
    flagged = sorted((g for g, m in by_group.items() if m["flagged"]), key=lambda g: by_group[g][metric])
    return {"overall": {metric: num(mf.overall[metric]), "n": int(mf.overall["n"])}, "by_group": by_group,
            "difference": num(spread.difference()) if len(d) else None,
            "ratio": num(spread.ratio()) if len(d) else None,
            "tested": sum(m["n"] >= b["min_n"] for m in by_group.values()),
            "flagged": [{"slice": g, metric: by_group[g][metric], "n": by_group[g]["n"],
                         "ci_high": by_group[g]["ci_high"],
                         "gap": round(mf.overall[metric] - by_group[g][metric], 4)} for g in flagged]}


def owner_report(df, unassigned, variants, b):
    """The owner rule as configured (the first variant) by slice, and every variant scored as a mitigation.

    Accuracy is scored where the next author has a team. The label is circular: the same person_teams (same lookback)
    gives the prediction and the label, and team_owner never names `unassigned`, so an unassigned next author is wrong
    by construction under every variant. How often that happens is coverage, reported apart (assigned_share). Each
    variant is scored against its own labels: a longer lookback also relabels next authors."""
    acc = {"accuracy": accuracy_score, "n": count}
    base = next(iter(variants))
    owner, nxt = df[f"owner|{base}"], df[f"next|{base}"]
    owned, assigned = owner != "unowned", nxt != unassigned
    ok = owned & assigned
    report = {f"accuracy_by_{by}": frame(acc, nxt[ok], owner[ok], df.loc[ok, col], b)
              for by, col in (("module", "module"), ("next_author_team", f"next|{base}"),
                              ("next_author_prior_commits", "prior"))}
    for name, hit in (("owned_share", owned), ("assigned_share", assigned)):
        report[f"{name}_by_module"] = frame({name: selection_rate, "n": count}, hit.astype(int), hit.astype(int),
                                            df["module"], b)

    preds = {name: (df[f"owner|{name}"], df[f"next|{name}"]) for name in variants}
    preds["fallback to last toucher"] = (owner.where(owned, df[f"last|{base}"].replace(unassigned, "unowned")), nxt)
    report["mitigations"] = {}
    for name, (pred, truth) in preds.items():
        kept = pred != "unowned"
        ok = kept & (truth != unassigned)
        # nothing scored (every next author unassigned): no slices, accuracy 0.0 as below
        r = (frame(acc, truth[ok], pred[ok], df.loc[ok, "module"], b)
             or {"overall": {"accuracy": 0.0}, "by_group": {}, "flagged": []})
        report["mitigations"][name] = {
            "files_owned": int(kept.sum()), "of": len(df), "accuracy_assigned": r["overall"]["accuracy"],
            "accuracy": round(float((pred[kept] == truth[kept]).mean()), 4) if kept.any() else 0.0,   # owners.py's
            "next_author_unassigned": round(float((truth[kept] == unassigned).mean()), 4) if kept.any() else 0.0,
            "flagged_modules": len(r["flagged"]),
            "worst_module_gap": round(max([0] + [r["overall"]["accuracy"] - m["accuracy"]
                                                 for m in r["by_group"].values() if m["n"] >= b["min_n"]]), 4)}
    return report


def held_out(early, now, unassigned, variants, reps=1000, seed=0):
    """Pick the variant with the best accuracy (next authors with a team) at the earlier cut, then score it against the
    configured one on the current cut: the delta, with a seeded paired bootstrap 95% interval over files."""
    def hits(df, name):   # per file: scored, right
        pred, truth = df[f"owner|{name}"], df[f"next|{name}"]
        ok = (pred != "unowned") & (truth != unassigned)
        return ok.to_numpy(), (ok & (pred == truth)).to_numpy()
    acc = lambda ok, right: right.sum() / ok.sum() if ok.any() else 0.0
    picked = {name: num(acc(*hits(early, name))) for name in variants} if len(early) else {}
    base = next(iter(variants))
    best = max(picked, key=picked.get, default=base)   # a tie keeps what's configured
    (ok0, r0), (ok1, r1) = hits(now, base), hits(now, best)
    i = np.random.default_rng(seed).integers(0, len(now), (reps, len(now)))
    with np.errstate(all="ignore"):   # a resample with nothing scored is NaN, left out
        delta = r1[i].sum(1) / ok1[i].sum(1) - r0[i].sum(1) / ok0[i].sum(1)
    lo, hi = np.nanpercentile(delta, [2.5, 97.5]) if not np.isnan(delta).all() else (None, None)
    return {"picked_at_earlier_cut": best, "accuracy_at_earlier_cut": picked,
            "accuracy_now": {base: num(acc(ok0, r0)), best: num(acc(ok1, r1))},
            "delta": num(acc(ok1, r1) - acc(ok0, r0)), "delta_ci": [num(lo), num(hi)], "bootstrap": reps, "seed": seed}


def mapping_report(out, b, earliest):
    """Share of Spark tickets with a code target, by component, issue type and the year it was filed (old tickets'
    files are often gone by the pinned SHA, so the year shows whether a component's gap is just its age). A ticket
    with several components counts once in each, so the component slicings' overall is over (ticket, component).
    An invalid created_at (validate's dates check: before earliest or after snapshot_at, like the 289 Spark tickets
    Jira dates 0010-0012) puts the ticket in no year slice; it still counts by component and type."""
    mapped = {r["key"] for r in stream(out / "ticket_map.jsonl", lambda r: r["target_kind"] != "unmapped")}
    rows = [{"component": json.loads(t["components"]) or ["(none)"], "type": t["effective_type"],
             "at": t["created_at"], "year": t["created_at"][:4], "mapped": int(t["key"] in mapped)}
            for t in stream(out / "ticket.jsonl", lambda t: t["tracker"] == "jira")]
    if not rows:   # no Jira tickets (a dataset without a tracker): nothing to slice, like owner_rule without owners
        return {}
    df = pd.DataFrame(rows)
    dated = df[df["at"].between(earliest, next(stream(out / "run.jsonl"))["snapshot_at"])]
    comp = df.explode("component", ignore_index=True)
    share = {"mapped_share": selection_rate, "n": count}
    report = {f"mapped_share_by_{by}": frame(share, d["mapped"], d["mapped"], d[by], b)
              for by, d in (("component", comp), ("type", df), ("year", dated))}
    if since := b.get("recent_since"):   # the same slices on recent tickets only: what's left once age is held fixed
        recent = comp[comp["year"] >= since]
        report[f"mapped_share_by_component_since_{since}"] = frame(share, recent["mapped"], recent["mapped"],
                                                                   recent["component"], b)
    return report


def resolver_report(cfg, out, b):
    """Share of edit-role candidates the join kept as call sites (not dropped as look-alikes), by repo."""
    roles = set(cfg["scanner"]["edit_roles"])
    det = {(r["repo"], r["path"], r["line"], r["col"]): r["detection"] for r in stream(out / "resolution.jsonl")}
    rows = [{"repo": c["repo"], "kept": int(det.get((c["repo"], c["path"], c["line"], c["col"])) != "dropped")}
            for c in stream(out / "candidate.jsonl", lambda c: c["role"] in roles)]
    if not rows:   # no edit-role candidates: nothing to slice
        return {}
    df = pd.DataFrame(rows)
    return {"kept_share_by_repo": frame({"kept_share": selection_rate, "n": count}, df["kept"], df["kept"],
                                        df["repo"], b)}


def main(run_id, config=CONFIG, root=None, settings=None):
    cfg, root = load_config(config), Path(root or data_root())
    out = handoff(cfg, run_id, root)
    need = ["run", "git_commit", "commit_file", "commit_ticket", "ticket", "person", "person_alias", "ticket_map",
            "candidate", "resolution"]
    if missing := [t for t in need if not (out / f"{t}.jsonl").exists()]:
        raise SystemExit(f"missing in {out}: {', '.join(missing)}. Run the pipeline through join first")
    b = load_settings("bias", settings)
    b = {"min_n": b.get("min_n", 30), "max_gap": b.get("max_gap", 0.10)} | b
    share, days = cfg["ownership"]["min_share"], cfg["ownership"]["window_days"]
    variants = {f"min_share {share}, teams from {days} days": (share, days)}   # as meridian.toml has it: first
    variants |= {f"min_share {s}": (s, days) for s in b.get("min_shares", []) if s != share}
    variants |= {f"teams from {d} days": (share, d) for d in b.get("team_lookback_days", []) if d != days}

    report = {"run_id": run_id, "min_n": b["min_n"], "max_gap": b["max_gap"]}
    df, unassigned, cut = owner_rows(cfg, out, root, run_id, variants)
    if len(df):
        report["owner_rule"] = owner_report(df, unassigned, variants, b) | {"cut": cut}
        # shortcut: reads the tables a second time for the earlier cut; load once if bias outgrows its timeout
        early, _, early_cut = owner_rows(cfg, out, root, run_id, variants, earlier=1)
        report["owner_rule"]["held_out"] = held_out(early, df, unassigned, variants) | {"cut": early_cut,
                                                                                        "files": len(early)}
    report["ticket_mapping"] = mapping_report(out, b, load_settings("validate", settings)["dates"]["earliest"])
    report["resolver"] = resolver_report(cfg, out, b)
    frames = [(part, name, r) for part in ("owner_rule", "ticket_mapping", "resolver")
              for name, r in sorted(report.get(part, {}).items()) if isinstance(r, dict) and "by_group" in r]
    report["slices_tested"] = sum(r["tested"] for *_, r in frames)
    report["slices_flagged"] = sum(len(r["flagged"]) for *_, r in frames)
    (out / "bias.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    for part, name, r in frames:
        metric = next(m for m in r["overall"] if m != "n")
        log(f"{part + ' ' + name:45} overall {r['overall'][metric]:.3f} (N={r['overall']['n']:,}) · "
            f"{r['tested']} of {len(r['by_group'])} slices judged · {len(r['flagged'])} flagged · "
            f"difference {r['difference']} · ratio {r['ratio']}" +
            "".join(f" · {f['slice']} {f[metric]:.3f} (N={f['n']:,})" for f in r["flagged"][:3]))
    for name, m in report.get("owner_rule", {}).get("mitigations", {}).items():
        log(f"  owner rule, {name:38} owns {m['files_owned']:,} of {m['of']:,} files · accuracy {m['accuracy']:.3f}, "
            f"{m['accuracy_assigned']:.3f} on next authors with a team · next author unassigned "
            f"{m['next_author_unassigned']:.1%} · {m['flagged_modules']} modules flagged · worst module gap "
            f"{m['worst_module_gap']:.3f}")
    if h := report.get("owner_rule", {}).get("held_out"):
        log(f"  held out: picked '{h['picked_at_earlier_cut']}' at the cut {h['cut'][:10]} ({h['files']:,} files); "
            f"on the current cut accuracy {h['accuracy_now']}, delta {h['delta']} (95% bootstrap {h['delta_ci']})")
    log(f"{report['slices_tested']} slices judged (N >= {b['min_n']}), {report['slices_flagged']} flagged "
        f"-> {out / 'bias.json'}")
    return report


if __name__ == "__main__":
    a = parser(__doc__).parse_args()
    main(a.run_id, a.config)
