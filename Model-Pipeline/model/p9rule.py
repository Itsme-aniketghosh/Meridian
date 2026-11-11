"""P9's rule on val (decision 15b, GAPS.md gap 4, Rachna): alert only when the model AND lines × files both flag.

    python -m model.p9rule          # from Model-Pipeline/: reads reports/candidate.json's judged val scores

The model alone tops out at precision 0.70 on 30 val alerts, under the 0.80 bar, so P9 can't use it alone. This asks
whether requiring both signals reaches the bar. The rule, written in decisions.md before this ran:

- candidates: the model's cutoff and lines × files' cutoff, each at the score of the val commit ranked at the top
  1%, 2%, ..., 30% (900 pairs). A commit alerts when its model score >= the model's cutoff AND its lines × files >=
  lines × files' cutoff
- eligible: at least 30 val alerts (05-test: no claim below N = 30)
- the pick: the highest precision; a tie goes to more alerts, then to the larger lines × files cutoff
- adopted only if its precision >= 0.80. Otherwise P9 stays lines × files and the report says the model's best was
  0.70

The pick is the best of 900 on the same rows it's scored on, so its val precision is optimistic. The 2024 run checks
it, and doesn't change it. No refit: the judged scores (S6). Never reads test rows. Writes reports/p9_rule.json, which
the push check reads ([push_check] p9_rule).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parents[1]
REPORTS = HERE / "reports"
TOPS = [i / 100 for i in range(1, 31)]     # 1% ... 30% of val commits
MIN_ALERTS = 30
BAR = 0.80


def cutoff(score, top):
    """The score of the val commit ranked at the top `top` share: alerting at >= it flags about that share."""
    s = np.sort(np.asarray(score, dtype=float))[::-1]
    return float(s[max(int(np.ceil(top * len(s))) - 1, 0)])


def wilson(k, n, z=1.959963984540054):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (k + z * z / 2) / (n + z * z)
    half = z * np.sqrt(n) / (n + z * z) * np.sqrt(p * (1 - p) + z * z / (4 * n))
    return float(max(centre - half, 0)), float(min(centre + half, 1))


def alerts(y, flag):
    k, n = int(y[flag].sum()), int(flag.sum())
    return {"alerts": n, "true": k, "precision": k / n if n else None, "interval": list(wilson(k, n)),
            "recall": k / int(y.sum()) if y.sum() else None}


def best_single(y, score):
    """The best precision one signal reaches alone with >= MIN_ALERTS, over the same cutoffs (for the report)."""
    best = None
    for top in TOPS:
        t = cutoff(score, top)
        r = alerts(y, score >= t) | {"top": top, "cutoff": t}
        if r["alerts"] >= MIN_ALERTS and (best is None or (r["precision"], r["alerts"]) > (best["precision"],
                                                                                           best["alerts"])):
            best = r
    return best


def pick(y, model, lxf):
    """Decision 15b's rule. Returns (the pick or None, every eligible pair)."""
    y, model, lxf = (np.asarray(v) for v in (y, model, lxf))
    cuts_m = {top: cutoff(model, top) for top in TOPS}
    cuts_l = {top: cutoff(lxf, top) for top in TOPS}
    pairs = []
    for tm, cm in cuts_m.items():
        for tl, cl in cuts_l.items():
            r = alerts(y, (model >= cm) & (lxf >= cl))
            if r["alerts"] >= MIN_ALERTS:
                pairs.append(r | {"model_top": tm, "model_cutoff": cm, "lxf_top": tl, "lxf_cutoff": cl})
    if not pairs:
        return None, pairs
    return max(pairs, key=lambda r: (r["precision"], r["alerts"], r["lxf_cutoff"])), pairs


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--candidate", default=str(REPORTS / "candidate.json"))
    p.add_argument("--out", default=str(REPORTS / "p9_rule.json"))
    a = p.parse_args(argv)
    c = json.loads(Path(a.candidate).read_text())
    scores = pd.read_csv(Path(a.candidate).parent / c["scores_file"])
    y = scores["label"].to_numpy(dtype=int)
    model, lxf = scores[c["column"]].to_numpy(dtype=float), scores["lines_x_files"].to_numpy(dtype=float)
    chosen, pairs = pick(y, model, lxf)
    adopted = bool(chosen and chosen["precision"] >= BAR)
    report = {"decision": "rachna-15b", "split": "val", "n": int(len(y)), "positives": int(y.sum()),
              "model": c["kind"], "model_md5": c.get("model_md5"), "rule": "model >= model_cutoff AND lines x files >= "
              "lxf_cutoff; cutoffs at the top 1-30% of val; >= 30 alerts; highest precision; adopt if >= 0.80",
              "pairs_tried": len(TOPS) ** 2, "pairs_eligible": len(pairs), "pick": chosen, "bar": BAR,
              "adopted": adopted, "model_alone": best_single(y, model), "lines_x_files_alone": best_single(y, lxf)}
    Path(a.out).write_text(json.dumps(report, indent=2) + "\n")
    m, lx = report["model_alone"], report["lines_x_files_alone"]
    print(f"val N = {len(y):,}, {int(y.sum())} bug-inducing; {len(pairs)} of {len(TOPS) ** 2} pairs have >= 30 alerts")
    for name, r in (("model alone", m), ("lines x files alone", lx)):
        if r:
            print(f"  {name:20} best precision {r['precision']:.3f} on {r['alerts']} alerts "
                  f"(Wilson {r['interval'][0]:.2f}-{r['interval'][1]:.2f}), top {r['top']:.0%}")
    if chosen:
        print(f"  model AND lines x files: precision {chosen['precision']:.3f} on {chosen['alerts']} alerts "
              f"(Wilson {chosen['interval'][0]:.2f}-{chosen['interval'][1]:.2f}), recall {chosen['recall']:.3f}; "
              f"model >= {chosen['model_cutoff']:.4f} (top {chosen['model_top']:.0%}), lines x files >= "
              f"{chosen['lxf_cutoff']:.0f} (top {chosen['lxf_top']:.0%})")
    print(("ADOPTED: P9 alerts when both flag" if adopted else
           "not adopted: under 0.80, P9 stays lines x files") + f"; wrote {a.out}")
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
