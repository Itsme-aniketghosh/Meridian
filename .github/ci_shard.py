"""Runs shard K of N of `python -m unittest discover -s DIR -t .`, split by test module, for ci.yml's matrix.

Run it from the folder ci.yml runs discover from:  python ../.github/ci_shard.py tests 2 3

It finds the modules discover would load (test*.py in DIR and the packages under it) and deals them, slowest first,
to the shard with the least time so far, by WEIGHTS. Every shard job computes the same split and checks that it holds
every module exactly once before running its own part, so a new test file runs with no workflow change. Correctness
never depends on WEIGHTS, only the balance does: a module not listed counts DEFAULT seconds. Each run prints its
modules' times, slowest first, to update WEIGHTS from.
"""
import os
import sys
import time
import unittest
from fnmatch import fnmatch
from pathlib import Path

DEFAULT = 2
WEIGHTS = {  # seconds on windows-latest
}


def modules(start):
    """Every test module discover finds under start, as dotted names from the current folder."""
    found = []
    for root, dirs, files in os.walk(start):
        dirs[:] = [d for d in dirs if Path(root, d, "__init__.py").exists()]  # discover only enters packages
        found += [Path(root, f[:-3]).as_posix().replace("/", ".") for f in files
                  if fnmatch(f, "test*.py") and f[:-3].isidentifier()]
    return sorted(found)


def split(mods, n, where):
    weight = {m: WEIGHTS.get(f"{where}:{m}", DEFAULT) for m in mods}
    parts, load = [[] for _ in range(n)], [0.0] * n
    for m in sorted(mods, key=lambda m: (-weight[m], m)):
        i = load.index(min(load))
        parts[i].append(m)
        load[i] += weight[m]
    return parts


class Timed(unittest.TextTestResult):
    """Adds each test's time, with its class and module set-up, to its module."""
    took = {}

    def startTestRun(self):
        super().startTestRun()
        Timed.mark = time.perf_counter()

    def stopTest(self, test):
        super().stopTest(test)
        now, m = time.perf_counter(), type(test).__module__
        Timed.took[m] = Timed.took.get(m, 0) + now - Timed.mark
        Timed.mark = now


def main(start, k, n):
    where = Path.cwd().name
    mods = modules(start)
    parts = split(mods, n, where)
    assert sorted(sum(parts, [])) == mods and mods, f"the {n} shards must hold each of {len(mods)} modules once"
    mine = sorted(parts[k - 1])
    print(f"{where} shard {k}/{n}: {len(mine)} of {len(mods)} modules: {' '.join(mine)}", flush=True)
    if not mine:
        return 0
    runner = unittest.TextTestRunner(resultclass=Timed)
    result = unittest.main(module=None, argv=[sys.argv[0], *mine], testRunner=runner, exit=False).result
    for m, s in sorted(Timed.took.items(), key=lambda kv: -kv[1]):
        print(f'    "{where}:{m}": {s:.0f},')
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
