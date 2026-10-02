"""Q2 step 1b: check the answer key in alias_calls.csv the way Python really scopes names.

For each _() call, look in the enclosing function, then its parent, out to the
module, and stop at the first scope where _ is given a meaning. Status:
  ok        nearest meaning of _ is ugettext* -> the answer key row is right
  shadowed  nearest meaning of _ is something else (for _ in ..., a parameter, _ = x)
  unbound   _ only means ugettext* inside some other function
  mixed     the same scope gives _ both meanings -> check by hand

Writes results/alias_calls_checked.csv
Usage: python scripts/q2_check_alias_calls.py [path-to-django]
"""
import csv
import os
import sys
from collections import Counter, defaultdict

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
SYMBOLS = {"ugettext", "ugettext_lazy", "ugettext_noop", "ungettext", "ungettext_lazy"}
SCOPES = ("function_definition", "lambda")


def walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def same(a, b):
    return a is not None and b is not None and (a.start_byte, a.end_byte) == (b.start_byte, b.end_byte)


def scope(node):
    """The function or lambda this node sits in, or None for module level."""
    up = node.parent
    while up is not None:
        if up.type in SCOPES:
            return up
        up = up.parent
    return None


def key(s):
    return "module" if s is None else (s.start_byte, s.end_byte)


def scope_name(s):
    if s is None:
        return "module"
    name = s.child_by_field_name("name")
    return f"{name.text.decode() if name is not None else 'lambda'}@{s.start_point[0] + 1}"


def role(node):
    """What this occurrence of _ is doing."""
    p = node.parent
    if p.type == "aliased_import" and same(p.child_by_field_name("alias"), node):
        return "import"
    if p.type == "assignment" and same(p.child_by_field_name("left"), node):
        return "assign"
    if p.type in ("for_statement", "for_in_clause") and same(p.child_by_field_name("left"), node):
        return "for"
    if p.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        return "unpack"
    if p.type in ("parameters", "lambda_parameters", "default_parameter",
                  "typed_parameter", "typed_default_parameter"):
        return "param"
    if p.type in ("as_pattern", "as_pattern_target"):
        return "as"
    if p.type == "call" and same(p.child_by_field_name("function"), node):
        return "call"
    return "use"


def main():
    with open("results/alias_calls.csv", newline="") as fh:
        files = sorted({r["file"] for r in csv.DictReader(fh)})

    parser = Parser(Language(tspython.language()))
    out = []

    for rel in files:
        with open(os.path.join(REPO, rel), "rb") as fh:
            source = fh.read()
        tree = parser.parse(source)

        bindings = defaultdict(list)   # scope key -> [(is_alias, label, line)]
        calls = []
        for node in walk(tree.root_node):
            if node.type != "identifier" or node.text != b"_":
                continue
            r = role(node)
            if r == "call":
                calls.append(node)
                continue
            if r == "use":
                continue
            is_alias, label = False, r
            if r == "import":
                real = node.parent.child_by_field_name("name").text.decode()
                is_alias, label = real in SYMBOLS, f"import {real} as _"
            elif r == "assign":
                right = node.parent.child_by_field_name("right")
                text = right.text.decode() if right is not None else ""
                is_alias = right is not None and right.type == "identifier" and text in SYMBOLS
                label = f"_ = {text[:40]}"
            bindings[key(scope(node))].append((is_alias, label, node.start_point[0] + 1))

        for call in calls:
            chain, s = [], scope(call)
            while True:
                chain.append(s)
                if s is None:
                    break
                s = scope(s)

            status, where, seen = "unbound", "", ""
            for s in chain:
                found = bindings.get(key(s))
                if not found:
                    continue
                aliases = [b for b in found if b[0]]
                others = [b for b in found if not b[0]]
                status = "ok" if aliases and not others else "shadowed" if others and not aliases else "mixed"
                where = scope_name(s)
                seen = "; ".join(f"{b[1]} (line {b[2]})" for b in found)[:150]
                break
            out.append([rel, call.start_point[0] + 1, status, where, seen])

    out.sort(key=lambda r: (r[0], r[1]))
    with open("results/alias_calls_checked.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "line", "status", "decided_in", "bindings_seen"])
        writer.writerows(out)

    counts = Counter(r[2] for r in out)
    print(f"_() calls checked: {len(out)}  (step 1 found 542)")
    for status in ("ok", "shadowed", "unbound", "mixed"):
        print(f"  {status:9} {counts[status]}")
    for status in ("shadowed", "unbound", "mixed"):
        rows = [r for r in out if r[2] == status]
        if rows:
            print(f"\n--- {status}: {len(rows)} calls in {len({r[0] for r in rows})} files (first 10) ---")
            for r in rows[:10]:
                print(f"{r[0]}:{r[1]}  in {r[3] or '-'}  | {r[4]}")


if __name__ == "__main__":
    main()
