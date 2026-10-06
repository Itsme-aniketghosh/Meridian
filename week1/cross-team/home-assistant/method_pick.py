"""Strict method-call benchmark on HA, step 4a (Pair 1's q3_pick.py): pick 30 uses with a fixed seed,
and save the list before anyone checks it.

Reads method_sites.csv. Writes method_hand_check_30_sample.csv
Usage: python3 method_pick.py
"""
import csv
import platform
import random

SEED, N = 1, 30

with open("method_sites.csv", newline="") as fh:
    rows = list(csv.DictReader(fh))

random.seed(SEED)
sample = random.sample(rows, N)

with open("method_hand_check_30_sample.csv", "w", newline="") as fh:
    writer = csv.writer(fh, lineterminator="\n")
    writer.writerow(["order", "file", "line"])
    for i, r in enumerate(sample, 1):
        writer.writerow([i, r["file"], r["line"]])

print(f"picked {N} of {len(rows)} with random.seed({SEED}), Python {platform.python_version()}")
print("saved method_hand_check_30_sample.csv")
