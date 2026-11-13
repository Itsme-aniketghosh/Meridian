"""The diff drafter's model comparison (GAPS.md gap 2): each model alone on the package lines the codemod refuses.

    MERIDIAN_GCP_PROJECT=<project> python -m meridian.diffs_compare --run-id c5118b019d642a9b \\
        --models gemini-flash,gemini-pro,llama-4-maverick,llama-4-scout,gemma-4

The LLM path (meridian.diffs, decision 12 and 04 §6) tries only a line the codemod refused, with 2 or more same-shape
examples, never inside `except`, and never one whose facts name a person. On the team run that's 12 lines, 11 in
django-guardian and 1 in debug-toolbar (`--url-style path_if_plain`, the guess the bars are scored with). Each model
runs alone (ladder = that model), so every line is one call to it. Answers go through llm_cache/, so a rerun with the
committed cache makes 0 calls and gives the same table.

Per model:
    tried       lines it was asked about
    drafted     answers that passed every check: JSON, the fact gate on what the line adds, one line, parses, resolves
    matched     drafted lines equal to the real after-line at the repo's migration commit (whitespace ignored)
    pass_rate   matched / tried, the score the rule reads (GAPS.md: "match with the real after-line, and it applies,
                parses and resolves")
    usd_per_match   what its paid calls cost, over the lines it matched
    p95_ms      95th percentile latency of its paid calls

The rule, written before the run (GAPS.md, the model comparison): the cheapest model within 2 points of the best pass
rate wins. Equal results go to the open-weights model. The bar stays 90% (PASS-BARS §2.5). A model the project can't
reach (not enabled in Model Garden, say) is reported as unreachable and never scored, as jev_compare does.
"""
import argparse
import json
import math
import sys
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from meridian import diffs as D
from meridian.common import CONFIG, data_root, git, handoff, load_config, repo_checkout

WITHIN = 2.0   # points of pass rate
OPEN_WEIGHTS = {"llama-4-maverick", "llama-4-scout", "gemma-4", "mistral-medium", "phi4-mini"}
REPOS = {"django-guardian/django-guardian", "jazzband/django-debug-toolbar"}   # where the refused lines are


def p95(xs):
    xs = sorted(xs)
    return xs[max(0, math.ceil(0.95 * len(xs)) - 1)] if xs else None


def pick(table):
    """The model the rule picks from {model: row}: within WITHIN points of the best pass rate, the cheapest per
    match (a model that matched nothing costs infinity), ties to the open-weights model, then by name."""
    table = {m: r for m, r in table.items() if "error" not in r}
    if not table:
        return None
    best = max(r["pass_rate"] for r in table.values())
    near = [m for m, r in table.items() if r["pass_rate"] >= best - WITHIN]

    def cost(m):
        u = table[m]["usd_per_match"]
        return math.inf if u is None else u
    return min(near, key=lambda m: (cost(m), m not in OPEN_WEIGHTS, m))


def row(model, calls, matched):
    tried = len({c["facts_hash"] for c in calls})
    paid = [c for c in calls if not c["cache_hit"]]
    usd = sum(c["cost_usd"] for c in paid)
    return {"tried": tried, "drafted": sum(c["source"] == "llm" for c in calls), "matched": matched,
            "pass_rate": round(100.0 * matched / tried, 1) if tried else 0.0,
            "usd": round(usd, 5), "usd_per_match": round(usd / matched, 5) if matched else None,
            "p95_ms": p95([c["latency_ms"] for c in paid if c["latency_ms"] is not None]),
            "calls": len(calls), "paid_calls": len(paid)}


def compare(run_id, cfg, models, llm, cache, root=None):
    """{model: row} on the team run's refused lines, scored against each repo's migration commit."""
    root = Path(root or data_root())
    snap = root / "snapshots" / run_id
    child = {}
    for d in cfg["datasets"].values():
        child.update({r["repo"]: r["ref"][:-2] for r in d.get("repos", []) if r["ref"].endswith("^1")})
    sites = D.present_sites(handoff(cfg, run_id, root), run_id)
    tree = tempfile.mkdtemp()
    out = {}
    for model in models:
        calls = []

        def log(**c):
            calls.append(c)
        try:
            with redirect_stdout(StringIO()):
                drafts, _, _, rows = D.run(run_id, cfg, root=root, out_dir=False, style="path_if_plain",
                                           cache=tree, repos=REPOS, llm=llm, llm_cache=cache, log_call=log,
                                           ladder=(model,))
        except Exception as e:      # a model the project can't reach is reported, never scored (as jev_compare)
            out[model] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            continue
        matched = 0
        for repo in sorted(REPOS):
            llm_lines = {(r["target"]) for r in rows if r["source"] == "llm" and r["model"] == model}
            ss = [s for s in sites if s["repo"] == repo and s["call_site_id"] in llm_lines]
            if not ss:
                continue
            rd = repo_checkout(snap, repo)
            m, _, _ = D.score(rd, git(rd, "rev-parse", "HEAD").strip(), child[repo], ss, drafts.get(repo, {}))
            matched += m
        out[model] = row(model, calls, matched)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--models", required=True, help="comma-separated meridian.llm.MODELS names")
    ap.add_argument("--cache-dir", type=Path, help="the LLM cache (default: Data-Pipeline/llm_cache/)")
    ap.add_argument("--llm", action="store_true", help="call models live; without it, only cached answers count")
    ap.add_argument("--out", type=Path, help="write the table and the pick as JSON here")
    a = ap.parse_args(argv)
    from meridian.llm_cache import Cache
    cache = Cache(a.cache_dir) if a.cache_dir else Cache()
    llm = None
    if a.llm:
        from meridian.llm import Client
        llm = Client()
    t0 = time.time()
    table = compare(a.run_id, load_config(a.config), [m.strip() for m in a.models.split(",") if m.strip()], llm, cache)
    chosen = pick(table)
    print(f"{'model':18} {'tried':>5} {'drafted':>7} {'matched':>7} {'pass %':>6} {'usd':>8} {'usd/match':>9} "
          f"{'p95 ms':>7}")
    for m, r in table.items():
        if "error" in r:
            print(f"{m:18} unreachable: {r['error'][:90]}")
            continue
        print(f"{m:18} {r['tried']:>5} {r['drafted']:>7} {r['matched']:>7} {r['pass_rate']:>6} {r['usd']:>8} "
              f"{str(r['usd_per_match']):>9} {str(r['p95_ms']):>7}")
    print(f"pick (cheapest within {WITHIN} points of the best pass rate): {chosen}   [{time.time() - t0:.0f} s]")
    if a.out:
        a.out.write_text(json.dumps({"table": table, "pick": chosen, "within": WITHIN}, indent=1, sort_keys=True)
                         + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
