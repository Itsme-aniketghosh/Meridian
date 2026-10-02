"""Q3 step 2: hand-check the 30 sampled lines, one at a time, timing each.

For each line, opens the file in VS Code at that line (if the `code` command exists).
You answer: is this a real line that has to change in the migration? (y/n)
Saves after every answer, so nothing is lost if you stop.

Reads results/hand_check_30_sample.csv
Writes results/hand_check_30.csv and results/hand_check_30_summary.json
Usage: python scripts/q3_check.py [path-to-django]
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")


def save(rows):
    with open("results/hand_check_30.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["file", "line", "is_call_site", "note", "seconds"])
        writer.writerows(rows)


def main():
    with open("results/hand_check_30_sample.csv", newline="") as fh:
        sample = list(csv.DictReader(fh))
    code = shutil.which("code")

    print(f"{len(sample)} lines to check.")
    print("For each, decide: does this line import, call, or alias one of")
    print("ugettext / ugettext_lazy / ugettext_noop / ungettext / ungettext_lazy in code,")
    print("so that it must change in the migration?  y = yes, n = no.")
    if not code:
        print("\n(VS Code's `code` command wasn't found. Open each file yourself at the line shown.)")
    input("\nPress Enter to START THE TIMER and open the first file...")

    start = time.perf_counter()
    rows = []
    for i, s in enumerate(sample, 1):
        t0 = time.perf_counter()
        path = os.path.join(REPO, s["file"])
        print(f"\n[{i}/{len(sample)}] {s['file']}:{s['line']}")
        if code:
            subprocess.run([code, "-g", f"{path}:{s['line']}"])
        while True:
            answer = input("  Real line to edit? (y/n): ").strip().lower()
            if answer in ("y", "n"):
                break
        note = input("  Note (Enter to skip): ").strip()
        seconds = round(time.perf_counter() - t0, 1)
        rows.append([s["file"], s["line"], answer, note, seconds])
        save(rows)
        print(f"  saved ({seconds}s)")

    total = time.perf_counter() - start
    n = len(rows)
    per_site = total / 60 / n
    summary = {
        "sites_checked": n,
        "yes": sum(r[2] == "y" for r in rows),
        "no": sum(r[2] == "n" for r in rows),
        "total_minutes": round(total / 60, 1),
        "minutes_per_site": round(per_site, 2),
        "projected_minutes_for_279": round(per_site * 279, 1),
        "projected_minutes_for_265": round(per_site * 265, 1),
    }
    with open("results/hand_check_30_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
