"""Check every CSV in git (tracked or staged) against CSV-POLICY.md. Exit 1 on any problem.

    python3 "DATA PIPELINE/check_csv.py"            # tracked + staged CSVs
    python3 "DATA PIPELINE/check_csv.py" a.csv b.csv # just these
"""
import csv, os, re, subprocess, sys

MAX_BYTES = 5 * 1024 * 1024
MAX_QUOTE = 200
PEOPLE = re.compile(r"(^|_)(author|email|e_mail|username|user_name|login|name|by|assignee|reporter|committer|person|people)s?(_|$)")
OK_COLUMNS = {"checked_by", "ended_by", "changed_by_commit", "symbol_name", "method_name"}   # not people
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
RESERVED = re.compile(r"(^|\.)(example\.(com|org|net)|[\w-]+\.(test|example|invalid|localhost))$", re.I)  # RFC 2606: not real people
HANDLE = re.compile(r"(?<![\w@])@(?!user\b)[A-Za-z0-9_][\w.-]*")
QUOTE_COLS = re.compile(r"(quote|evidence_text|snippet|comment)", re.I)

csv.field_size_limit(MAX_BYTES)


def git_csvs():
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--", "*.csv"], capture_output=True, text=True).stdout
    return [os.path.join(root, p) for p in sorted(set(out.splitlines()))]  # lines, not words: "DATA PIPELINE/" has a space


def words(col):
    """'commit authorEmail' -> 'commit_author_email': camelCase, spaces and dashes split words like '_' does."""
    return re.sub(r"[\s-]+", "_", re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", col.strip())).lower()


def check(path):
    problems = []
    if os.path.getsize(path) > MAX_BYTES:
        problems.append(f"over 5 MB ({os.path.getsize(path):,} bytes): this is data, put it in GCS")
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:  # -sig: Excel's BOM would hide the first column
        rows = list(csv.reader(f))
    if not rows:
        return problems
    header = rows[0]
    for col in header:
        if words(col) not in OK_COLUMNS and PEOPLE.search(words(col)):
            problems.append(f"people column '{col}'")
    quote_idx = [i for i, c in enumerate(header) if QUOTE_COLS.search(c)]
    for n, row in enumerate(rows[1:], 2):
        for i, cell in enumerate(row):
            if any(not RESERVED.search(m.group(1)) for m in EMAIL.finditer(cell)):
                problems.append(f"line {n}: email in column {header[i] if i < len(header) else i}")
            if HANDLE.search(cell):
                problems.append(f"line {n}: @handle '{HANDLE.search(cell).group()}' (write @user)")
            if i in quote_idx and len(cell) > MAX_QUOTE:
                problems.append(f"line {n}: quote is {len(cell)} chars (max {MAX_QUOTE})")
    return problems


if __name__ == "__main__":
    files = sys.argv[1:] or git_csvs()
    bad = 0
    for p in files:
        probs = check(p)
        rel = os.path.relpath(p)
        if probs:
            bad += 1
            print(f"FAIL {rel}")
            for x in probs[:10]:
                print(f"     {x}")
            if len(probs) > 10:
                print(f"     ... {len(probs) - 10} more")
        else:
            print(f"ok   {rel}")
    print(f"\n{len(files) - bad} of {len(files)} CSVs pass")
    sys.exit(1 if bad else 0)
