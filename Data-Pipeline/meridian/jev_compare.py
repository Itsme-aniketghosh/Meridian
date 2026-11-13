"""Jev's model comparison (decisions 13, 14, 21; GAPS.md gap 2, Rachna): which model should route, if any.

    python -m meridian.jev_compare --run-id <run_id>                         # the default models below
    python -m meridian.jev_compare --run-id <run_id> --models phi4-mini gemma-4

Each model plays Jev (meridian.jev.Router) on the 30 held-out tasks of decision 19's set (labels/jev_tasks.csv,
labelled by hand before any run), and its pick is scored against the person's: the rung it starts the writer on
(small, mid, large on agents.LADDER) against the label. The rules play too: they always start small, for $0. Each model
is asked twice, the second time from the cache, so "same problem twice, same pick" is checked, not assumed. Jev's
answers go through the committed LLM cache (llm_cache/), so a rerun costs $0.

The rules, written in decisions.md before this ran (13, 14 and 21 "as run": the hosted models the originals named
aren't on the project's GCP credits):
- 21, local or hosted: Phi-4-mini on this laptop is Jev's model only if it gets within 3 of the 30 tasks of the best
  hosted model and its p95 latency per pick is <= 10 s
- 14, which hosted model: the cheapest (Gemma 4) unless another gets >= 3 more of the 30 right
- 13, Jev or rules: Jev stays only if the chosen model gets more of the 30 right than the rules do, and costs no more
  per pick than the rules ($0). Otherwise the rules route, and the report says so
Counts per model: right (pick = label), refused (Jev's answer failed a check: the rules picked), and how its wrong
picks lean (too big, too small). Never reads the prompt tasks: they're for writing Jev's prompt.
"""
import argparse
import json
import math
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from meridian import agents as G
from meridian import jev as J
from meridian.common import HERE
from meridian.jev_tasks import LABEL_CHOICES, LABELS, read_labels
from meridian.llm_cache import Cache

ROOT = HERE.parent
DEFAULT_MODELS = ("phi4-mini", "gemma-4", "llama-4-scout", "llama-4-maverick", "gemini-flash")
LOCAL = "phi4-mini"
HOSTED_BY_PRICE = ("gemma-4", "llama-4-scout", "llama-4-maverick", "gemini-flash", "gemini-pro")  # cheapest first
WITHIN_LOCAL, MORE_HOSTED, P95_LOCAL_S = 3, 3, 10.0


def load_tasks(folder=LABELS, split="held_out"):
    """[(Task, label)] for `split`, the label the person gave it."""
    labels = read_labels(Path(folder) / "jev_tasks.csv")
    facts = json.loads((Path(folder) / "jev_tasks_facts.json").read_text(encoding="utf-8"))
    out = []
    for f in facts:
        if f["split"] != split:
            continue
        task = G.Task(kind=f["kind"], target_kind=f["target_kind"], target=f["target"], owner_team=f["owner_team"],
                      repo=f["repo"], facts=f["facts"], template="")
        out.append((task, labels[f["task_id"]][1]))
    return out


def rung_label(pick):
    """small, mid or large: the rung a pick starts on."""
    return LABEL_CHOICES[min(pick.rung, len(LABEL_CHOICES) - 1)] if pick.model else "small"


def p95(xs):
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)] if xs else None


class Timed:
    """Wraps llm(model, prompt) to keep each paid call's latency."""

    def __init__(self, llm):
        self.llm, self.ms = llm, []

    def __call__(self, model, prompt):
        t0 = time.perf_counter()
        r = self.llm(model, prompt)
        self.ms.append((time.perf_counter() - t0) * 1000)
        return r


def score(model, tasks, llm, cache, teams, people):
    """One router model on the tasks, asked twice (the second from the cache)."""
    timed = Timed(llm)
    router = J.Router(llm=timed, cache=cache, teams=teams, people=people, model=model, spend_cap_usd=1e9,
                      top_budget=10 ** 6)
    first = [router.pick(t) for t, _ in tasks]
    down = next((p.why for p in first if p.why.startswith("jev down")), None)
    if down:  # the router falls back to the rules when its model can't be reached; a comparison says so instead
        raise RuntimeError(f"{model} couldn't be reached ({down})")
    spent, calls = router.spent, router.calls
    again = [J.Router(cache=cache, teams=teams, people=people, model=model, top_budget=10 ** 6).pick(t)
             for t, _ in tasks]
    preds = [rung_label(p) for p in first]
    labels = [lab for _, lab in tasks]
    lean = Counter("right" if p == lab else ("too big" if LABEL_CHOICES.index(p) > LABEL_CHOICES.index(lab)
                                             else "too small") for p, lab in zip(preds, labels))
    return {"model": model, "tasks": len(tasks), "right": sum(p == lab for p, lab in zip(preds, labels)),
            "refused": sum(p.route == "rules" for p in first), "why_refused": dict(Counter(
                p.why for p in first if p.route == "rules")), "lean": dict(lean), "paid_calls": calls,
            "usd": round(spent, 6), "usd_per_pick": round(spent / len(tasks), 6) if tasks else None,
            "p95_s": round(p95(timed.ms) / 1000, 2) if timed.ms else None,
            "same_twice": [(p.model, p.route) for p in first] == [(p.model, p.route) for p in again],
            "picks": preds}


def rules_row(tasks):
    preds = [rung_label(J.rules_pick(t.kind)) for t, _ in tasks]
    labels = [lab for _, lab in tasks]
    return {"model": "rules", "tasks": len(tasks), "right": sum(p == lab for p, lab in zip(preds, labels)),
            "refused": 0, "usd": 0.0, "usd_per_pick": 0.0, "p95_s": 0.0, "same_twice": True, "picks": preds}


def decide(rows, rules):
    """Decisions 21, 14 and 13 by the rules written before the run. rows: {model: row} that ran."""
    hosted = [m for m in HOSTED_BY_PRICE if m in rows]
    out = {}
    if hosted:
        cheapest = hosted[0]
        better = [m for m in hosted[1:] if rows[m]["right"] >= rows[cheapest]["right"] + MORE_HOSTED]
        pick14 = max(better, key=lambda m: (rows[m]["right"], -hosted.index(m))) if better else cheapest
        out["14"] = {"picked": pick14, "rule": f"the cheapest hosted ({cheapest}) unless another gets >= "
                     f"{MORE_HOSTED} more right"}
    best_hosted = max((rows[m]["right"] for m in hosted), default=None)
    if LOCAL in rows:
        r = rows[LOCAL]
        ok = (best_hosted is None or r["right"] >= best_hosted - WITHIN_LOCAL) and (r["p95_s"] or 0) <= P95_LOCAL_S
        out["21"] = {"picked": LOCAL if ok else out.get("14", {}).get("picked"),
                     "rule": f"local if within {WITHIN_LOCAL} of the best hosted ({best_hosted}) and p95 <= "
                             f"{P95_LOCAL_S:.0f} s"}
    jev_model = out.get("21", out.get("14", {})).get("picked")
    if jev_model:
        j = rows[jev_model]
        stays = j["right"] > rules["right"] and (j["usd_per_pick"] or 0) <= rules["usd_per_pick"]
        out["13"] = {"picked": f"jev ({jev_model})" if stays else "rules", "jev_model": jev_model,
                     "rule": "Jev only if more right than the rules and no more cost per pick ($0)"}
    return out


def names(db_path, tasks):
    """(teams, people) for the gate: from the block when there is one, else the tasks' own teams and no people."""
    if db_path and Path(db_path).is_file():
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            return G.names(db)
    return sorted({t.owner_team for t, _ in tasks if t.owner_team}), []


def main(argv=None, llm=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", help="the block the tasks came from, for the gate's names")
    ap.add_argument("--db", type=Path)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    ap.add_argument("--cache", type=Path, help="default: the committed llm_cache/")
    ap.add_argument("--out", type=Path, help="default: <labels>/jev_compare.json")
    a = ap.parse_args(argv)
    tasks = load_tasks(a.labels)
    db = a.db or (ROOT / "out" / a.run_id / "meridian.sqlite" if a.run_id else None)
    teams, people = names(db, tasks)
    if llm is None:
        from meridian.llm import Client
        llm = Client()
    cache = Cache(a.cache) if a.cache else Cache()
    rules = rules_row(tasks)
    rows = {}
    for m in a.models:
        try:
            rows[m] = score(m, tasks, llm, cache, teams, people)
        except Exception as e:      # a model the project can't reach: say so, compare the rest
            rows[m] = {"model": m, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    ran = {m: r for m, r in rows.items() if "error" not in r}
    verdict = decide(ran, rules)
    labels = Counter(lab for _, lab in tasks)
    print(f"{len(tasks)} held-out tasks, labelled by hand: {dict(labels)}")
    print(f"{'router':18}{'right':>7}{'refused':>9}{'usd':>10}{'p95 s':>8}  same twice  wrong picks")
    for r in [rules, *rows.values()]:
        if "error" in r:
            print(f"{r['model']:18}  not run: {r['error']}")
            continue
        lean = {k: v for k, v in r.get("lean", {}).items() if k != "right"}
        print(f"{r['model']:18}{r['right']:>4}/{r['tasks']:<2}{r['refused']:>9}{r['usd']:>10.4f}"
              f"{(r['p95_s'] or 0):>8.1f}  {str(r['same_twice']):10}  {lean or ''}")
    for d in ("21", "14", "13"):
        if d in verdict:
            print(f"decision {d}: {verdict[d]['picked']}   ({verdict[d]['rule']})")
    out = a.out or a.labels / "jev_compare.json"
    out.write_text(json.dumps({"labels": dict(labels), "rules": rules, "models": rows, "decisions": verdict},
                              indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return {"rules": rules, "models": rows, "decisions": verdict}


if __name__ == "__main__":
    main()
