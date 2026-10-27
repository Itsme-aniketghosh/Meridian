"""Hand-check the Spark collision pairs, blind: did BOTH commits really change `function` in `path`?

    python -m tests.hand_check_collisions --initials RS            # from Data-Pipeline/: check, resumable
    python -m tests.hand_check_collisions --initials RS --browser  # also open both commits on GitHub
    python -m tests.hand_check_collisions --initials RS --merge    # then write your answers into the label file

For each pair it prints, for each of the two commits, the function's code from the Spark checkout in the team
snapshot, with that commit's added lines marked + and the lines it deleted from the function listed with -. Long
unchanged stretches are folded. It never shows the model's verdict or note, and it doesn't decide: you read and answer.

Your answers go to out/hand_check_collisions.csv (git-ignored) until --merge copies them into
labels/spark_collision_pairs_50.csv (human_verdict, checked_by, and your note after the model's).
Type q at any prompt to stop; the next run resumes where you left off.
"""
import argparse
import csv
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from meridian.common import data_root

LABELS = HERE / "labels" / "spark_collision_pairs_50.csv"
SHEET = HERE.parent / "out" / "hand_check_collisions.csv"
COLS = ["key_a", "key_b", "path", "function", "human_verdict", "my_note"]
ANSWERS = {"r": "real", "real": "real", "n": "no", "no": "no", "u": "unclear", "unclear": "unclear"}
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
CONTEXT = 2   # unchanged lines shown around each change


# ---------- the sheet ----------
def key(r):
    return (r["key_a"], r["key_b"], r["path"], r["function"])


def load_labels():
    with open(LABELS, newline="") as f:
        rows = list(csv.DictReader(f))
    return rows, list(rows[0].keys())


def load_sheet():
    if not SHEET.exists():
        return {}
    with open(SHEET, newline="") as f:
        return {key(r): r for r in csv.DictReader(f)}


def save_sheet(done):
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    tmp = SHEET.with_name(SHEET.name + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, lineterminator="\n")
        w.writeheader()
        for r in done.values():
            w.writerow({c: r.get(c, "") for c in COLS})
    tmp.replace(SHEET)


# ---------- reading the code from git ----------
def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


def strip_code(line, python=False):
    """The line without string literals and comments, so their brackets don't count."""
    s = line.strip()
    if not python and (s.startswith("*") or s.startswith("/*")):
        return ""   # a Scaladoc/Javadoc comment line
    line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
    if python:
        line = re.sub(r"'(?:\\.|[^'\\])*'", "''", line)
        i = line.find("#")
    else:
        line = re.sub(r"'(?:\\.|[^'\\])'", "''", line)
        i = line.find("//")
    return line[:i] if i >= 0 else line


def indent(s):
    return len(s) - len(s.lstrip())


def span(lines, start):
    """(first, last) 0-based line of the function whose def is at start: to its matching closing brace,
    or, for a body without braces, to the line before the next line indented no deeper than the def."""
    base, depth, parens, seen = indent(lines[start]), 0, 0, False
    for j in range(start, len(lines)):
        code = strip_code(lines[j])
        if j > start and not seen and parens <= 0 and code.strip() and indent(lines[j]) <= base:
            return start, j - 1
        for ch in code:
            if ch == "(" and not seen:
                parens += 1
            elif ch == ")" and not seen:
                parens -= 1
            elif ch == "{":
                depth, seen = depth + 1, True
            elif ch == "}":
                depth -= 1
        if seen and depth <= 0:
            return start, j
    return start, len(lines) - 1


def python_span(lines, start):
    """(first, last) 0-based line of a Python function: its signature up to the ':' that closes it, then every
    line indented deeper than the def (blank lines and triple-quoted strings don't end it)."""
    base, depth, j = indent(lines[start]), 0, start
    while j < len(lines):
        code = strip_code(lines[j], python=True)
        depth += sum(code.count(c) for c in "([{") - sum(code.count(c) for c in ")]}")
        if depth <= 0 and code.rstrip().endswith(":"):
            break
        j += 1
    end, quote = j, None
    for k in range(j + 1, len(lines)):
        line = lines[k]
        if quote is None and line.strip() and indent(line) <= base:
            break
        if line.strip():
            end = k
        for q in ('"""', "'''"):
            if line.count(q) % 2 == 1:
                quote = q if quote is None else (None if quote == q else quote)
    return start, end


JAVA_NOT_DECL = ("return ", "new ", "throw ", "else", "if ", "if(", "while", "for ", "for(", "switch", "case ")


def java_declaration(code, name):
    """A Java method declaration of name: `[modifiers] Type name(` and not a call or a statement."""
    s = code.strip()
    if not s or s.startswith(JAVA_NOT_DECL) or s.endswith(";"):
        return False
    m = re.search(r"\b" + re.escape(name) + r"\s*\(", s)
    if not m:
        return False
    before = s[:m.start()].strip()
    return (bool(before) and "=" not in before and "." not in before.split()[-1]
            and re.fullmatch(r"(?:@\w+(?:\([^)]*\))?\s+)*[\w<>\[\],.?\s]+", before) is not None)


def function_spans(lines, name, python=False, java=False):
    if java:
        hits = [i for i, l in enumerate(lines) if java_declaration(strip_code(l), name)]
        return [span(lines, i) for i in hits]
    kinds = r"(?:def|class)" if python else r"(?:def|class|object|trait)"
    pat = re.compile(r"\b" + kinds + r"\s+" + re.escape(name) + r"\b")
    hits = [i for i, l in enumerate(lines) if pat.search(strip_code(l, python))]
    return [(python_span if python else span)(lines, i) for i in hits]


def changes(repo, sha, path):
    """(added new-file line numbers, [(old-file line number, text)] deleted), both 1-based."""
    out = git(repo, "diff", "-U0", "--no-color", f"{sha}^", sha, "--", path)
    if out is None:
        return None, None
    added, deleted, o, n = set(), [], None, None
    for line in out.splitlines():
        m = HUNK.match(line)
        if m:
            o, n = int(m.group(1)), int(m.group(3))
            continue
        if o is None:
            continue   # the diff's header lines
        if line.startswith("+"):
            added.add(n)
            n += 1
        elif line.startswith("-"):
            deleted.append((o, line[1:]))
            o += 1
    return added, deleted


def show_commit(repo, label, url, path, name):
    sha = url.rstrip("/").rsplit("/", 1)[-1]
    print(f"\n  ── commit {label}  {sha[:10]} ──")
    if git(repo, "cat-file", "-e", sha + "^{commit}") is None:
        print("  this commit isn't in the snapshot's Spark history: use --browser for this row")
        return
    new, old = git(repo, "show", f"{sha}:{path}"), git(repo, "show", f"{sha}^:{path}")
    added, deleted = changes(repo, sha, path)
    if new is None:
        print(f"  {path} doesn't exist after this commit (moved or deleted?)")
        return
    if added is None or (not added and not deleted):
        print(f"  this commit didn't change {path}")
        return
    nl, ol = new.splitlines(), (old or "").splitlines()
    py, java = path.endswith(".py"), path.endswith(".java")
    spans = function_spans(nl, name, py, java)
    if not spans:
        print(f"  no `{name}` declaration in this commit's version of the file (renamed or removed?)")
    for s, e in spans:
        print(f"  {name}: new-file lines {s + 1}-{e + 1} ({e - s + 1} lines)   (+ = line this commit added)")
        changed = [i for i in range(s, e + 1) if (i + 1) in added]
        keep = {s, e} | {k for i in changed for k in range(i - CONTEXT, i + CONTEXT + 1) if s <= k <= e}
        last = None
        for i in sorted(keep):
            if last is not None and i > last + 1:
                print(f"         ⋮   ({i - last - 1} unchanged lines)")
            mark = "+" if (i + 1) in added else " "
            print(f"  {i + 1:>5} {mark} {nl[i]}")
            last = i
        outside = sorted(a for a in added if not s + 1 <= a <= e + 1)
        before = [a for a in outside if a < s + 1]
        after = [a for a in outside if a > e + 1]
        near = []
        if before:
            near.append(f"last before it: line {before[-1]}")
        if after:
            near.append(f"first after it: line {after[0]}")
        if near:
            print("        added lines outside the function: " + ", ".join(near))
    for s, e in function_spans(ol, name, py, java):
        dels = [(no, t) for no, t in deleted if s + 1 <= no <= e + 1]
        print(f"  lines this commit deleted from the function (old-file lines {s + 1}-{e + 1}):")
        for no, t in dels:
            print(f"  {no:>5} - {t}")
        if not dels:
            print("         (none)")


# ---------- the check ----------
def ask(prompt):
    a = input(prompt).strip()
    return None if a.lower() == "q" else a


def open_url(url):
    try:
        subprocess.run(["open", url], check=False)
    except FileNotFoundError:
        pass


def summary(done, total):
    c = Counter(r["human_verdict"] for r in done.values())
    print(f"\n{len(done)} of {total} checked · real {c['real']} · no {c['no']} · unclear {c['unclear']}")
    print("Run the same command again to resume." if len(done) < total else "All done. Next: --merge")


def check(run_id, browser, redo=()):
    repo = data_root() / "snapshots" / run_id / "repos" / "apache__spark"
    if not (repo / ".git").exists() and git(repo, "rev-parse", "HEAD") is None:
        sys.exit(f"no Spark checkout at {repo}: run `python run_demo.py download --zip ...` first")
    rows, _ = load_labels()
    done = load_sheet()
    for n in redo:
        if 1 <= n <= len(rows) and key(rows[n - 1]) in done:
            del done[key(rows[n - 1])]
            print(f"row {n} cleared: you'll check it again")
    save_sheet(done)
    todo = [r for r in rows if key(r) not in done]
    print(f"{len(rows)} pairs · {len(done)} done · {len(todo)} to go · q at any prompt stops (progress is saved)")
    for r in todo:
        print("\n" + "=" * 100)
        print(f"[row {rows.index(r) + 1} · {len(done)} of {len(rows)} done] {r['key_a']}  vs  {r['key_b']}")
        print(f"  file:     {r['path']}")
        print(f"  function: {r['function']}")
        print(f"  commit a: {r['evidence_url_a']}")
        print(f"  commit b: {r['evidence_url_b']}")
        show_commit(repo, "a", r["evidence_url_a"], r["path"], r["function"])
        show_commit(repo, "b", r["evidence_url_b"], r["path"], r["function"])
        if browser:
            open_url(r["evidence_url_a"])
            open_url(r["evidence_url_b"])
        print()
        while True:
            a = ask("  Did BOTH commits change this function? r = real, n = no, u = unclear, q = stop: ")
            if a is None:
                return summary(done, len(rows))
            if a.lower() in ANSWERS:
                break
        note = ask("  Note (Enter to skip; say why if unclear): ")
        if note is None:
            return summary(done, len(rows))
        done[key(r)] = {"key_a": r["key_a"], "key_b": r["key_b"], "path": r["path"], "function": r["function"],
                        "human_verdict": ANSWERS[a.lower()], "my_note": note}
        save_sheet(done)
        print("  saved")
    summary(done, len(rows))


def merge(initials):
    rows, fields = load_labels()
    done = load_sheet()
    missing = [k for k in map(key, rows) if k not in done]
    if missing:
        sys.exit(f"{len(missing)} of {len(rows)} pairs not checked yet: finish them first")
    for r in rows:
        d = done[key(r)]
        r["human_verdict"] = d["human_verdict"]
        r["checked_by"] = initials
        if d["my_note"]:
            r["note"] = (r["note"] + " | " if r["note"] else "") + "human: " + d["my_note"]
    with open(LABELS, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    c = Counter(r["human_verdict"] for r in rows)
    agree = sum(r["human_verdict"] == r["verdict"] for r in rows)
    print(f"wrote {LABELS.relative_to(HERE.parent)}")
    print(f"you: real {c['real']} · no {c['no']} · unclear {c['unclear']}  (N = {len(rows)})")
    print(f"the model's verdict agrees with yours on {agree} of {len(rows)}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--initials", required=True, help="2-3 letters, e.g. RS (no names: CSV-POLICY.md)")
    p.add_argument("--run-id", default="70dd1dabfed544b8", help="the team run whose Spark checkout to read")
    p.add_argument("--browser", action="store_true", help="also open both commits on GitHub")
    p.add_argument("--redo", type=int, nargs="*", default=[], help="row numbers to clear and check again")
    p.add_argument("--merge", action="store_true", help="write the finished answers into the label file")
    a = p.parse_args(argv)
    if not (2 <= len(a.initials) <= 3 and a.initials.isalpha()):
        sys.exit("--initials must be 2-3 letters")
    merge(a.initials.upper()) if a.merge else check(a.run_id, a.browser, a.redo)


if __name__ == "__main__":
    main()
