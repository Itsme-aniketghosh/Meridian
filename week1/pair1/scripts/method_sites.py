"""Method-call benchmark, step 1: find every call of the old methods at each snapshot.

Two benchmarks, each a method that Django stopped calling in one commit:
  is_ajax   request.is_ajax()                        conversion 7fa0fa45c5 (2019)
  is_auth   user.is_authenticated(), is_anonymous()  conversion c1aec0feda (2016)

The snapshot is the conversion commit's parent. Each one is checked out as its own git worktree
next to the main clone (django-is_ajax, django-is_auth), so the main clone stays at 4353640ea9.

Tree-sitter finds every use `<receiver>.<method>` and every `def <method>`. Each use is a
  call     user.is_authenticated()
  assign   user.is_authenticated = lambda: False   (replaces the method on one object; tests do this)
  other    anything else, e.g. passed around without calling it
Then the conversion commit's diff says which lines Django changed, and any changed line that
mentions a method name but isn't a use or a definition is printed as missed.

Writes:
  results/method_sites.csv   one row per use: benchmark, file, line, col (of the method name), kind, ...
  results/method_defs.csv    where each old method is defined at each snapshot

Usage: python scripts/method_sites.py [path-to-django]
"""
import csv
import os
import re
import subprocess
import sys
from collections import Counter

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
BENCHMARKS = {
    "is_ajax": {"conversion": "7fa0fa45c5", "methods": {"is_ajax"}},
    "is_auth": {"conversion": "c1aec0feda", "methods": {"is_authenticated", "is_anonymous"}},
}


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], text=True)


def worktree(name, sha):
    """The snapshot, checked out next to the main clone. Made on first run."""
    path = f"{REPO}-{name}"
    if not os.path.isdir(path):
        subprocess.check_call(["git", "-C", REPO, "worktree", "add", "-q", "--detach", path, sha])
    head = git(path, "rev-parse", "HEAD").strip()
    if head != sha:
        sys.exit(f"{path} is at {head}, expected {sha}")
    return path


def changed_old_lines(repo, snapshot, conversion):
    """Lines on the snapshot side that the conversion commit removed or rewrote, per file."""
    changed, path = {}, None
    for line in git(repo, "diff", "-U0", "--no-color", snapshot, conversion, "--", "*.py").split("\n"):
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            changed.setdefault(path, set()).update(range(start, start + count))
    return changed


def find_py_files(repo):
    files = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        files += [os.path.join(root, n) for n in names if n.endswith(".py")]
    return sorted(files)


def walk(node):
    stack = [node]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def same(a, b):
    return a is not None and b is not None and (a.start_byte, a.end_byte) == (b.start_byte, b.end_byte)


def use_kind(attribute):
    """Is this `x.method` called, assigned to, or something else?"""
    parent = attribute.parent
    if parent.type == "call" and same(parent.child_by_field_name("function"), attribute):
        return "call"
    up = attribute
    while up.parent is not None and up.parent.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        up = up.parent
    if up.parent is not None and up.parent.type in ("assignment", "augmented_assignment") \
            and same(up.parent.child_by_field_name("left"), up):
        return "assign"
    return "other"


def main():
    parser = Parser(Language(tspython.language()))
    sites, defs = [], []

    for name, bench in BENCHMARKS.items():
        snapshot = git(REPO, "rev-parse", bench["conversion"] + "^").strip()
        path = worktree(name, snapshot)
        changed = changed_old_lines(REPO, snapshot, bench["conversion"])
        methods = bench["methods"]
        name_pattern = re.compile(r"\b(%s)\b" % "|".join(sorted(methods)))
        found = set()   # lines with a use or a definition

        for full in find_py_files(path):
            rel = os.path.relpath(full, path)
            with open(full, "rb") as fh:
                source = fh.read()
            text_lines = source.decode("utf-8", errors="replace").split("\n")
            for node in walk(parser.parse(source).root_node):
                if node.type == "function_definition":
                    fname = node.child_by_field_name("name")
                    if fname.text.decode() in methods:
                        defs.append([name, snapshot[:10], rel, fname.start_point[0] + 1, fname.text.decode()])
                        found.add((rel, fname.start_point[0] + 1))
                if node.type != "attribute":
                    continue
                attr = node.child_by_field_name("attribute")
                method = attr.text.decode()
                if method not in methods:
                    continue
                line = attr.start_point[0] + 1
                found.add((rel, line))
                sites.append([name, snapshot[:10], rel, line, attr.start_point[1], method, use_kind(node),
                              node.child_by_field_name("object").text.decode(),
                              "yes" if line in changed.get(rel, set()) else "no",
                              text_lines[line - 1].strip()])

        # Lines the commit changed that mention a method name, but hold no use or definition
        missed = []
        for rel, lines in changed.items():
            full = os.path.join(path, rel)
            if not os.path.exists(full):
                continue
            with open(full, encoding="utf-8", errors="replace") as fh:
                text_lines = fh.read().split("\n")
            missed += [(rel, l) for l in sorted(lines)
                       if l <= len(text_lines) and name_pattern.search(text_lines[l - 1]) and (rel, l) not in found]

        mine = [s for s in sites if s[0] == name]
        print(f"{name}: snapshot {snapshot[:10]} (parent of {bench['conversion']})")
        print(f"  uses found: {len(mine)} on {len({(s[2], s[3]) for s in mine})} lines in {len({s[2] for s in mine})} files"
              f"  by kind {dict(Counter(s[6] for s in mine))}  by method {dict(Counter(s[5] for s in mine))}")
        print(f"  changed by the commit: {sum(1 for s in mine if s[8] == 'yes')} of {len(mine)}")
        print(f"  changed lines naming a method with no use or definition found: {len(missed)} {missed}")
        print(f"  definitions: {[(d[2], d[3], d[4]) for d in defs if d[0] == name]}")

    os.makedirs("results", exist_ok=True)
    sites.sort(key=lambda s: (s[0], s[2], s[3], s[4]))
    with open("results/method_sites.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["benchmark", "snapshot", "file", "line", "col", "method", "kind", "receiver",
                         "changed_by_commit", "text"])
        writer.writerows(sites)
    defs.sort()
    with open("results/method_defs.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["benchmark", "snapshot", "file", "line", "method"])
        writer.writerows(defs)
    print("saved results/method_sites.csv, method_defs.csv")


if __name__ == "__main__":
    main()
