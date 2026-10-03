"""Grep check: is a plain text search (like Cmd+F) good enough to find the edit lines?

Runs the search a person would: git grep for "ugettext" or "ungettext" in .py files. Then
tree-sitter says what each line it finds really is, using the first label that fits:
  real use      in the edit set (results/sites_279.csv)
  definition    where the old names are defined (results/sites_other.csv)
  longer name   part of a longer name, e.g. def test_ungettext_lazy
  string        inside quoted text, e.g. a name listed in __all__
  docstring     inside a docstring
  comment       inside a # comment

Also checks two things the search can't see:
  aliased calls   _("Hello") never contains the old name (results/alias_calls.csv)
  re-exports      a file importing a nickname for an old name from another file, not from
                  django.utils.translation

Reads results/sites_279.csv, sites_other.csv, alias_calls.csv, scanner_vs_commit.csv. Writes:
  results/grep_hits.csv            one row per line the search finds, with its label
  results/grep_check_summary.json  counts

Usage: python scripts/grep_check.py [path-to-django]
"""
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

REPO = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django")
EXPECTED_COMMIT = "4353640ea9495d58fabd0357253b82de3b069408"
SYMBOLS = {"ugettext", "ugettext_lazy", "ugettext_noop", "ungettext", "ungettext_lazy"}
SEARCH = re.compile(rb"ugettext|ungettext")   # the same as: git grep -e ugettext -e ungettext
LABELS = ["real use", "definition", "longer name", "string", "docstring", "comment", "other"]
TRANSLATION = "django.utils.translation"


def git(*args):
    return subprocess.check_output(["git", "-C", REPO, *args], text=True)


def read_keys(path):
    with open(path, newline="") as fh:
        return {(r["file"], int(r["line"])) for r in csv.DictReader(fh)}


def is_docstring(string_node):
    """A string that is the first statement of a module, class, or function."""
    stmt = string_node.parent
    if stmt is None or stmt.type != "expression_statement":
        return False
    body = stmt.parent
    if body is None or body.type not in ("module", "block"):
        return False
    first = next((c for c in body.named_children if c.type != "comment"), None)
    return first is not None and first.start_byte == stmt.start_byte


def hit_label(node):
    """What one search hit sits in: a name, a comment, a string, or a docstring."""
    if node.type == "identifier":
        return "name" if node.text.decode() in SYMBOLS else "longer name"
    up = node
    while up is not None:
        if up.type == "comment":
            return "comment"
        if up.type == "string":
            return "docstring" if is_docstring(up) else "string"
        up = up.parent
    return "other"


def module_bindings(root):
    """Names a module binds, at top level, to an old function: `import ugettext as _`, `_ = ugettext_lazy`."""
    bound = set()
    for node in root.named_children:
        if node.type == "import_from_statement" and node.child_by_field_name("module_name").text.decode() == TRANSLATION:
            for name in node.children_by_field_name("name"):
                if name.type == "aliased_import":
                    if name.child_by_field_name("name").text.decode() in SYMBOLS:
                        bound.add(name.child_by_field_name("alias").text.decode())
                elif name.text.decode() in SYMBOLS:
                    bound.add(name.text.decode())
        if node.type == "expression_statement" and node.named_children and node.named_children[0].type == "assignment":
            assign = node.named_children[0]
            left, right = assign.child_by_field_name("left"), assign.child_by_field_name("right")
            if right is not None and right.type == "identifier" and right.text.decode() in SYMBOLS:
                bound.add(left.text.decode())
    return bound


def module_all(root):
    """The names in a module's top-level __all__, or None if it has none."""
    for node in root.named_children:
        if node.type == "expression_statement" and node.named_children and node.named_children[0].type == "assignment":
            assign = node.named_children[0]
            if assign.child_by_field_name("left").text == b"__all__":
                right = assign.child_by_field_name("right")
                return {s.text.decode().strip("'\"") for s in right.named_children if s.type == "string"}
    return None


def exported(root, bound):
    """Which of a module's nickname bindings `from it import *` brings in."""
    names = module_all(root)
    return bound & names if names is not None else {b for b in bound if not b.startswith("_")}


def module_files(rel, module):
    """Files a `from <module> import` could mean. Tests run with tests/ on the path."""
    dots = len(module) - len(module.lstrip("."))
    parts = [p for p in module.lstrip(".").split(".") if p]
    if dots:
        base = os.path.dirname(rel)
        for _ in range(dots - 1):
            base = os.path.dirname(base)
        bases = [os.path.join(base, *parts)]
    else:
        bases = [os.path.join(*parts), os.path.join("tests", *parts)]
    return [b + ".py" for b in bases] + [os.path.join(b, "__init__.py") for b in bases]


def find_py_files(repo):
    files = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        files += [os.path.join(root, n) for n in names if n.endswith(".py")]
    return sorted(files)


def main():
    commit = git("rev-parse", "HEAD").strip()
    if commit != EXPECTED_COMMIT:
        sys.exit(f"Wrong snapshot: {commit}. Run: git -C {REPO} checkout 4353640ea9")

    with open("results/sites_279.csv", newline="") as fh:
        edit_kind = {(r["file"], int(r["line"])): r["kind"] for r in csv.DictReader(fh)}
    edit = set(edit_kind)
    definitions = read_keys("results/sites_other.csv")
    alias_lines = read_keys("results/alias_calls.csv")
    with open("results/scanner_vs_commit.csv", newline="") as fh:
        changed = {(r["file"], int(r["line"])) for r in csv.DictReader(fh) if r["status"] in ("both", "commit_only")}

    # 1. The plain search, exactly as a person would run it
    hits = {}   # (file, line) -> text
    for row in git("grep", "-n", "-z", "-e", "ugettext", "-e", "ungettext", "--", "*.py").split("\n"):
        if not row:
            continue
        path, line, text = row.split("\0", 2)
        hits[(path, int(line))] = text.strip()

    # 2. What each line really is
    parser = Parser(Language(tspython.language()))
    hit_kinds = {}   # (file, line) -> set of labels for the hits on that line
    for path in sorted({f for f, _ in hits}):
        with open(os.path.join(REPO, path), "rb") as fh:
            source = fh.read()
        root = parser.parse(source).root_node
        for m in SEARCH.finditer(source):
            line = source.count(b"\n", 0, m.start()) + 1
            node = root.descendant_for_byte_range(m.start(), m.end())
            hit_kinds.setdefault((path, line), set()).add(hit_label(node))
    if set(hit_kinds) != set(hits):
        sys.exit("git grep and the Python search disagree on which lines match")

    rows = []
    for key in sorted(hits):
        if key in edit:
            label = "real use"
        elif key in definitions:
            label = "definition"
        else:
            label = next((l for l in LABELS if l in hit_kinds[key]), "other")
        rows.append([key[0], key[1], label, "yes" if key in changed else "no", hits[key]])

    os.makedirs("results", exist_ok=True)
    with open("results/grep_hits.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["file", "line", "label", "changed_by_commit", "text"])
        writer.writerows(rows)

    # 3. What the search can't see: aliased calls, and nicknames imported from another file
    alias_found = sum(1 for k in alias_lines if k in hits)
    bindings, reexports = {}, []
    trees = {}
    star_checked = 0   # `from x import *` lines whose source binds a nickname
    for path in find_py_files(REPO):
        rel = os.path.relpath(path, REPO)
        with open(path, "rb") as fh:
            trees[rel] = parser.parse(fh.read()).root_node
        bindings[rel] = module_bindings(trees[rel])
    for rel, root in trees.items():
        stack = [root]
        while stack:
            node = stack.pop()
            stack.extend(node.named_children)
            if node.type != "import_from_statement":
                continue
            module = node.child_by_field_name("module_name").text.decode()
            if module == TRANSLATION:
                continue
            sources = [f for f in module_files(rel, module) if bindings.get(f)]
            names = []
            for name in node.children_by_field_name("name"):
                names.append((name.child_by_field_name("name") if name.type == "aliased_import" else name).text.decode())
            star = any(c.type == "wildcard_import" for c in node.children)
            for src in sources:
                brought = set(n for n in names if n in bindings[src])
                if star:
                    star_checked += 1
                    brought |= exported(trees[src], bindings[src])
                for name in sorted(brought):
                    reexports.append({"file": rel, "line": node.start_point[0] + 1, "name": name, "from": src,
                                      "search_sees_it": bool(SEARCH.search(name.encode()))})

    labels = Counter(r[2] for r in rows)
    imports_by_word = sum(1 for r in rows if r[2] != "definition" and "import" in r[4])
    import_lines = [k for k, kind in edit_kind.items() if kind == "import"]
    not_real = [r for r in rows if r[2] not in ("real use", "definition")]
    summary = {
        "commit": commit,
        "search": "git grep -n -e ugettext -e ungettext -- '*.py'",
        "lines_found": len(rows),
        "files_found": len({r[0] for r in rows}),
        "by_label": {l: labels[l] for l in LABELS},
        "edit_lines_found": f"{sum(1 for k in edit if k in hits)} of {len(edit)}",
        "not_real_uses": len(rows) - labels["real use"],
        "non_uses_besides_definitions": {
            "lines": len(not_real),
            "changed_by_commit": sum(1 for r in not_real if r[3] == "yes"),
        },
        "import_counting": {
            "parser_import_lines": len(import_lines),
            "lines_with_the_word_import": imports_by_word,
            "import_lines_without_the_word": sorted(f"{f}:{l}" for f, l in import_lines if "import" not in hits[(f, l)]),
            "word_import_but_not_an_import": sorted(
                f"{r[0]}:{r[1]}" for r in rows if "import" in r[4] and edit_kind.get((r[0], r[1])) != "import"),
        },
        "docs_03_numbers_rebuilt": {
            "how": "the search's lines, minus the 5 lines that define the old names",
            "lines": len(rows) - labels["definition"],
            "files": len({r[0] for r in rows if r[2] != "definition"}),
            "lines_with_the_word_import": imports_by_word,
            "lines_without_it": len(rows) - labels["definition"] - imports_by_word,
        },
        "aliased_call_lines_found": f"{alias_found} of {len(alias_lines)}",
        "files_binding_a_nickname": sum(1 for b in bindings.values() if b),
        "star_imports_checked": star_checked,
        "reexports": reexports,
    }
    with open("results/grep_check_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")

    print(f"commit: {commit}")
    print(f"plain search finds {len(rows)} lines in {summary['files_found']} files")
    for l in LABELS:
        print(f"  {l:<12} {labels[l]:>4}")
    print(f"edit lines found: {summary['edit_lines_found']}; lines that aren't real uses: {summary['not_real_uses']}")
    d = summary["docs_03_numbers_rebuilt"]
    print(f"minus the definitions: {d['lines']} lines in {d['files']} files, "
          f"{d['lines_with_the_word_import']} with the word 'import', {d['lines_without_it']} without")
    n = summary["non_uses_besides_definitions"]
    print(f"non-uses besides definitions: {n['lines']}, of which the commit changed {n['changed_by_commit']}")
    i = summary["import_counting"]
    print(f"import lines: parser {i['parser_import_lines']}, word 'import' {i['lines_with_the_word_import']}; "
          f"without the word: {i['import_lines_without_the_word']}; word but not an import: {i['word_import_but_not_an_import']}")
    print(f"aliased-call lines found: {summary['aliased_call_lines_found']}")
    print(f"files binding a nickname: {summary['files_binding_a_nickname']}; `import *` lines checked: {star_checked}; "
          f"nicknames brought in from another file: {len(reexports)}")
    print("saved results/grep_hits.csv, grep_check_summary.json")


if __name__ == "__main__":
    main()
