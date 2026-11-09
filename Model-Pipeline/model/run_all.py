"""The whole model pipeline in one command (guideline §8.1). Run from Model-Pipeline/.

    python -m model.run_all            # the real table: data check, train, seed noise, evaluate, bias, gate, registry
    python -m model.run_all --fake     # the same steps on tests/fake_table.py in a temp folder, in under 2 minutes:
                                       # CI's model job runs it on every PR (packets/README.md, Integration tests)
    python -m model.run_all --dry-run  # print the commands, run nothing

Runs every step in order and stops at the first failure, saying which step and why. A step whose module isn't in the
repo yet fails clearly instead of being skipped, so a green run always means every step ran. --fake writes nothing
outside its temp folder, MLflow included.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]   # Model-Pipeline/
MODEL_TOML = HERE / "model.toml"
# --fake: a small table and a short search, so every step runs for real in under 2 minutes.
FAKE_TABLE = {"n": 4000, "seed": 31}
FAKE_SEARCH = ["--start", "4", "--step", "2", "--max-trials", "4", "--reps", "200"]
# Steps --fake can't run yet, with why. Remove a line when its module takes --features and --out (or its fake args).
FAKE_LATER = {"gate": "gate.py's fake arguments (Chaitali, when gate.py lands)",
              "registry": "nothing to register from a fake table"}


def steps(fake=None):
    """(name, owner, module, args), in order. fake: the temp folder of a --fake run, or None for the real table."""
    if fake is None:
        return [
            ("data", "Utkarsh", "model.train", ["--check"]),
            ("train", "Rachna", "model.train", ["--tune", "logreg", "forest", "lgbm"]),
            ("seed noise", "Rachna", "model.train", ["--seed-noise", "reports/candidate.json"]),
            ("evaluate", "Rochan", "model.evaluate", []),
            ("bias", "Chaitali", "model.bias", []),
            ("gate", "Chaitali", "model.gate", []),
            ("registry", "Rochan", "model.registry", []),
        ]
    fake = Path(fake)
    table, out = fake / "features.parquet", fake / "reports"
    io = ["--config", str(MODEL_TOML), "--features", str(table), "--out", str(out)]
    candidate = ["--candidate", str(out / "candidate.json")]
    plan = [
        ("data", "Utkarsh", "model.train", ["--check", *io]),
        ("train", "Rachna", "model.train", ["--tune", "logreg", "forest", "lgbm", *FAKE_SEARCH, *io]),
        ("seed noise", "Rachna", "model.train", ["--seed-noise", str(out / "candidate.json"), *io]),
        ("evaluate", "Rochan", "model.evaluate", [*io, *candidate, "--no-mlflow", "--no-charts"]),
        ("bias", "Chaitali", "model.bias", [*io, *candidate, "--no-mlflow", "--no-charts"]),
        ("gate", "Chaitali", "model.gate", []),
        ("registry", "Rochan", "model.registry", []),
    ]
    return [s for s in plan if s[0] not in FAKE_LATER]


def write_fake(folder):
    """The fake feature table, as the real one is stored. Returns its path."""
    sys.path.insert(0, str(HERE))
    from tests.fake_table import fake_table
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "features.parquet"
    fake_table(**FAKE_TABLE).to_parquet(path, index=False)
    return path


def show(plan):
    for i, (name, owner, module, args) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {name} ({owner}): python -m {module} {' '.join(args)}".rstrip(), flush=True)


def run(plan, until=None, runner=subprocess.run, find=importlib.util.find_spec, cwd=HERE, env=None):
    """Run each step; return 0 if all pass, else the failing step's exit code (at least 1)."""
    names = [name for name, *_ in plan]
    if until and until not in names:
        raise SystemExit(f"--until {until!r}: not a step; one of {names}")
    for i, (name, owner, module, args) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {name} ({owner}): python -m {module} {' '.join(args)}".rstrip(), flush=True)
        if find(module) is None:
            print(f"FAILED at {name}: {module} isn't in the repo yet ({owner}'s step)", flush=True)
            return 1
        code = runner([sys.executable, "-m", module, *args], cwd=cwd, env=env).returncode
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
    p.add_argument("--fake", action="store_true", help="every step on tests/fake_table.py, in a temp folder")
    p.add_argument("--until", help="stop after this step")
    p.add_argument("--dry-run", action="store_true", help="print the steps without running them")
    a = p.parse_args(argv)
    if not a.fake:
        plan = steps()
        if a.dry_run:
            show(plan)
            return 0
        return run(plan, a.until)
    with tempfile.TemporaryDirectory(prefix="meridian-fake-") as d:
        plan = steps(d)
        if a.dry_run:
            show(plan)
            return 0
        start = time.monotonic()
        write_fake(d)
        env = {**os.environ, "MLFLOW_TRACKING_URI": f"sqlite:///{Path(d) / 'mlflow.db'}"}
        code = run(plan, a.until, env=env)
        for name, why in FAKE_LATER.items():
            print(f"not in --fake yet: {name}: {why}")
        took = time.monotonic() - start
        print(f"--fake took {took:.0f} s" + ("" if took <= 120 else " (over the 2-minute budget)"), flush=True)
        return code


if __name__ == "__main__":
    sys.exit(main())
