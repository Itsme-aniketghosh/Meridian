"""Q1: time tree-sitter over every .py file in the Django repo.

Usage: python scripts/q1_speed.py [path-to-django]
"""
import json
import os
import platform
import statistics
import subprocess
import sys
import time

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
RUNS = 3


def sh(cmd):
    """Run a shell command and return its output, or 'unknown' if it fails."""
    try:
        return subprocess.check_output(cmd, shell=True, text=True).strip()
    except Exception:
        return "unknown"


def find_py_files(repo):
    """Every .py file in the repo, skipping .git, in a fixed order."""
    files = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            if name.endswith(".py"):
                files.append(os.path.join(root, name))
    return sorted(files)


def one_run(parser):
    """One full pass. Returns (total seconds, parse-only seconds, files, error files)."""
    start = time.perf_counter()
    files = find_py_files(REPO)
    parse_seconds = 0.0
    error_files = []
    for path in files:
        with open(path, "rb") as fh:
            source = fh.read()
        p_start = time.perf_counter()
        tree = parser.parse(source)
        parse_seconds += time.perf_counter() - p_start
        if tree.root_node.has_error:
            error_files.append(os.path.relpath(path, REPO))
    total_seconds = time.perf_counter() - start
    return total_seconds, parse_seconds, files, error_files


def main():
    parser = Parser(Language(tspython.language()))

    totals, parses = [], []
    for i in range(RUNS):
        total, parse, files, errors = one_run(parser)
        totals.append(total)
        parses.append(parse)
        print(f"run {i + 1}: total {total:.2f}s, parse only {parse:.2f}s")

    result = {
        "commit": sh(f"git -C {REPO} rev-parse HEAD"),
        "files": len(files),
        "files_with_parse_errors": len(errors),
        "runs": RUNS,
        "total_seconds_each_run": [round(t, 2) for t in totals],
        "parse_seconds_each_run": [round(p, 2) for p in parses],
        "median_total_seconds": round(statistics.median(totals), 2),
        "median_parse_seconds": round(statistics.median(parses), 2),
        "median_total_minutes": round(statistics.median(totals) / 60, 3),
        "machine": {
            "cpu": sh("sysctl -n machdep.cpu.brand_string"),
            "ram_gb": round(int(sh("sysctl -n hw.memsize") or 0) / 2**30, 1)
            if sh("sysctl -n hw.memsize").isdigit() else "unknown",
            "os": platform.platform(),
            "python": platform.python_version(),
        },
    }

    os.makedirs("results", exist_ok=True)
    with open("results/q1_speed.json", "w") as fh:
        json.dump(result, fh, indent=2)
    with open("results/q1_parse_errors.txt", "w") as fh:
        fh.write("\n".join(errors) + ("\n" if errors else ""))

    print(json.dumps(result, indent=2))
    print(f"\nFiles with parse errors are listed in results/q1_parse_errors.txt")


if __name__ == "__main__":
    main()
