"""P9's precision on real pushes (GAPS.md gap 5, decision 15c, Rachna): 2023 Spark commits replayed through the push
check, each P9 alert scored against the commit's SZZ label.

    python -m meridian.p9_precision --run-id <run_id>           # from Data-Pipeline/

The 21 migration commits can't measure precision: their repos have no bug labels, and they give 7 alerts. 2023's
Spark commits have both: the c5118 feature table (Model-Pipeline/data/features.parquet) labels each one (did a later
fix blame it?). The rule, written in decisions.md before this ran:

- the pushes: a seeded draw of val (2023) commits, OVER of them with lines x files above [push_check]
  defect_risk_threshold (P9 should alert) and UNDER below it (P9 should stay quiet), each replayed as parent...commit
  through the real push check against the run's block, with settings.toml's limits
- P9's precision: of the checked pushes P9 alerted on, the share whose commit is labelled bug-inducing, with a
  Wilson interval. Meets the bar (PASS-BARS, 05-test) only if >= 30 alerts and precision >= 0.80
- for comparison, the table's own number: precision of lines x files > threshold over all val commits
- and a check: on checked pushes, P9 alerts exactly when the table's lines x files is over the threshold. A
  disagreement is reported with its commit (the push's numstat and the table's la, ld, nf should be the same diff)

A push over the scope limit is skipped by the push check, so P9 never sees it: skips are counted, and they're the
biggest commits. Every finding is written as a push_alert row (replay.py), so /push shows real Spark pushes.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from meridian import replay as RP
from meridian.common import CONFIG, HERE, load_config, load_settings

ROOT = HERE.parent
FEATURES = ROOT / "Model-Pipeline" / "data" / "features.parquet"
REPORT = ROOT / "Model-Pipeline" / "reports" / "p9_precision.json"
REPO = "apache/spark"
OVER, UNDER, SEED = 45, 15, 1     # 45 over: >= 30 alerts even if a third are skipped
MIN_ALERTS, BAR = 30, 0.80


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    c = (k + z * z / 2) / (n + z * z)
    h = z * math.sqrt(n) / (n + z * z) * math.sqrt(p * (1 - p) + z * z / (4 * n))
    return [max(c - h, 0.0), min(c + h, 1.0)]


def val_rows(features):
    df = pd.read_parquet(features) if str(features).endswith(".parquet") else pd.read_csv(features)
    return val_rows_from(df)


def val_rows_from(df):
    v = df[df["split"] == "val"][["sha", "label", "la", "ld", "nf"]].copy()
    v["lxf"] = (v["la"] + v["ld"]) * v["nf"]
    return v.reset_index(drop=True)


def table_precision(v, threshold):
    flag = v["lxf"] > threshold
    k, n = int(v.loc[flag, "label"].sum()), int(flag.sum())
    return {"alerts": n, "true": k, "precision": k / n if n else None, "interval": wilson(k, n), "of": int(len(v))}


def draw(v, threshold, over=OVER, under=UNDER, seed=SEED):
    rng = np.random.default_rng(seed)
    hi, lo = v[v["lxf"] > threshold], v[v["lxf"] <= threshold]
    pick = lambda d, n: d.iloc[np.sort(rng.choice(len(d), size=min(n, len(d)), replace=False))]
    return pd.concat([pick(hi, over), pick(lo, under)]).reset_index(drop=True)


def score(sample, results, threshold):
    """Per checked push: did P9 alert, its label, and whether P9 agrees with the table's lines x files."""
    by_sha = {r["sha"]: r for r in sample.to_dict("records")}
    pushes, alerts, disagree = [], [], []
    for _, sha, status, rows in results:
        r = by_sha.get(sha)
        if r is None or status != "checked":
            pushes.append({"sha": sha, "status": status})
            continue
        p9 = any(x["check_id"] == "P9" for x in rows)
        expect = r["lxf"] > threshold
        pushes.append({"sha": sha, "status": status, "p9": p9, "label": int(r["label"]), "lxf": int(r["lxf"])})
        if p9:
            alerts.append(int(r["label"]))
        if p9 != expect:
            disagree.append({"sha": sha, "p9": p9, "table_lxf": int(r["lxf"])})
    k, n = sum(alerts), len(alerts)
    return {"pushes": len(results), "checked": sum(p["status"] == "checked" for p in pushes),
            "skipped": sum(p["status"] == "skipped" for p in pushes),
            "missing": sum(p["status"] == "missing" for p in pushes), "alerts": n, "true": k,
            "precision": k / n if n else None, "interval": wilson(k, n),
            "meets_bar": n >= MIN_ALERTS and n and k / n >= BAR, "disagree_with_table": disagree, "per_push": pushes}


def main(argv=None, check=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--mirrors", type=Path, default=RP.MIRRORS)
    ap.add_argument("--features", type=Path, default=FEATURES)
    ap.add_argument("--over", type=int, default=OVER)
    ap.add_argument("--under", type=int, default=UNDER)
    ap.add_argument("--out", type=Path, default=REPORT)
    ap.add_argument("--apply", action="store_true", help="also put the push_alert rows into the block")
    a = ap.parse_args(argv)
    db = a.db or RP.OUT / a.run_id / "meridian.sqlite"
    if not db.is_file():
        raise SystemExit(f"{db} not found")
    cfg, settings = load_config(CONFIG), load_settings("push_check")
    threshold = settings["defect_risk_threshold"]
    v = val_rows(a.features)
    sample = draw(v, threshold, a.over, a.under)
    commits = [(REPO, (f"{s}^1", s)) for s in sample["sha"]]
    results = RP.replay(db, a.run_id, a.mirrors, cfg, settings, commits=commits, check=check)
    real = score(sample, results, threshold)
    table = table_precision(v, threshold)
    new = [row for *_, rows in results for row in rows]
    replayed = {sha for _, sha, _, _ in results if sha}
    RP.merge(db.parent / "push_alert.jsonl", new, replayed)
    if a.apply:
        RP.apply(db, new, replayed)
    drawn = {"over": int((sample["lxf"] > threshold).sum()), "under": int((sample["lxf"] <= threshold).sum())}
    report = {"decision": "rachna-15c", "repo": REPO, "split": "val", "threshold": threshold, "seed": SEED,
              "drawn": drawn,
              "real_pushes": real, "table_all_val": table, "bar": {"min_alerts": MIN_ALERTS, "precision": BAR}}
    a.out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"{real['pushes']} Spark val pushes replayed: {real['checked']} checked, {real['skipped']} skipped over the "
          f"scope limit, {real['missing']} with no mirror")
    if real["alerts"]:
        print(f"  P9 on real pushes: precision {real['precision']:.3f} on {real['alerts']} alerts "
              f"(Wilson {real['interval'][0]:.2f}-{real['interval'][1]:.2f}): "
              + ("meets the 0.80 bar" if real["meets_bar"] else "under the bar (needs >= 30 alerts at >= 0.80)"))
    t = table
    print(f"  the table, all {t['of']:,} val commits: lines x files > {threshold} flags {t['alerts']}, precision "
          f"{t['precision']:.3f} (Wilson {t['interval'][0]:.2f}-{t['interval'][1]:.2f})")
    print(f"  P9 vs the table's lines x files on checked pushes: {len(real['disagree_with_table'])} disagreement(s)")
    print(f"wrote {a.out} and {db.parent / 'push_alert.jsonl'}" + (f"; rows into {db}" if a.apply else ""))
    return report


if __name__ == "__main__":
    main()
