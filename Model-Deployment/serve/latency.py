"""S2's latency bars against the deployed service: cold start <= 15 s, p95 <= 2 s warm (PLAN §6).

    python -m serve.latency --url https://meridian-risk-<hash>-<region>.run.app            # from Model-Deployment/
    python -m serve.latency --url <url> --cold-only      # after >= 15 min idle, so the service has scaled to zero

First call: /health, timed from the request to the reply. If the service had scaled to zero (min_instances = 0), that
is the cold start. Then --n warm /score calls, half known SHAs (the feature table's, seeded) and half pushes, one at
a time, and their p50, p95 and max. Writes reports/serve_latency.json (appending one run per call) and exits 1 if a
bar is missed. Standard library only: it runs anywhere.
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "serve_latency.json"
COLD_BAR_S, P95_BAR_S = 15.0, 2.0
SMOKE_SHA = "3d819389819557523f373c192f88a594b665734d"   # deploy.toml [smoke]
SMOKE_RISK = 0.4946872061692944                             # its judged val score, the champion's
PUSHES = [
    {"numstat": [[40, 5, "core/src/main/scala/org/apache/spark/SparkContext.scala"]], "subject": "[SPARK-12345] Fix"},
    {"numstat": [[3, 1, "python/pyspark/sql/functions.py"], [12, 0, "python/pyspark/sql/tests/test_functions.py"]],
     "subject": "[MINOR] Tidy a docstring"},
    {"numstat": [[220, 90, "sql/core/src/main/scala/org/apache/spark/sql/execution/SparkPlan.scala"],
                 [150, 40, "sql/core/src/test/scala/org/apache/spark/sql/execution/PlannerSuite.scala"],
                 [None, None, "docs/img/plan.png"]], "subject": "[SPARK-40000][SQL] Rework planning"},
]


def call(url, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, text = r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode("utf-8")
    return status, text, time.perf_counter() - t0


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))]


def shas(n, seed=1):
    try:                                                    # the repo's feature table, if this runs from the repo
        import pandas as pd
        table = pd.read_parquet(Path(__file__).resolve().parents[2] / "Model-Pipeline" / "data" / "features.parquet")
        return random.Random(seed).sample(sorted(table["sha"]), n)
    except Exception:
        return [SMOKE_SHA] * n


def run(url, n=200, cold_only=False):
    status, text, cold = call(url, "/health")
    if status != 200:
        raise SystemExit(f"/health answered {status}: {text[:200]}")
    model = json.loads(text)["model"]
    out = {"url": url, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "model": model,
           "first_call_s": round(cold, 3), "cold_bar_s": COLD_BAR_S}
    if not cold_only:
        status, text, _ = call(url, "/score", {"repo": "apache/spark", "sha": SMOKE_SHA})
        if status != 200 or abs(json.loads(text)["risk"] - SMOKE_RISK) > 1e-9:
            raise SystemExit(f"the smoke SHA didn't score as judged: {status} {text[:200]}")
        times, kinds, errors = [], {"sha": [], "push": []}, 0
        for i, sha in enumerate(shas(n // 2)):
            for kind, body in (("sha", {"repo": "apache/spark", "sha": sha}),
                               ("push", {"repo": "apache/spark", **PUSHES[i % len(PUSHES)]})):
                status, _, s = call(url, "/score", body)
                errors += status != 200
                times.append(s)
                kinds[kind].append(s)
        out.update(warm_calls=len(times), errors=errors, p50_s=round(pct(times, 0.5), 3),
                   p95_s=round(pct(times, 0.95), 3), max_s=round(max(times), 3), p95_bar_s=P95_BAR_S,
                   by_kind={k: {"p50_s": round(pct(v, 0.5), 3), "p95_s": round(pct(v, 0.95), 3)}
                            for k, v in kinds.items() if v})
    out["passed"] = cold <= COLD_BAR_S and (cold_only or (out["p95_s"] <= P95_BAR_S and out["errors"] == 0))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="the meridian-risk service URL")
    ap.add_argument("--n", type=int, default=200, help="warm /score calls (half SHAs, half pushes)")
    ap.add_argument("--cold-only", action="store_true", help="time the first call only (run after >= 15 min idle)")
    a = ap.parse_args(argv)
    r = run(a.url, a.n, a.cold_only)
    runs = json.loads(REPORT.read_text())["runs"] if REPORT.is_file() else []
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"bars": {"cold_start_s": COLD_BAR_S, "p95_warm_s": P95_BAR_S},
                                  "runs": runs + [r]}, indent=1) + "\n", encoding="utf-8")
    line = f"first call {r['first_call_s']:.2f} s (bar {COLD_BAR_S:.0f} s if it was cold)"
    if not a.cold_only:
        line += (f"; {r['warm_calls']} warm calls, {r['errors']} errors: p50 {r['p50_s'] * 1000:.0f} ms, "
                 f"p95 {r['p95_s'] * 1000:.0f} ms (bar {P95_BAR_S * 1000:.0f} ms), max {r['max_s'] * 1000:.0f} ms")
    print(("PASS: " if r["passed"] else "MISS: ") + line + f"; {REPORT}")
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
