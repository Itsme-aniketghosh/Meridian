"""The whole model pipeline in one command (guideline §8.1): python -m model.run_all, from Model-Pipeline/.

Runs every step in order and stops at the first failure, saying which step and why. It's what the Docker image
runs, and what Aniket runs at integration. Each step is its owner's command; a step whose module isn't in the
repo yet fails clearly instead of being skipped, so a green run_all always means every step ran.
"""
import argparse
import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]   # Model-Pipeline/


def steps(candidate):
    """(name, owner, module, args), in order. Edit the args here when an owner's command changes."""
    return [
        ("data", "Utkarsh", "model.train", ["--check"]),
        ("train", "Rachna", "model.train", ["--tune", "logreg", "forest", "lgbm"]),
        ("seed noise", "Rachna", "model.train", ["--seed-noise", f"reports/tuning_{candidate}.json"]),
        ("evaluate", "Rochan", "model.evaluate", []),
        ("bias", "Chaitali", "model.bias", []),
        ("gate", "Chaitali", "model.gate", []),
        ("registry", "Rochan", "model.registry", []),
    ]


def candidate_kind(config):
    """The candidate model kind from model.toml [candidate] kind, set once decision 1 is made."""
    path = Path(config)
    if path.exists():
        with open(path, "rb") as f:
            kind = tomllib.load(f).get("candidate", {}).get("kind")
        if kind:
            return kind
    return None


def run(plan, until=None, runner=subprocess.run, find=importlib.util.find_spec, cwd=HERE):
    """Run each step; return 0 if all pass, else the failing step's exit code (at least 1)."""
    names = [name for name, *_ in plan]
    if until and until not in names:
        raise SystemExit(f"--until {until!r}: not a step; one of {names}")
    for i, (name, owner, module, args) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {name} ({owner}): python -m {module} {' '.join(args)}".rstrip(), flush=True)
        if find(module) is None:
            print(f"FAILED at {name}: {module} isn't in the repo yet ({owner}'s step)", flush=True)
            return 1
        code = runner([sys.executable, "-m", module, *args], cwd=cwd).returncode
        if code != 0:
            print(f"FAILED at {name} ({owner}): exit {code}. Steps after it didn't run.", flush=True)
            return code
        if name == until:
            print(f"stopped after {name}, as asked", flush=True)
            return 0
    print(f"all {len(plan)} steps passed", flush=True)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", default=str(HERE / "model.toml"))
    p.add_argument("--candidate", choices=["logreg", "forest", "lgbm"],
                   help="the candidate model kind; default model.toml [candidate] kind")
    p.add_argument("--until", help="stop after this step")
    p.add_argument("--dry-run", action="store_true", help="print the steps without running them")
    a = p.parse_args(argv)
    candidate = a.candidate or candidate_kind(a.config)
    if candidate is None:
        p.error("no candidate: pass --candidate or set model.toml [candidate] kind (decision 1)")
    plan = steps(candidate)
    if a.dry_run:
        for i, (name, owner, module, args) in enumerate(plan, 1):
            print(f"[{i}/{len(plan)}] {name} ({owner}): python -m {module} {' '.join(args)}".rstrip())
        return 0
    return run(plan, a.until)


if __name__ == "__main__":
    sys.exit(main())
