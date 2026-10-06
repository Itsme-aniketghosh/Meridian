"""Strict method-call benchmark on HA, step 4b (Pair 1's q3_check.py): a person checks the 30 sampled
lines, one at a time, timed. Only file and line are shown, not the scanner's or the resolvers' answers.

For each line, opens the file in VS Code at that line (if the `code` command exists).
Question: does this line use ConfigEntries.async_setup_platforms (hass.config_entries.async_setup_platforms),
so that it must change before the method is removed?  y / n
Saves after every answer, so nothing is lost if you stop.

Reads method_hand_check_30_sample.csv. Writes method_hand_check_30.csv and method_hand_check_30_summary.json
Usage: python3 method_check.py
"""
import csv
import json
import os
import shutil
import subprocess
import time

ROOT = os.path.abspath("data/code/wt_method_strict")
TOTAL = 395


def save(rows):
    with open("method_hand_check_30.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["file", "line", "is_use", "note", "seconds"])
        writer.writerows(rows)


def main():
    with open("method_hand_check_30_sample.csv", newline="") as fh:
        sample = list(csv.DictReader(fh))
    code = shutil.which("code")
    print(f"{len(sample)} lines to check.")
    print("For each, decide: does this line use hass.config_entries.async_setup_platforms")
    print("(ConfigEntries' method, not zwave_me's own function), so it must change?  y = yes, n = no.")
    if not code:
        print(f"\n(VS Code's `code` command wasn't found. Open each file yourself under {ROOT}.)")
    input("\nPress Enter to START THE TIMER and open the first file...")

    start, rows = time.perf_counter(), []
    for i, s in enumerate(sample, 1):
        t0 = time.perf_counter()
        path = os.path.join(ROOT, s["file"])
        print(f"\n[{i}/{len(sample)}] {s['file']}:{s['line']}")
        if code:
            subprocess.run([code, "-g", f"{path}:{s['line']}"])
        while True:
            answer = input("  Real use to change? (y/n): ").strip().lower()
            if answer in ("y", "n"):
                break
        note = input("  Note (Enter to skip): ").strip()
        seconds = round(time.perf_counter() - t0, 1)
        rows.append([s["file"], s["line"], answer, note, seconds])
        save(rows)
        print(f"  saved ({seconds}s)")

    total, n = time.perf_counter() - start, len(rows)
    per_site = total / 60 / n
    summary = {"sites_checked": n, "yes": sum(r[2] == "y" for r in rows), "no": sum(r[2] == "n" for r in rows),
               "total_minutes": round(total / 60, 1), "minutes_per_site": round(per_site, 2),
               f"projected_minutes_for_{TOTAL}": round(per_site * TOTAL, 1)}
    with open("method_hand_check_30_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
