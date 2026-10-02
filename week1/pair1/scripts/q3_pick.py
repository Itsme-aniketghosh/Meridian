"""Q3 step 1: pick 30 of the edit lines with a fixed seed, and save the list before checking.

Reads results/sites_279.csv. Writes results/hand_check_30_sample.csv
Usage: python scripts/q3_pick.py
"""
import csv
import platform
import random

SEED, N = 1, 30

with open("results/sites_279.csv", newline="") as fh:
    rows = list(csv.DictReader(fh))

random.seed(SEED)
sample = random.sample(rows, N)

with open("results/hand_check_30_sample.csv", "w", newline="") as fh:
    writer = csv.writer(fh, lineterminator="\n")
    writer.writerow(["order", "file", "line"])
    for i, r in enumerate(sample, 1):
        writer.writerow([i, r["file"], r["line"]])

print(f"picked {N} of {len(rows)} with random.seed({SEED}), Python {platform.python_version()}")
print("saved results/hand_check_30_sample.csv")
