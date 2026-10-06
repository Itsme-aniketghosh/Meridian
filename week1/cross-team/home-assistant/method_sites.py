"""Strict method-call benchmark on HA, step 1 (Pair 1's method_sites.py, applied to HA).

The method: ConfigEntries.async_setup_platforms, called as `hass.config_entries.async_setup_platforms(...)`.
  deprecated + converted  cd03c49fc27 (2022-07-09, #73806): one commit changed most uses to async_forward_entry_setups
  removed                 739963b5ee1 (2023-04-23, #91929): the definition is deleted, so no use can survive it

The snapshot is the conversion commit's parent, checked out as its own worktree (data/code/wt_method_strict).
Tree-sitter finds every `<receiver>.async_setup_platforms` and every `def async_setup_platforms` in every .py file
(tests included, labelled), and sorts each use into call / assign / other, exactly as Pair 1 did.

Two checks against git, both on the snapshot's line numbers:
  changed_by_commit   the conversion commit removed or rewrote the line
  changed_by_removal  the line is gone or rewritten by the removal commit (diff snapshot..removal), so it was a real use
Any changed line that names the method but holds no use or definition is printed as missed.
Plain `git grep` is labelled too, to show what text search adds or misses.

Writes method_sites.csv, method_defs.csv, method_grep.csv
Usage: python3 method_sites.py   (from this folder, after cloning data/repo; see README)
"""
import csv
import os
import re
import subprocess
import sys
from collections import Counter

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

REPO = os.path.abspath("data/repo")
WT = os.path.abspath("data/code/wt_method_strict")
CONVERSION, REMOVAL = "cd03c49fc27", "739963b5ee1"
METHODS = {"async_setup_platforms"}


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], text=True)


def worktree(sha):
    if not os.path.isdir(WT):
        subprocess.check_call(["git", "-C", REPO, "worktree", "add", "-q", "--detach", WT, sha])
    head = git(WT, "rev-parse", "HEAD").strip()
    if head != sha:
        sys.exit(f"{WT} is at {head}, expected {sha}")
    return WT


def changed_old_lines(snapshot, later):
    """Lines on the snapshot side that `later` removed or rewrote, per file."""
    changed, path = {}, None
    for line in git(REPO, "diff", "-U0", "--no-color", "--no-renames", snapshot, later, "--", "*.py").split("\n"):
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            changed.setdefault(path, set()).update(range(start, start + count))
    return changed


def walk(node):
    stack = [node]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def same(a, b):
    return a is not None and b is not None and (a.start_byte, a.end_byte) == (b.start_byte, b.end_byte)


def use_kind(attribute):
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


def bucket(rel):
    if rel.startswith("tests/"):
        return "tests"
    if rel.startswith("homeassistant/components/"):
        return "components"
    if rel.startswith("homeassistant/"):
        return "core"
    return "other"


def main():
    snapshot = git(REPO, "rev-parse", CONVERSION + "^").strip()
    root = worktree(snapshot)
    by_commit = changed_old_lines(snapshot, CONVERSION)
    by_removal = changed_old_lines(snapshot, REMOVAL)
    parser = Parser(Language(tspython.language()))
    pattern = re.compile(r"\b(%s)\b" % "|".join(sorted(METHODS)))
    sites, defs, found, parse_errors = [], [], set(), 0

    py = sorted(os.path.relpath(os.path.join(d, n), root) for d, ds, ns in os.walk(root)
                if ".git" not in d for n in ns if n.endswith(".py"))
    for rel in py:
        with open(os.path.join(root, rel), "rb") as fh:
            source = fh.read()
        if pattern.search(source.decode("utf-8", errors="replace")) is None:
            continue
        tree = parser.parse(source)
        parse_errors += tree.root_node.has_error
        text_lines = source.decode("utf-8", errors="replace").split("\n")
        for node in walk(tree.root_node):
            if node.type == "function_definition":
                fname = node.child_by_field_name("name")
                if fname.text.decode() in METHODS:
                    defs.append([snapshot[:10], rel, fname.start_point[0] + 1, fname.text.decode()])
                    found.add((rel, fname.start_point[0] + 1))
            if node.type != "attribute":
                continue
            attr = node.child_by_field_name("attribute")
            if attr.text.decode() not in METHODS:
                continue
            line = attr.start_point[0] + 1
            found.add((rel, line))
            sites.append([snapshot[:10], rel, line, attr.start_point[1], attr.text.decode(), use_kind(node),
                          node.child_by_field_name("object").text.decode(), bucket(rel),
                          "yes" if line in by_commit.get(rel, set()) else "no",
                          "yes" if line in by_removal.get(rel, set()) else "no",
                          text_lines[line - 1].strip()])

    missed = []
    for rel, lines in by_commit.items():
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            continue
        text_lines = open(full, encoding="utf-8", errors="replace").read().split("\n")
        missed += [(rel, l) for l in sorted(lines)
                   if l <= len(text_lines) and pattern.search(text_lines[l - 1]) and (rel, l) not in found]

    # Plain text search, every hit labelled by what tree-sitter found on that line
    site_lines = {(s[1], s[2]) for s in sites}
    def_lines = {(d[1], d[2]) for d in defs}
    grep = []
    for hit in git(root, "grep", "-n", "-e", "async_setup_platforms", "--", "*.py").splitlines():
        rel, line, text = hit.split(":", 2)
        key = (rel, int(line))
        label = "use" if key in site_lines else "definition" if key in def_lines else \
            "longer name" if not pattern.search(text) else \
            "same name, not a method use" if re.search(r"(?<![.\w])async_setup_platforms\(", text) else "string or comment"
        grep.append([rel, int(line), label, "yes" if re.search(r"\.async_setup_platforms\(", text) else "no",
                     text.strip()[:110]])

    print(f"snapshot {snapshot[:10]} (parent of {CONVERSION}); {len(py)} .py files, {parse_errors} with parse errors")
    print(f"uses: {len(sites)} on {len(site_lines)} lines in {len({s[1] for s in sites})} files")
    print(f"  by kind {dict(Counter(s[5] for s in sites))}  by bucket {dict(Counter(s[7] for s in sites))}")
    print(f"  changed by the conversion commit: {sum(s[8] == 'yes' for s in sites)} of {len(sites)}")
    print(f"  gone by the removal commit: {sum(s[9] == 'yes' for s in sites)} of {len(sites)}")
    print(f"  changed lines naming the method with no use or definition found: {len(missed)} {missed[:10]}")
    print(f"definitions: {[(d[1], d[2]) for d in defs]}")
    print(f"git grep: {len(grep)} lines {dict(Counter(g[2] for g in grep))}; "
          f"the old benchmark's regex in components only: "
          f"{sum(g[3] == 'yes' and g[0].startswith('homeassistant/components/') for g in grep)}")

    for name, header, rows in (
            ("method_sites.csv", ["snapshot", "file", "line", "col", "method", "kind", "receiver", "bucket",
                                  "changed_by_commit", "changed_by_removal", "text"], sorted(sites, key=lambda s: (s[1], s[2], s[3]))),
            ("method_defs.csv", ["snapshot", "file", "line", "method"], sorted(defs)),
            ("method_grep.csv", ["file", "line", "label", "old_regex_hit", "text"], sorted(grep))):
        with open(name, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
    print("saved method_sites.csv, method_defs.csv, method_grep.csv")


if __name__ == "__main__":
    main()
