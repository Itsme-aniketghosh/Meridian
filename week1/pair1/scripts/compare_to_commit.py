"""Compare the scanner's edit lines with the lines commit c651331b34 actually removed.

Writes results/scanner_vs_commit.csv with one row per line and a status:
  both          scanner found it and the commit changed it
  scanner_only  scanner found it, the commit did not change it
  commit_only   the commit changed it, the scanner missed it

Usage: python scripts/compare_to_commit.py [path-to-django]
"""
import csv
import os
import re
import subprocess
import sys
from collections import Counter

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
BEFORE, AFTER = "4353640ea9", "c651331b34"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")


def removed_lines():
    """{(file, line number in BEFORE): text} for every .py line the commit removed or changed."""
    out = subprocess.check_output(
        ["git", "-C", REPO, "diff", "-U0", BEFORE, AFTER, "--", "*.py"]
    ).decode("utf-8", errors="replace")

    removed, path, old, in_header = {}, None, None, False
    for line in out.splitlines():
        if line.startswith("diff --git "):
            path, old, in_header = None, None, True
            continue
        if in_header:
            if line.startswith("--- "):
                name = line[4:]
                path = name[2:] if name.startswith("a/") else None
                continue
            if not line.startswith("@@"):
                continue
            in_header = False
        match = HUNK.match(line)
        if match:
            old = int(match.group(1))
            continue
        if path and old is not None and line.startswith("-"):
            removed[(path, old)] = line[1:].strip()
            old += 1
    return removed


def load(csv_path):
    with open(csv_path, newline="") as fh:
        return {(r["file"], int(r["line"])): r for r in csv.DictReader(fh)}


def main():
    removed = removed_lines()
    scanner = load("results/edit_sites.csv")
    other = load("results/sites_other.csv")

    rows = []
    for key in sorted(set(removed) | set(scanner)):
        if key in removed and key in scanner:
            status = "both"
        elif key in scanner:
            status = "scanner_only"
        else:
            status = "commit_only"
        found = scanner.get(key) or other.get(key)
        kind = found["kind"] if found else ""
        text = removed.get(key) or found["text"]
        rows.append([key[0], key[1], status, kind, text])

    with open("results/scanner_vs_commit.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "line", "status", "scanner_kind", "text"])
        writer.writerows(rows)

    counts = Counter(r[2] for r in rows)
    print(f".py lines the commit removed or changed: {len(removed)}  (03-data says ~289)")
    print(f"both:         {counts['both']}")
    print(f"scanner_only: {counts['scanner_only']}")
    print(f"commit_only:  {counts['commit_only']}")
    print(f"files the commit touched: {len({k[0] for k in removed})}")

    for status in ("scanner_only", "commit_only"):
        print(f"\n--- {status} ---")
        for r in rows:
            if r[2] == status:
                tag = f" [{r[3]}]" if r[3] else ""
                print(f"{r[0]}:{r[1]}{tag}  {r[4][:100]}")


if __name__ == "__main__":
    main()
