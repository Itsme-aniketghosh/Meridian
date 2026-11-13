"""The fault table for the report (packets/aniket-lead section 4): every fault script run, one row each.

    python -m tests.faults.table        # from Model-Pipeline/, on frozen main

Each test_fault_NN_*.py opens with three lines: "Fault NN: ...", "Expected: ...", "Why: ...". A known misbehaviour is
an expectedFailure naming its owner, so CI stays green and the table says it.
"""
import contextlib
import importlib
import io
import pkgutil
import sys
import unittest
from pathlib import Path


def rows():
    for m in sorted(pkgutil.iter_modules([str(Path(__file__).parent)]), key=lambda m: m.name):
        if not m.name.startswith("test_fault_"):
            continue
        mod = importlib.import_module(f"tests.faults.{m.name}")
        fault, expected, _ = (line.split(": ", 1)[1] for line in mod.__doc__.strip().splitlines()[:3])
        with contextlib.redirect_stdout(io.StringIO()):  # train.py prints its tables; the report wants the row
            r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
                unittest.defaultTestLoader.loadTestsFromModule(mod))

        def names(tests):
            return "; ".join(t.id().rsplit(".", 1)[1] for t, _ in tests)
        if r.failures or r.errors:
            result = "FAILS: " + names(r.failures + r.errors)
        elif r.expectedFailures:
            result = "Doesn't behave (owner in the script): " + names(r.expectedFailures)
        else:
            result = f"Behaves ({r.testsRun} {'test' if r.testsRun == 1 else 'tests'})"
        yield int(m.name.split("_")[2]), fault, expected, result


def main():
    print("| # | Fault | Expected | Result |\n|---|---|---|---|")
    found = {n: row for n, *row in rows()}
    for n in range(1, max(21, *found) + 1):
        fault, expected, result = found.get(n, ("", "", "No script yet"))
        print(f"| {n} | {fault} | {expected} | {result} |")
    return 1 if len(found) < 21 or any(r.startswith("FAILS") for _, _, r in found.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
