"""The writer's model comparison (GAPS.md gap 2): per model, from the calls agents.py logged with --calls.

    python -m meridian.agents --run-id R --llm --models gemini-flash --calls out/R/writer_calls.jsonl --quiet
    python -m meridian.writer_compare out/R/writer_calls.jsonl            # the table, and the pick

Each model runs alone (--models <one>), so every task is one call to it and the numbers are that model's own:
    tasks          answers it gave (a cache hit counts: the answer is the same, only its first cost was paid)
    pass_rate      answers the fact gate passed
    valid_json     answers that parsed as {"text", "used"} (gate_pass not None)
    usd_per_text   what its paid calls cost, over the texts it got through the gate
    p95_ms         95th percentile latency of its paid calls (cache hits take no time and are left out)

The rule, written before the run (GAPS.md, the model comparison): the cheapest model (usd_per_text) within 2 points of
the best pass rate wins. Equal results go to the open-weights model.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

WITHIN = 2.0  # points of pass rate
OPEN_WEIGHTS = {"llama-4-maverick", "llama-4-scout", "gemma-4", "mistral-medium", "phi4-mini"}


def p95(xs):
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)] if xs else None


def table(rows):
    by = defaultdict(list)
    for r in rows:
        if r.get("agent", "writer") == "writer":
            by[r["model"]].append(r)
    out = {}
    for model, rs in sorted(by.items()):
        paid = [r for r in rs if not r["cache_hit"]]
        passed = sum(r["gate_pass"] is True for r in rs)
        usd = sum(r["cost_usd"] for r in paid)
        out[model] = {"tasks": len(rs), "paid_calls": len(paid), "passed": passed,
                      "pass_rate": round(100 * passed / len(rs), 1),
                      "valid_json": round(100 * sum(r["gate_pass"] is not None for r in rs) / len(rs), 1),
                      "usd": round(usd, 6), "usd_per_text": round(usd / passed, 6) if passed else None,
                      "p95_ms": p95([r["latency_ms"] for r in paid if r.get("latency_ms") is not None])}
    return out


def pick(t):
    """The winner by the written rule, or None when no model passed anything."""
    ok = {m: r for m, r in t.items() if r["usd_per_text"] is not None}
    if not ok:
        return None
    best = max(r["pass_rate"] for r in ok.values())
    near = [m for m, r in ok.items() if r["pass_rate"] >= best - WITHIN]
    return min(near, key=lambda m: (ok[m]["usd_per_text"], m not in OPEN_WEIGHTS, m))


def read(paths):
    rows = []
    for p in paths:
        rows += [json.loads(line) for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("calls", nargs="+", type=Path, help="the --calls jsonl files")
    ap.add_argument("--json", type=Path, help="also write the table and the pick here")
    a = ap.parse_args(argv)
    t = table(read(a.calls))
    cols = ("tasks", "paid_calls", "pass_rate", "valid_json", "usd", "usd_per_text", "p95_ms")
    print(f"{'model':20}" + "".join(f"{c:>13}" for c in cols))
    for m, r in t.items():
        print(f"{m:20}" + "".join(f"{'-' if r[c] is None else r[c]!s:>13}" for c in cols))
    w = pick(t)
    print(f"\npick: {w or 'none (no model got a text through the gate)'}")
    if a.json:
        a.json.write_text(json.dumps({"models": t, "pick": w, "rule": f"cheapest within {WITHIN} points"},
                                     indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
