"""Find every line at the snapshot that uses ugettext or one of its variants.

Writes:
  results/edit_sites.csv   import and call lines (the edit set)
  results/sites_other.csv  every other mention (definitions, passed as a value, ...)

Usage: python scripts/scan_sites.py [path-to-django]
"""
import csv
import os
import subprocess
import sys
from collections import Counter

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
EXPECTED_COMMIT = "4353640ea9495d58fabd0357253b82de3b069408"
SYMBOLS = {"ugettext", "ugettext_lazy", "ugettext_noop", "ungettext", "ungettext_lazy"}
PRIORITY = {"import": 0, "call": 1, "alias": 2, "other": 3}   # if one line has several kinds, keep the first


def same(a, b):
    """True if two tree-sitter nodes are the same piece of code."""
    return a is not None and b is not None and (a.start_byte, a.end_byte) == (b.start_byte, b.end_byte)


def classify(node):
    """Is this mention an import, a direct call, or something else?"""
    up = node
    while up is not None:
        if up.type in ("import_statement", "import_from_statement"):
            return "import"
        up = up.parent

    parent = node.parent
    # ugettext("...")
    if parent.type == "call" and same(parent.child_by_field_name("function"), node):
        return "call"
    # translation.ugettext("...")
    if parent.type == "attribute" and same(parent.child_by_field_name("attribute"), node):
        grand = parent.parent
        if grand is not None and grand.type == "call" and same(grand.child_by_field_name("function"), parent):
            return "call"
        # _ = ugettext_lazy  (an alias made with =, not with import)
    if parent.type == "assignment" and same(parent.child_by_field_name("right"), node):
        return "alias"
    return "other"


def identifiers(root):
    """Every identifier node in the tree (names in code, never in strings or comments)."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "identifier":
            yield node
        stack.extend(node.children)


def find_py_files(repo):
    files = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        files += [os.path.join(root, n) for n in names if n.endswith(".py")]
    return sorted(files)


def main():
    commit = subprocess.check_output(["git", "-C", REPO, "rev-parse", "HEAD"], text=True).strip()
    if commit != EXPECTED_COMMIT:
        sys.exit(f"Wrong snapshot: {commit}. Run: git -C {REPO} checkout 4353640ea9")

    parser = Parser(Language(tspython.language()))
    lines = {}   # (file, line) -> {"kind": ..., "symbols": set(), "text": ...}

    for path in find_py_files(REPO):
        with open(path, "rb") as fh:
            source = fh.read()
        text_lines = source.decode("utf-8", errors="replace").splitlines()
        tree = parser.parse(source)
        rel = os.path.relpath(path, REPO)

        for node in identifiers(tree.root_node):
            name = node.text.decode()
            if name not in SYMBOLS:
                continue
            line_no = node.start_point[0] + 1
            kind = classify(node)
            row = lines.setdefault((rel, line_no), {
                "kind": kind, "symbols": set(), "text": text_lines[line_no - 1].strip()})
            row["symbols"].add(name)
            if PRIORITY[kind] < PRIORITY[row["kind"]]:
                row["kind"] = kind

    os.makedirs("results", exist_ok=True)
    edit, other = [], []
    for (rel, line_no), row in sorted(lines.items()):
        out = [rel, line_no, row["kind"], "|".join(sorted(row["symbols"])), row["text"]]
        (other if row["kind"] == "other" else edit).append(out)

    header = ["file", "line", "kind", "symbol", "text"]
    for name, rows in (("results/edit_sites.csv", edit), ("results/sites_other.csv", other)):
        with open(name, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)

    kinds = Counter(r[2] for r in edit)
    symbols = Counter(s for r in edit for s in r[3].split("|"))
    print(f"commit: {commit}")
    print(f"edit lines: {len(edit)}  (commit changed 265)")
    print(f"  import lines: {kinds['import']}  (target 110)")
    print(f"  alias lines:  {kinds['alias']}  (like _ = ugettext_lazy)")
    print(f"  call lines:   {kinds['call']}  (target 169)")
    print(f"files with edit lines: {len({r[0] for r in edit})}  (target 118)")
    print(f"other mentions (not in the edit set): {len(other)} lines")
    print("lines per symbol:", dict(symbols.most_common()))


if __name__ == "__main__":
    main()
