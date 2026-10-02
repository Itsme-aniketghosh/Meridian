"""Q2 step 2: ask Jedi what each aliased _() call really is, and score it.

right      Jedi names the old function, or lands on the line that defines it
wrong      Jedi names anything else (including the new name, e.g. gettext_lazy)
no_answer  Jedi returns nothing or raises an error

Reads results/alias_calls.csv and results/sites_other.csv (definition lines).
Writes results/q2_jedi.csv
Usage: python scripts/q2_jedi.py [path-to-django]
"""
import csv
import os
import statistics
import sys
import time
from collections import Counter, defaultdict

import jedi

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
TRANSLATION = os.path.join("django", "utils", "translation", "__init__.py")
PASS_BAR = 0.95


def main():
    with open("results/alias_calls.csv", newline="") as fh:
        calls = list(csv.DictReader(fh))
    with open("results/sites_other.csv", newline="") as fh:
        def_lines = {r["symbol"]: int(r["line"]) for r in csv.DictReader(fh)}

    by_file = defaultdict(list)
    for c in calls:
        by_file[c["file"]].append(c)

    project = jedi.Project(REPO)
    rows, times = [], []
    start = time.perf_counter()

    for rel, items in sorted(by_file.items()):
        path = os.path.join(REPO, rel)
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
        script = jedi.Script(code=code, path=path, project=project)

        for c in items:
            expected = c["expected_symbol"]
            t0 = time.perf_counter()
            try:
                names = script.goto(int(c["line"]), int(c["col"]), follow_imports=True)
                error = ""
            except Exception as exc:
                names, error = [], type(exc).__name__
            seconds = time.perf_counter() - t0
            times.append(seconds)

            full = def_file = def_line = ""
            if names:
                first = names[0]
                full = first.full_name or ""
                module = str(first.module_path or "")
                def_file = os.path.relpath(module, REPO) if module else ""
                def_line = first.line
                right = (full == f"django.utils.translation.{expected}"
                         or (def_file == TRANSLATION and def_line == def_lines.get(expected)))
                result = "right" if right else "wrong"
            else:
                result = "no_answer"

            rows.append([c["file"], c["line"], c["col"], expected, result,
                         full, def_file, def_line, len(names), error, round(seconds, 4)])

    total = time.perf_counter() - start
    with open("results/q2_jedi.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "line", "col", "expected_symbol", "result", "jedi_full_name",
                         "jedi_def_file", "jedi_def_line", "n_answers", "error", "seconds"])
        writer.writerows(rows)

    counts = Counter(r[4] for r in rows)
    n = len(rows)
    print(f"calls: {n}")
    for result in ("right", "wrong", "no_answer"):
        print(f"  {result:10} {counts[result]}")
    print(f"right: {counts['right']} of {n} = {counts['right'] / n:.3f}  (pass bar {PASS_BAR})")
    print(f"verdict: {'PASS' if counts['right'] / n >= PASS_BAR else 'FAIL'}")
    print(f"time: {total:.1f}s total, median {statistics.median(times) * 1000:.1f} ms per call, "
          f"p95 {sorted(times)[int(0.95 * n) - 1] * 1000:.1f} ms")

    print("\nright by expected symbol:")
    for sym in sorted({r[3] for r in rows}):
        sub = [r for r in rows if r[3] == sym]
        print(f"  {sym:15} {sum(r[4] == 'right' for r in sub)} of {len(sub)}")

    bad = [r for r in rows if r[4] != "right"]
    if bad:
        print("\nwhat Jedi said instead:", dict(Counter(r[5] or r[9] or "(nothing)" for r in bad).most_common(8)))
        print("\nfirst 10 not right:")
        for r in bad[:10]:
            print(f"  {r[0]}:{r[1]}:{r[2]}  expected {r[3]} -> {r[4]}: {r[5] or r[9] or '(nothing)'}"
                  f" at {r[6]}:{r[7]}")


if __name__ == "__main__":
    main()
