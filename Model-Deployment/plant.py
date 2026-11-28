"""The planted broken image (S3, Nov 28): deploy.yml run by hand with `plant` breaks the serving code in the runner's
checkout before the build, so main's own workflow has to keep it from serving. Nothing here is ever committed: the
edit lives in that run's checkout only, and the image is pushed under a "-planted-<kind>" tag, never the champion's.

    wrong-risk  a feature read as 0 (#158's bug): /score's risk moves, so the smoke test against the new revision fails
                and it never gets traffic. deploy.yml skips the runner's own smoke test for this kind, so the broken
                image reaches Cloud Run and the main-only gate is the one that stops it
    slow        every /score waits 2.5 s: the risk is right and the smoke test passes, so it takes the canary's share,
                and the canary fails on p95 latency (over 2 s) and rolls back. Refused when [canary] percent is 0

    python Model-Deployment/plant.py wrong-risk
"""
import argparse
import sys
import tomllib
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
SCORER = DEPLOY / "serve" / "scorer.py"
MARK = "# PLANTED by deploy.yml (S3 test, never committed)"

PLANTS = {
    "wrong-risk": ("        row = self.table.loc[[sha]]\n",
                   f"        row = self.table.loc[[sha]].assign(prior_bug_fixes=0)   {MARK}\n"),
    "slow": ('        if not isinstance(body, dict):\n            raise BadRequest("the body must be a JSON object")\n',
             '        if not isinstance(body, dict):\n            raise BadRequest("the body must be a JSON object")\n'
             f"        __import__(\"time\").sleep(2.5)   {MARK}\n"),
}


class PlantError(RuntimeError):
    pass


def plant(kind, scorer=SCORER, canary_percent=None):
    """Edit scorer.py in place for kind; the anchor must be there exactly once, so a moved line fails loudly."""
    if kind not in PLANTS:
        raise PlantError(f"no plant named {kind}: {', '.join(PLANTS)}")
    if kind == "slow" and not canary_percent:
        raise PlantError("the slow plant passes the smoke test, so only the canary catches it: [canary] percent is 0")
    anchor, broken = PLANTS[kind]
    text = scorer.read_text(encoding="utf-8")
    if MARK in text:
        raise PlantError(f"{scorer.name} is planted already")
    if text.count(anchor) != 1:
        raise PlantError(f"the {kind} anchor is in {scorer.name} {text.count(anchor)} times, not once: update plant.py")
    scorer.write_text(text.replace(anchor, broken), encoding="utf-8")
    return broken.strip()


def main(argv=None, scorer=SCORER):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=sorted(PLANTS))
    a = ap.parse_args(argv)
    with open(DEPLOY / "deploy.toml", "rb") as f:
        percent = int(tomllib.load(f)["canary"]["percent"])
    try:
        line = plant(a.kind, scorer, percent)
    except PlantError as e:
        sys.exit(f"plant: {e}")
    print(f"plant: {a.kind} in {scorer.name}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
