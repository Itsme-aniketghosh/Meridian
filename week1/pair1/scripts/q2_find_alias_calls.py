"""Q2 step 1: find every call made through an alias of ugettext or its variants.

An alias is a second name for the function:
  from django.utils.translation import ugettext_lazy as _   (import alias)
  _ = ugettext_lazy                                          (assignment alias)
Tree-sitter sees where the alias is made, but at the call _("Hi") it only sees "_".
For every such call, this records which function the alias points to, using the
binding in the same file. That is the answer Jedi and pyright must match.

Writes results/alias_calls.csv
Usage: python scripts/q2_find_alias_calls.py [path-to-django]
"""
import csv
import os
import subprocess
import sys
from collections import Counter, defaultdict

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
EXPECTED_COMMIT = "4353640ea9495d58fabd0357253b82de3b069408"
SYMBOLS = {"ugettext", "ugettext_lazy", "ugettext_noop", "ungettext", "ungettext_lazy"}


def walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def find_bindings(root):
    """alias name -> list of (real symbol, line, kind) created in this file."""
    found = defaultdict(list)
    for node in walk(root):
        if node.type == "import_from_statement":
            for child in node.children_by_field_name("name"):
                if child.type != "aliased_import":
                    continue
                real = child.child_by_field_name("name").text.decode()
                alias = child.child_by_field_name("alias").text.decode()
                if real in SYMBOLS and alias != real:
                    found[alias].append((real, node.start_point[0] + 1, "import"))
        elif node.type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if (left is not None and right is not None
                    and left.type == "identifier" and right.type == "identifier"
                    and right.text.decode() in SYMBOLS):
                found[left.text.decode()].append(
                    (right.text.decode(), node.start_point[0] + 1, "assignment"))
    return found


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
    rows = []

    for path in find_py_files(REPO):
        with open(path, "rb") as fh:
            source = fh.read()
        tree = parser.parse(source)
        bindings = find_bindings(tree.root_node)
        if not bindings:
            continue
        text_lines = source.decode("utf-8", errors="replace").splitlines()
        rel = os.path.relpath(path, REPO)

        for node in walk(tree.root_node):
            if node.type != "call":
                continue
            fn = node.child_by_field_name("function")
            if fn is None or fn.type != "identifier":
                continue
            alias = fn.text.decode()
            if alias not in bindings:
                continue
            options = bindings[alias]
            reals = sorted({opt[0] for opt in options})
            line = fn.start_point[0] + 1
            # column in characters (what Jedi expects), not bytes
            line_start = fn.start_byte - fn.start_point[1]
            col = len(source[line_start:fn.start_byte].decode("utf-8", errors="replace"))
            rows.append([
                rel, line, col, alias, "|".join(reals),
                options[0][2], options[0][1],
                "yes" if len(reals) > 1 else "no",
                text_lines[line - 1].strip()[:120],
            ])

    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    os.makedirs("results", exist_ok=True)
    with open("results/alias_calls.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "line", "col", "alias", "expected_symbol",
                         "binding_kind", "binding_line", "ambiguous", "text"])
        writer.writerows(rows)

    print(f"commit: {commit}")
    print(f"alias calls: {len(rows)}  (03-data says ~480)")
    print(f"files: {len({r[0] for r in rows})}")
    print("by alias name:", dict(Counter(r[3] for r in rows).most_common()))
    print("by real function:", dict(Counter(r[4] for r in rows).most_common()))
    print("by binding kind:", dict(Counter(r[5] for r in rows).most_common()))
    print(f"ambiguous (alias bound to 2+ functions in one file): {sum(r[7] == 'yes' for r in rows)}")


if __name__ == "__main__":
    main()
