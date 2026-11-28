"""The canary deploy.yml watches before a new revision gets 100% of traffic (S3, Nov 27).

The new revision has passed its smoke test and now takes deploy.toml [canary] percent of the service's traffic; the
revision that served before keeps the rest. For [canary] watch_s seconds this calls POST /score (the [smoke] sha)
about [canary] rps times a second, twice per tick:
- through the service's own URL, as a caller does: the reply's model version says which revision answered, so the
  split is seen working (at least one answer from the new model, when the two models differ)
- through the new revision's tag URL, so the canary's own errors and latency are measured on every tick

The canary fails when its error rate (a non-200, no answer, or a reply from a model that is neither, out of the calls
it answered or may have answered) is over [canary] max_error_rate, when its p95 latency is over [canary] max_p95_ms
(PLAN §4: 5xx under 1%, p95 under 2 s), or when the service URL never reached it. An error the service URL can't
attribute counts against the canary. A failure exits 1 and deploy.yml rolls back: the revision that served before
gets 100% again. Standard library only.

    python Model-Deployment/canary.py --url "$SERVICE_URL" --tag-url "$TAG_URL" --version <new sha> \
        --github-output "$GITHUB_OUTPUT"
"""
import argparse
import math
import sys
import time
import tomllib
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke  # noqa: E402  the same HTTP call and deploy.toml

NEW, OLD, UNKNOWN, OTHER = "new", "old", "unknown", "other"


def p95(values):
    """The nearest-rank 95th percentile, or None for no values."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def sample(caller, url, body, new_version, old_version, timeout):
    """One /score call as (who answered, ok, ms): new, old, other (a model that is neither) or unknown (no model)."""
    try:
        status, reply, seconds = caller(url, "/score", body, timeout=timeout)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return UNKNOWN, False, None
    version = ((reply or {}).get("model") or {}).get("version") if isinstance(reply, dict) else None
    ms = round(seconds * 1000, 1)
    if version is None:
        return UNKNOWN, False, ms
    if version == new_version:
        return NEW, status == 200, ms
    if old_version and version == old_version:
        return OLD, status == 200, ms
    return OTHER, False, ms


def judge(via_service, via_tag, new_version, old_version, max_error_rate, max_p95_ms):
    """(problems, seen) from the samples: every failure in words, and the numbers deploy.yml reports."""
    canary = via_tag + [s for s in via_service if s[0] in (NEW, UNKNOWN, OTHER)]
    errors = sum(1 for who, ok, _ in canary if not ok)
    latencies = [ms for who, ok, ms in canary if ok and ms is not None]
    new_share = sum(1 for s in via_service if s[0] == NEW)
    seen = {"calls": len(via_service) + len(via_tag), "canary_calls": len(canary), "errors": errors,
            "error_rate": round(errors / len(canary), 4) if canary else 0.0,
            "p95_ms": p95(latencies), "new_via_service": new_share, "via_service": len(via_service),
            "old_errors": sum(1 for who, ok, _ in via_service if who == OLD and not ok)}
    problems = []
    if not canary:
        problems.append("no call reached the canary")
    if canary and errors / len(canary) > max_error_rate:
        problems.append(f"error rate {errors}/{len(canary)} = {seen['error_rate']:.2%}, over {max_error_rate:.2%}")
    if seen["p95_ms"] is not None and seen["p95_ms"] > max_p95_ms:
        problems.append(f"p95 latency {seen['p95_ms']} ms, over {max_p95_ms} ms")
    if old_version and old_version != new_version and via_service and not new_share:
        problems.append(f"none of {len(via_service)} calls through the service URL reached the new revision: "
                        "the split isn't serving it")
    others = sum(1 for s in canary if s[0] == OTHER)
    if others:
        problems.append(f"{others} replies named a model that is neither the new one nor the one that served before")
    return problems, seen


def watch(url, tag_url, body, new_version, old_version, seconds, rps, timeout, caller=smoke.call,
          clock=time.monotonic, sleep=time.sleep):
    """(via service, via tag): the samples of seconds of calls, one tick every 1/rps seconds."""
    via_service, via_tag = [], []
    interval = 1.0 / rps
    end = clock() + seconds
    while clock() < end:
        tick = clock()
        via_service.append(sample(caller, url, body, new_version, old_version, timeout))
        via_tag.append(sample(caller, tag_url, body, new_version, old_version, timeout))
        sleep(max(0.0, interval - (clock() - tick)))
    return via_service, via_tag


def served_before(url, caller=smoke.call, timeout=60):
    """The version the service's /health names before the split moves, or "" if it doesn't answer."""
    try:
        status, reply, _ = caller(url, "/health", timeout=timeout)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return ""
    return (((reply or {}).get("model") or {}).get("version") or "") if status == 200 else ""


def main(argv=None, caller=smoke.call, clock=time.monotonic, sleep=time.sleep):
    with open(smoke.DEPLOY_TOML, "rb") as f:
        toml = tomllib.load(f)
    cfg, sha = toml["canary"], toml["smoke"]["sha"]
    with open(smoke.MODEL_TOML, "rb") as f:
        repo = tomllib.load(f)["data"]["repo"]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="the service's own URL")
    ap.add_argument("--tag-url", required=True, help="the new revision's tag URL")
    ap.add_argument("--version", required=True, help="the new revision's model version")
    ap.add_argument("--old-version", help="the model that served before (default: the service's /health now)")
    ap.add_argument("--seconds", type=float, default=float(cfg["watch_s"]), help="how long to watch")
    ap.add_argument("--github-output", type=Path, help="append the results as canary_* lines")
    a = ap.parse_args(argv)

    old = a.old_version if a.old_version is not None else served_before(a.url, caller)
    body = {"repo": repo, "sha": sha}
    via_service, via_tag = watch(a.url, a.tag_url, body, a.version, old, a.seconds, float(cfg["rps"]),
                                 float(cfg["timeout_s"]), caller, clock, sleep)
    problems, seen = judge(via_service, via_tag, a.version, old, float(cfg["max_error_rate"]),
                           float(cfg["max_p95_ms"]))
    seen["percent"], seen["watch_s"] = int(cfg["percent"]), a.seconds
    why = "; ".join(problems)
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            f.writelines(f"canary_{k}={'' if v is None else v}\n" for k, v in seen.items())
            f.write(f"canary_passed={str(not problems).lower()}\ncanary_why={why}\n")
    split = (f"{seen['new_via_service']} of {seen['via_service']} service-URL calls answered by the new model"
             if old and old != a.version else "the same model on both revisions: the split can't be told apart")
    print(f"canary: {seen['canary_calls']} calls reached {a.version[:12]} at {seen['percent']}%, "
          f"{seen['errors']} errors, p95 {seen['p95_ms']} ms; {split}")
    if problems:
        print(f"canary: failed: {why}", file=sys.stderr)
        return 1
    print("canary: passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
