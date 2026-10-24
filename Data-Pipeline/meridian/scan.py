"""Scan · step 1 · D3–D4: every place a tracked old name appears in the code. See CONTRACTS.md.

    python -m meridian.scan --run-id <run_id> --config meridian.toml      # from Data-Pipeline/

Reads the code in snapshots/<run_id>/ (every repo in the manifest whose dataset lists symbols). Writes, sorted by
primary key, for the writer:
    out/<run_id>/candidate.jsonl       one row per identifier that is a tracked old name (schema.sql, candidate)
    out/<run_id>/parse_failure.jsonl   one row per file tree-sitter can't parse cleanly (schema.sql, parse_failure)
    out/<run_id>/module_import.jsonl   one row per imported module or name, tracked or not (schema.sql, module_import)
    out/<run_id>/deprecation.jsonl     one row per warn() or decorator that marks a def/class deprecated

Grew out of week 1's scan_sites.py and Rochan's stand-in scanner (same roles and columns).

Files. Every .py file, plus Python outside .py ([scanner] embedded_python, e.g. code.sample) as file_kind
'embedded'. [scanner] skip_paths are never read. .git is never read. Names match case as in git on every OS
(Views.PY is not .py, Test_Case.py is not test_*.py). An unreadable folder stops the run. meridian.resolve walks
the same list.

Candidates. Tree-sitter (pinned) finds every identifier whose text is a tracked old name. Comments and strings are
never identifiers, so they never become rows. Role, by where the identifier sits:
    import       anywhere inside an import statement, including a parenthesised import's continuation lines
    call         ugettext(...) or translation.ugettext(...)
    alias        right side of =, e.g. _ = ugettext_lazy
    definition   name of a def or class, or a left side of = (ugettext = gettext)
    attribute    obj.ugettext, not called
    reference    anything else: passed as a value, a keyword-argument or parameter name, ...
Keyword-argument and parameter names are kept only if [scanner] keep_name_only.
col counts characters, like Jedi (tree-sitter counts bytes). scope is the dotted names of the def/class bodies the
name sits in: parameters, defaults, decorators, bases and a def's own name run in the outer scope.
in_except is 1 inside an except or finally block of the name's own function.

Imports (module_import). Every import statement in every cleanly parsed file: `import a.b` -> (a.b, ''),
`from a import c, d` -> (a, c), (a, d). Relative imports resolve against the file's folders as packages. is_test:
the file matches [scanner] junk test.

Deprecations. warnings.warn() / warn() whose category (its last dotted part, whole) or message (anywhere) matches a
[scanner] deprecation_markers regex: scope is the def/class around it. A decorator whose name matches: scope is the
def/class it decorates. marker is the matched text, the first regex that matches.

Junk ([scanner] junk). test: a path segment from `test` (tests/ anywhere in the path) or a file-name pattern.
migration: the file holds migration_marker. example: a path segment from `example`. Else 'none'.
Junk is a label only: junk rows are kept.

Parse failures. A file whose tree has an ERROR or MISSING node is excluded and becomes a parse_failure row
(CONTRACTS.md rule 9: nothing is silently dropped). The log says how many tracked names those files held.

Same run, same bytes: files and rows in a fixed order, no clock, nothing random.
"""
import json, os, re
from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from meridian.common import data_root, git, handoff, load_config, log, parser, write_jsonl

PY = Parser(Language(tspython.language()))
COLUMNS = ["run_id", "repo", "path", "line", "col", "symbol", "role", "scope", "text", "junk", "file_kind", "in_except"]
FAIL_COLUMNS = ["run_id", "repo", "path", "error"]
IMPORT_COLUMNS = ["run_id", "repo", "path", "line", "module", "name", "is_test"]
DEPRECATION_COLUMNS = ["run_id", "repo", "path", "line", "scope", "qualname", "marker"]
KEYS = {"candidate": ("repo", "path", "line", "col"), "parse_failure": ("repo", "path"),
        "module_import": ("repo", "path", "line", "module", "name"), "deprecation": ("repo", "path", "line")}
DEFS = ("function_definition", "class_definition")


def tracked(cfg):
    """{repo: {old name: module}} for every dataset in meridian.toml that lists symbols."""
    out = {}
    for ds in cfg.get("datasets", {}).values():
        symbols = {s["name"]: s["module"] for s in ds.get("symbols", [])}
        for r in ds.get("repos", []) if symbols else []:
            out[r["repo"]] = symbols
    return out


def answer_keys(cfg):
    """{repo: answer_key} for datasets that have one ([datasets.django] answer_key)."""
    return {r["repo"]: ds["answer_key"] for ds in cfg.get("datasets", {}).values()
            if "answer_key" in ds for r in ds.get("repos", [])}


def skipped(path, patterns):
    """'docs/' is a prefix, '*.po' a pattern. Case-sensitive on every OS (fnmatch ignores case on Windows)."""
    return any(path.startswith(p) if p.endswith("/") else fnmatchcase(path, p) for p in patterns)


def under(path, pattern):
    """'tests/' matches a path segment anywhere (tests/x.py, wagtail/admin/tests/x.py). 'test_*.py' a file name."""
    if pattern.endswith("/"):
        return ("/" + pattern) in ("/" + path)
    return fnmatchcase(path.rsplit("/", 1)[-1], pattern)


def is_test(path, rules):
    return any(under(path, p) for p in rules.get("test", []))


def junk(path, source, rules):
    if is_test(path, rules):
        return "test"
    marker = rules.get("migration_marker")
    if marker and marker.encode() in source:
        return "migration"
    if any(under(path, p) for p in rules.get("example", [])):
        return "example"
    return "none"


def fail(error):
    raise error  # an unreadable folder would otherwise drop its files silently (CONTRACTS.md rule 9)


def files(repo_dir, scanner):
    """[(path, file_kind)] in a fixed order. .git and skip_paths are never read. Files only, never a folder x.py."""
    skip = scanner.get("skip_paths", [])
    embedded = scanner.get("embedded_python", [])
    found = []
    for top, dirs, names in os.walk(repo_dir, onerror=fail):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            rel = (Path(top) / name).relative_to(repo_dir).as_posix()
            if skipped(rel, skip):
                continue
            if name.endswith(".py"):
                found.append((rel, "py"))
            elif any(fnmatchcase(name, e) for e in embedded):
                found.append((rel, "embedded"))
    return sorted(found)


def walk(node):
    stack = [node]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def char_col(source, node):
    """Column in characters, which is what Jedi counts. Tree-sitter counts bytes. Same as meridian.resolve."""
    start = node.start_byte - node.start_point[1]
    return len(source[start:node.start_byte].decode("utf-8", errors="replace"))


def first_error(root):
    for node in walk(root):
        if node.type == "ERROR" or node.is_missing:
            what = f"missing {node.type}" if node.is_missing else "syntax error"
            return f"{what} at line {node.start_point[0] + 1}, byte col {node.start_point[1]}"
    return "syntax error"


def same(a, b):
    return a is not None and b is not None and a.id == b.id


def name_only(node):
    """A keyword-argument name or a parameter name: never a use of the tracked function."""
    parent = node.parent
    if parent.type == "keyword_argument" and same(parent.child_by_field_name("name"), node):
        return True
    if parent.type in ("parameters", "lambda_parameters"):
        return True
    if parent.type in ("default_parameter", "typed_default_parameter") and same(parent.child_by_field_name("name"), node):
        return True
    if parent.type == "typed_parameter" and parent.children and same(parent.children[0], node):
        return True
    if parent.type in ("list_splat_pattern", "dictionary_splat_pattern") and parent.parent is not None \
            and parent.parent.type in ("parameters", "lambda_parameters"):
        return True
    return False


def role(node):
    up = node
    while up is not None:
        if up.type in ("import_statement", "import_from_statement"):
            return "import"
        up = up.parent
    parent = node.parent
    if parent.type in ("function_definition", "class_definition") and same(parent.child_by_field_name("name"), node):
        return "definition"
    if parent.type == "call" and same(parent.child_by_field_name("function"), node):
        return "call"
    if parent.type == "attribute" and same(parent.child_by_field_name("attribute"), node):
        grand = parent.parent
        if grand is not None and grand.type == "call" and same(grand.child_by_field_name("function"), parent):
            return "call"
        return "attribute"
    while parent.type in ("assignment", "pattern_list", "expression_list"):
        if parent.type == "assignment":
            if same(parent.child_by_field_name("right"), node):
                return "alias"
            return "definition"   # a left side, possibly one of several: gettext_lazy = ugettext_lazy = ...
        node, parent = parent, parent.parent
    return "reference"


def opens(up, child):
    """Is up a def/class whose body is child? Only its body runs in its scope."""
    return up.type in DEFS and same(up.child_by_field_name("body"), child)


def scopes(node):
    """The defs/classes whose body holds node, innermost first. Shared with meridian.resolve."""
    chain, child, up = [], node, node.parent
    while up is not None:
        if opens(up, child):
            chain.append(up)
        child, up = up, up.parent
    return chain


def scope(node):
    names = [d.child_by_field_name("name").text.decode("utf-8", errors="replace") for d in scopes(node)]
    return ".".join(reversed(names)) or "<module>"


def in_except(node):
    """1 inside an except or finally block, at any depth, of node's own function (or module)."""
    child, up = node, node.parent
    while up is not None and not opens(up, child):
        if up.type in ("except_clause", "except_group_clause", "finally_clause"):  # except* too
            return 1
        child, up = up, up.parent
    return 0


def text(node):
    return node.text.decode("utf-8", errors="replace")


def dotted(node):
    return ".".join(text(c) for c in node.children if c.type == "identifier")


def imports(path, nodes):
    """{(line, module, name)} for every import statement. Relative imports resolve against path's folders."""
    package, found = path.split("/")[:-1], set()
    for node in nodes:
        line = node.start_point[0] + 1
        if node.type == "import_statement":
            for item in node.children_by_field_name("name"):
                found.add((line, dotted(item.child_by_field_name("name") if item.type == "aliased_import" else item), ""))
        elif node.type in ("import_from_statement", "future_import_statement"):
            module = node.child_by_field_name("module_name")
            if module is None:
                module = "__future__"
            elif module.type == "relative_import":
                dots = module.children[0].text.count(b".")
                if dots > len(package):  # climbs above the repo root: kept as written ("..a"), never a made-up top-level name
                    module = text(module)
                else:
                    module = ".".join(package[:len(package) - dots + 1]
                                      + [dotted(c) for c in module.children if c.type == "dotted_name"])
            else:
                module = dotted(module)
            for item in node.children_by_field_name("name"):
                found.add((line, module, dotted(item.child_by_field_name("name") if item.type == "aliased_import" else item)))
            if any(c.type == "wildcard_import" for c in node.children):
                found.add((line, module, "*"))
    return found


def matched(s, patterns, whole):
    """The text of the first [scanner] deprecation_markers regex that matches: all of a name, anywhere in a message."""
    for p in patterns:
        if m := (re.fullmatch if whole else re.search)(p, s):
            return m.group(0)
    return None


def module_of(path):
    """'django/utils/translation/__init__.py' -> 'django.utils.translation', 'a/b.py' -> 'a.b': the name Jedi reports."""
    parts = path[:-3].split("/") if path.endswith(".py") else path.split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def qualname(path, scope_):
    """The dotted name a call to the deprecated thing resolves to: K.__init__ is reached by calling K."""
    s = scope_.removesuffix(".__init__")
    return ".".join(p for p in (module_of(path), "" if s == "<module>" else s) if p)


def direct(node):
    """A statement straight in its def's (or the module's) body: runs on every call, not only under a condition."""
    stmt = node.parent
    return stmt.type == "expression_statement" and (
        stmt.parent.type == "module" or (stmt.parent.type == "block" and opens(stmt.parent.parent, stmt.parent)))


def strings(node):
    """The literal text inside a message argument, never its code: self.deprecated_msg is a name, not a message."""
    return " ".join(text(n) for n in walk(node) if n.type == "string_content")


def deprecations(nodes, patterns):
    """{line: (scope, marker)}: warn() calls and decorators that mark a def/class deprecated. First one per line.
    A warn() counts only as a direct statement of its body: `if x is not None: warn(...)` deprecates an argument."""
    found = {}
    for node in nodes:
        marker = target = None
        if node.type == "call" and node.child_by_field_name("function").text in (b"warn", b"warnings.warn") and direct(node):
            args = node.child_by_field_name("arguments").named_children
            pos = [a for a in args if a.type not in ("keyword_argument", "comment")]
            kw = {a.child_by_field_name("name").text: a.child_by_field_name("value") for a in args if a.type == "keyword_argument"}
            category = kw.get(b"category", pos[1] if len(pos) > 1 else None)
            message = kw.get(b"message", pos[0] if pos else None)
            marker = (category is not None and matched(text(category).rsplit(".", 1)[-1], patterns, True)) or \
                     (message is not None and matched(strings(message), patterns, False))
            target = node
        elif node.type == "decorator" and node.parent.type == "decorated_definition":
            expr = node.named_children[0]
            name = text(expr.child_by_field_name("function") if expr.type == "call" else expr)
            marker = matched(name.rsplit(".", 1)[-1], patterns, True)
            target = node.parent.child_by_field_name("definition").child_by_field_name("body")
        if marker:
            found.setdefault(node.start_point[0] + 1, (scope(target), marker))
    return found


def scan_repo(run_id, repo, repo_dir, symbols, scanner):
    """(candidate rows, parse_failure rows, counts, module_import rows, deprecation rows) for one repo."""
    rows, failures, imported, deprecated, counts = [], [], [], [], Counter()
    rules = scanner.get("junk", {})
    for rel, kind in files(repo_dir, scanner):
        source = (repo_dir / rel).read_bytes()
        tree = PY.parse(source)
        nodes = list(walk(tree.root_node))
        names = [n for n in nodes if n.type == "identifier" and n.text.decode("utf-8", errors="replace") in symbols]
        counts["files"] += 1
        if tree.root_node.has_error:
            failures.append(dict(zip(FAIL_COLUMNS, [run_id, repo, rel, first_error(tree.root_node)])))
            counts["names_in_failed_files"] += len(names)
            continue
        test = int(is_test(rel, rules))
        imported += [dict(zip(IMPORT_COLUMNS, [run_id, repo, rel, *i, test])) for i in sorted(imports(rel, nodes))]
        deprecated += [dict(zip(DEPRECATION_COLUMNS, [run_id, repo, rel, line, sc, qualname(rel, sc), marker]))
                       for line, (sc, marker) in sorted(deprecations(nodes, scanner["deprecation_markers"]).items())]
        if not names:
            continue
        lines = source.decode("utf-8", errors="replace").split("\n")
        label = junk(rel, source, rules)
        for node in names:
            if name_only(node):
                counts["name_only"] += 1
                if not scanner["keep_name_only"]:
                    continue
            line = node.start_point[0] + 1
            rows.append(dict(zip(COLUMNS, [run_id, repo, rel, line, char_col(source, node),
                                           node.text.decode("utf-8", errors="replace"), role(node), scope(node),
                                           lines[line - 1].strip(), label, kind, in_except(node)])))
    rows.sort(key=lambda r: (r["path"], r["line"], r["col"]))
    return rows, failures, counts, imported, deprecated


def head(repo_dir):
    return git(repo_dir, "rev-parse", "HEAD").strip()


def edit_lines(rows, file_kind, roles):
    """Lines someone must edit: rows whose role is in [scanner] edit_roles (the Django 265)."""
    return {(r["path"], r["line"]) for r in rows if r["role"] in roles and r["file_kind"] == file_kind}


def run(run_id, cfg, root=None):
    snap = Path(root or data_root()) / "snapshots" / run_id
    manifest_path = snap / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"no snapshot: {manifest_path} is missing. Run meridian.collect first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wanted, keys = tracked(cfg), answer_keys(cfg)
    scanner = cfg.get("scanner", {})
    tables = {t: [] for t in KEYS}
    for repo, sha in sorted(manifest["repos"].items()):
        if repo not in wanted:
            continue
        repo_dir = snap / "repos" / repo.replace("/", "__")
        if head(repo_dir) != sha:
            raise SystemExit(f"{repo}: checkout is at {head(repo_dir)!r}, the manifest says {sha}")
        got, failed, counts, imported, deprecated = scan_repo(run_id, repo, repo_dir, wanted[repo], scanner)
        log(f"{repo}: {len(got)} candidates in {counts['files']} files · "
            f"roles {dict(sorted(Counter(r['role'] for r in got).items()))} · "
            f"junk {dict(sorted(Counter(r['junk'] for r in got).items()))} · {sum(r['in_except'] for r in got)} in except")
        log(f"  {len(failed)} parse failures (holding {counts['names_in_failed_files']} tracked names, excluded) · "
            f"{counts['name_only']} keyword-argument/parameter names "
            f"({'kept as reference' if scanner['keep_name_only'] else 'not emitted'})")
        log(f"  {len(imported)} module imports · {len(deprecated)} deprecation marks")
        if repo in keys:
            key = keys[repo]
            py, emb = (len(edit_lines(got, kind, scanner["edit_roles"])) for kind in ("py", "embedded"))
            log(f"  answer key: {py} edit lines in .py (key {key.get('edit_lines')}) · "
                f"{emb} outside .py (key {key.get('outside_py')})")
        for t, part in zip(KEYS, (got, failed, imported, deprecated)):
            tables[t] += part
    out = handoff(cfg, run_id, root)
    out.mkdir(parents=True, exist_ok=True)
    for t, key in KEYS.items():
        write_jsonl(out / f"{t}.jsonl", tables[t], key)
    log(" · ".join(f"{len(tables[t])} {t}" for t in KEYS) + f" -> {out}")
    return tables["candidate"], tables["parse_failure"]


def main(argv=None):
    a = parser(__doc__).parse_args(argv)
    run(a.run_id, load_config(a.config))


if __name__ == "__main__":
    main()
