"""The smoke test deploy.yml runs on a revision before it gets any traffic (S3, contract D1).

Three checks, against a URL: the image built on the runner (every run) or a Cloud Run revision's tag URL (main):
- GET /health answers 200 with the champion deploy.yml checked: its version and its md5
- POST /score for [smoke] sha in deploy.toml answers 200, and its risk is the one the same champion gives the same
  row on the runner (serve.scorer, the image's own code, with the staged champion) to [smoke] risk_tolerance
- the reply names the same model as /health

A failed check exits 1 and nothing moves: the new revision keeps 0% of traffic and the old one serves. Standard
library only, except the expected risk, which needs the model's packages (deploy.yml has them) unless it's given.

    python Model-Deployment/smoke.py --url http://localhost:8080 --version <sha> --md5 <md5>
    python Model-Deployment/smoke.py --url "$TAG_URL" --version <sha> --md5 <md5> --github-output "$GITHUB_OUTPUT"
"""
import argparse
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
REPO = DEPLOY.parent
DEPLOY_TOML = DEPLOY / "deploy.toml"
MODEL_TOML = REPO / "Model-Pipeline" / "model.toml"


def call(url, path, body=None, timeout=60):
    """(status, JSON reply or None, seconds) for one GET, or POST when body is given."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url.rstrip("/") + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    seconds = time.perf_counter() - t0
    try:
        return status, json.loads(raw), seconds
    except ValueError:
        return status, None, seconds


def health(url, timeout=60, tries=5, wait=5.0):
    """/health, retried while the server starts (a refused connection, a 5xx): the last answer."""
    for attempt in range(1, tries + 1):
        try:
            status, reply, seconds = call(url, "/health", timeout=timeout)
            if status < 500 or attempt == tries:
                return status, reply, seconds
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if attempt == tries:
                raise
        time.sleep(wait)


def expected_risk(sha, repo, champion=None):
    """The risk the champion gives sha's row here, through the image's own scorer: what /score must answer."""
    sys.path.insert(0, str(DEPLOY))
    from serve.scorer import Scorer
    reply, _ = Scorer(champion=champion).score({"repo": repo, "sha": sha})
    return reply["risk"]


def check(url, version, model_md5, sha, repo, risk, tolerance, timeout=60, tries=5, wait=5.0):
    """(problems, results): every check's failure in words, and what was seen."""
    problems, seen = [], {}
    try:
        status, reply, seconds = health(url, timeout, tries, wait)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        return [f"/health: no answer after {tries} tries ({e})"], seen
    seen["health_status"], seen["health_s"] = status, round(seconds, 3)
    model = (reply or {}).get("model") or {}
    if status != 200:
        problems.append(f"/health answered {status}")
    elif model.get("version") != version or model.get("md5") != model_md5:
        problems.append(f"/health serves {model.get('version')} md5 {model.get('md5')}, "
                        f"deploy.yml checked {version} md5 {model_md5}")

    try:
        status, reply, seconds = call(url, "/score", {"repo": repo, "sha": sha}, timeout=timeout)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        return problems + [f"/score: no answer ({e})"], seen
    seen["score_status"], seen["score_s"] = status, round(seconds, 3)
    if status != 200 or not isinstance(reply, dict) or not isinstance(reply.get("risk"), (int, float)):
        problems.append(f"/score for {sha[:12]} answered {status}: {(reply or {}).get('error', reply)}")
        return problems, seen
    seen["risk"], seen["expected_risk"] = reply["risk"], risk
    if abs(reply["risk"] - risk) > tolerance:
        problems.append(f"/score for {sha[:12]} gave risk {reply['risk']!r}, the champion gives {risk!r} "
                        f"(tolerance {tolerance})")
    if reply.get("model") != {"version": version, "md5": model_md5}:
        problems.append(f"/score's reply names model {reply.get('model')}, not {version[:12]} md5 {model_md5}")
    return problems, seen


def main(argv=None):
    with open(DEPLOY_TOML, "rb") as f:
        smoke = tomllib.load(f)["smoke"]
    with open(MODEL_TOML, "rb") as f:
        repo = tomllib.load(f)["data"]["repo"]
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="the server: http://localhost:8080, or a revision's tag URL")
    ap.add_argument("--version", required=True, help="the champion's version deploy.yml checked")
    ap.add_argument("--md5", required=True, help="the champion's model md5 deploy.yml checked")
    ap.add_argument("--sha", default=smoke["sha"], help="a commit to score (default: deploy.toml [smoke] sha)")
    ap.add_argument("--expect-risk", type=float, help="its risk; default: the staged champion scores it here")
    ap.add_argument("--champion", type=Path, help="the champion.json to score with (default: the registry's)")
    ap.add_argument("--wait", type=float, default=5.0, help="seconds between /health tries")
    ap.add_argument("--github-output", type=Path, help="append the results as smoke_* lines")
    a = ap.parse_args(argv)

    risk = a.expect_risk if a.expect_risk is not None else expected_risk(a.sha, repo, a.champion)
    problems, seen = check(a.url, a.version, a.md5, a.sha, repo, risk, float(smoke["risk_tolerance"]),
                           timeout=float(smoke["timeout_s"]), tries=int(smoke["tries"]), wait=a.wait)
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            f.writelines(f"smoke_{k}={v}\n" for k, v in seen.items())
            f.write(f"smoke_passed={str(not problems).lower()}\n")
    for p in problems:
        print(f"smoke: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"smoke: passed. /health {seen['health_s']} s, /score {seen['score_s']} s: {a.sha[:12]} risk "
          f"{seen['risk']!r} (the champion gives {risk!r}), model {a.version[:12]} md5 {a.md5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
