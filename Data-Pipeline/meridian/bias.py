"""Bias · does the pipeline serve every team, module and repo equally? Data slicing with Fairlearn's MetricFrame.

    python -m meridian.bias --run-id <run_id> --config meridian.toml      # after join

There is no trained model yet: the pipeline's "models" are its rules. Three of them decide what a team sees, and
each can be checked slice by slice:

1. Owner rule (the dataset whose teams are people's ticket components: Spark, the only one with real teams). Week
   1's test, one window back: does the file's owner team predict the team of the next author? Sliced by top-level
   module (sql/, core/, python/, ...) and by the next author's team. Also how often the rule owns a file at all (the
   rest is 'unowned'), by module.
2. Ticket -> code mapping (Jira). The share of tickets mapped to code, by component, issue type and year filed: an
   unmapped team's work is invisible to every view.
3. Resolver (Django and the packages). The share of edit-role candidates kept as call sites, by repo.

A slice with at least [bias] min_n rows whose metric is more than max_gap below the overall value is flagged. For the
owner rule, mitigations are scored the same way, so the trade-off is on record: lower min_shares, longer lookbacks
for a person's team, and falling back to the last toucher's team where the rule says 'unowned'. They are proposals:
applying one changes meridian.toml (or owners.py), which means a new run_id.

Reads out/<run_id>/ tables and settings.toml [bias]. Writes out/<run_id>/bias.json. Never writes a table.
"""
import json, re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from fairlearn.metrics import MetricFrame, count, selection_rate
from sklearn.metrics import accuracy_score

import meridian.owners as O
from meridian.common import CONFIG, data_root, handoff, load_config, load_settings, log, parser

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


def owner_rows(cfg, out, root, run_id, variants):
    """One row per Spark source file with commits before and after the cut (week 1's test, owners.py). For each
    variant {name: (min_share, team lookback days)}: the owner team, the next author's team, the last toucher's team."""
    run = next(stream(out / "run.jsonl"))
    fmt = cfg["run"]["time_format"]
    back = lambda t, d=cfg["ownership"]["window_days"]: (datetime.strptime(t, fmt).replace(tzinfo=timezone.utc)
                                                         - timedelta(days=d)).strftime(fmt)
    cut = back(run["snapshot_at"])
    ds, repos = author_dataset(cfg)
    in_ds = lambda r: r["repo"] in repos
    commits = list(stream(out / "git_commit.jsonl", in_ds))
    files = list(stream(out / "commit_file.jsonl", in_ds))
    keys = defaultdict(list)
    for r in stream(out / "commit_ticket.jsonl", in_ds):
        keys[r["sha"]].append(r["key"])
    components = {t["key"]: json.loads(t["components"]) for t in stream(out / "ticket.jsonl", lambda t: t["tracker"] == "jira")}
    pid = {a["email"]: a["person_id"] for a in stream(out / "person_alias.jsonl")}
    bots = {p["person_id"] for p in stream(out / "person.jsonl") if p["is_bot"]}
    unassigned = cfg["teams"][ds]["unassigned"] if ds else "unassigned"
    teams = {d: O.person_teams(commits, keys, components, pid, back(cut, d), cut, unassigned)
             for d in sorted({d for _, d in variants.values()})}
    src, test = (re.compile(cfg["checks"][k]) for k in ("source_files", "test_files"))
    touched = {}
    for repo in repos:   # a file's history is per repo: renames are followed inside one
        co = root / "snapshots" / run_id / "repos" / repo.replace("/", "__")
        skip = O.ignore_revs(co, json.loads(run["repo_shas"])[repo], cfg["ownership"]["ignore_revs_file"]) if co.exists() else set()
        mine = lambda r: r["repo"] == repo
        for path, cs in O.touches([c for c in commits if mine(c)], [f for f in files if mine(f)], skip).items():
            touched[path if len(repos) == 1 else f"{repo}:{path}"] = cs
    rows = []
    for path, cs in sorted(touched.items()):
        ts = [(c["authored_at"], pid[c["author_email"]]) for c in cs if pid[c["author_email"]] not in bots]
        if not src.search(path) or test.search(path):
            continue
        before = [x for x in ts if x[0] <= cut]
        after = [x for x in ts if x[0] > cut]
        if not before or not after:
            continue
        window = [x for x in before if back(cut) < x[0]]
        row = {"path": path, "module": module(path)}
        for name, (share, d) in variants.items():
            team_of = lambda p: teams[d].get(p, unassigned)
            row[f"owner|{name}"] = O.team_owner(window, team_of, unassigned, share)[0]
            row[f"next|{name}"], row[f"last|{name}"] = team_of(after[0][1]), team_of(before[-1][1])
        rows.append(row)
    return pd.DataFrame(rows), unassigned


def frame(metrics, y_true, y_pred, by):
    """MetricFrame -> {overall, by_group: {slice: {metric: value}}}, rounded, ready for JSON."""
    mf = MetricFrame(metrics=metrics, y_true=y_true, y_pred=y_pred, sensitive_features=by)
    by_group = {str(k): {m: round(float(v), 4) for m, v in row.items()} for k, row in mf.by_group.iterrows()}
    return {"overall": {m: round(float(v), 4) for m, v in mf.overall.items()}, "by_group": by_group}


def flag(result, metric, min_n, max_gap):
    """Slices with n >= min_n whose metric is more than max_gap below overall, worst first."""
    base = result["overall"][metric]
    low = [(g, m[metric], int(m["n"])) for g, m in result["by_group"].items()
           if m["n"] >= min_n and m[metric] < base - max_gap]
    return [{"slice": g, metric: v, "n": n, "gap": round(base - v, 4)} for g, v, n in sorted(low, key=lambda x: x[1])]


def owner_report(df, unassigned, variants, b):
    """The owner rule as configured (the first variant) by slice, and every variant scored as a mitigation."""
    acc = {"accuracy": accuracy_score, "n": count}
    base = next(iter(variants))
    owner, nxt = df[f"owner|{base}"], df[f"next|{base}"]
    report = {}
    owned = owner != "unowned"
    for by, feature in (("module", df["module"]), ("next_author_team", nxt)):
        r = frame(acc, nxt[owned], owner[owned], feature[owned])
        report[f"accuracy_by_{by}"] = r | {"flagged": flag(r, "accuracy", b["min_n"], b["max_gap"])}
    r = frame({"owned_share": selection_rate, "n": count}, owned.astype(int), owned.astype(int), df["module"])
    report["owned_share_by_module"] = r | {"flagged": flag(r, "owned_share", b["min_n"], b["max_gap"])}

    preds = {name: (df[f"owner|{name}"], df[f"next|{name}"]) for name in variants}
    preds["fallback to last toucher"] = (owner.where(owned, df[f"last|{base}"].replace(unassigned, "unowned")), nxt)
    report["mitigations"] = {}
    for name, (pred, truth) in preds.items():
        kept = pred != "unowned"
        r = frame(acc, truth[kept], pred[kept], df.loc[kept, "module"])
        gaps = flag(r, "accuracy", b["min_n"], 0)
        report["mitigations"][name] = {
            "files_owned": int(kept.sum()), "of": len(df), "accuracy": r["overall"]["accuracy"],
            "next_author_unassigned": round(float((truth[kept] == unassigned).mean()), 4) if kept.any() else 0.0,
            "flagged_modules": len(flag(r, "accuracy", b["min_n"], b["max_gap"])),
            "worst_module_gap": max([f["gap"] for f in gaps] or [0])}
    return report


def mapping_report(out, b):
    """Share of Spark tickets with a code target, by component, issue type and the year it was filed (old tickets'
    files are often gone by the pinned SHA, so the year shows whether a component's gap is just its age)."""
    mapped = {r["key"] for r in stream(out / "ticket_map.jsonl", lambda r: r["target_kind"] != "unmapped")}
    rows = [{"component": (json.loads(t["components"]) or ["(none)"])[0], "type": t["effective_type"],
             "year": t["created_at"][:4], "mapped": int(t["key"] in mapped)}
            for t in stream(out / "ticket.jsonl", lambda t: t["tracker"] == "jira")]
    df = pd.DataFrame(rows)
    report = {}
    for by in ("component", "type", "year"):
        r = frame({"mapped_share": selection_rate, "n": count}, df["mapped"], df["mapped"], df[by])
        report[f"mapped_share_by_{by}"] = r | {"flagged": flag(r, "mapped_share", b["min_n"], b["max_gap"])}
    if since := b.get("recent_since"):   # the same slices on recent tickets only: what's left once age is held fixed
        recent = df[df["year"] >= since]
        r = frame({"mapped_share": selection_rate, "n": count}, recent["mapped"], recent["mapped"], recent["component"])
        report[f"mapped_share_by_component_since_{since}"] = r | {"flagged": flag(r, "mapped_share", b["min_n"], b["max_gap"])}
    return report


def resolver_report(cfg, out, b):
    """Share of edit-role candidates the join kept as call sites (not dropped as look-alikes), by repo."""
    roles = set(cfg["scanner"]["edit_roles"])
    det = {(r["repo"], r["path"], r["line"], r["col"]): r["detection"] for r in stream(out / "resolution.jsonl")}
    rows = [{"repo": c["repo"], "kept": int(det.get((c["repo"], c["path"], c["line"], c["col"])) != "dropped")}
            for c in stream(out / "candidate.jsonl", lambda c: c["role"] in roles)]
    df = pd.DataFrame(rows)
    r = frame({"kept_share": selection_rate, "n": count}, df["kept"], df["kept"], df["repo"])
    return {"kept_share_by_repo": r | {"flagged": flag(r, "kept_share", b["min_n"], b["max_gap"])}}


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
    df, unassigned = owner_rows(cfg, out, root, run_id, variants)
    if len(df):
        report["owner_rule"] = owner_report(df, unassigned, variants, b)
    report["ticket_mapping"] = mapping_report(out, b)
    report["resolver"] = resolver_report(cfg, out, b)
    (out / "bias.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    for part in ("owner_rule", "ticket_mapping", "resolver"):
        for name, r in sorted(report.get(part, {}).items()):
            if "overall" not in r:
                continue
            metric = next(m for m in r["overall"] if m != "n")
            log(f"{part + ' ' + name:45} overall {r['overall'][metric]:.3f} (N={int(r['overall']['n']):,}) · "
                f"{len(r['by_group'])} slices · {len(r['flagged'])} flagged" +
                "".join(f" · {f['slice']} {f[metric]:.3f} (N={f['n']:,})" for f in r["flagged"][:3]))
    for name, m in report.get("owner_rule", {}).get("mitigations", {}).items():
        log(f"  owner rule, {name:38} owns {m['files_owned']:,} of {m['of']:,} files · accuracy {m['accuracy']:.3f} · "
            f"next author unassigned {m['next_author_unassigned']:.1%} · {m['flagged_modules']} modules flagged · "
            f"worst module gap {m['worst_module_gap']:.3f}")
    log(f"-> {out / 'bias.json'}")
    return report


if __name__ == "__main__":
    a = parser(__doc__).parse_args()
    main(a.run_id, a.config)
