"""Scan · step 1 · D3–D4: every place a tracked old name appears in the code. See CONTRACTS.md.

    python -m meridian.scan --run-id <run_id> --config meridian.toml      # from DATA PIPELINE/

Reads the code in snapshots/<run_id>/ (every repo in the manifest whose dataset lists symbols). Writes, sorted by
primary key, for the writer:
    out/<run_id>/candidate.jsonl       one row per identifier that is a tracked old name (schema.sql, candidate)
    out/<run_id>/parse_failure.jsonl   one row per file tree-sitter can't parse cleanly (schema.sql, parse_failure)

Grew out of week 1's scan_sites.py and Rochan's tests/standin_candidates.py (same roles, scope and columns).

Files. Every .py file, plus Python outside .py ([scanner] embedded_python, e.g. code.sample) as file_kind
'embedded'. [scanner] skip_paths are never read. .git is never read.

Candidates. Tree-sitter (pinned) finds every identifier whose text is a tracked old name. Comments and strings are
never identifiers, so they never become rows. Role, by where the identifier sits:
    import       anywhere inside an import statement, including a parenthesised import's continuation lines
    call         ugettext(...) or translation.ugettext(...)
    alias        right side of =, e.g. _ = ugettext_lazy
    definition   name of a def or class, or a left side of = (ugettext = gettext)
    attribute    obj.ugettext, not called
    reference    anything else: passed as a value, a keyword-argument or parameter name, ...
col counts characters, like Jedi (tree-sitter counts bytes). scope is the dotted enclosing def/class names.

Junk ([scanner] junk). test: a path segment from `test` (tests/ anywhere in the path) or a file-name pattern.
migration: the file holds migration_marker. example: a path segment from `example`. Else 'none'.
Junk is a label only: junk rows are kept.

Parse failures. A file whose tree has an ERROR or MISSING node is excluded and becomes a parse_failure row
(CONTRACTS.md rule 9: nothing is silently dropped). The log says how many tracked names those files held.

Same run, same bytes: files and rows in a fixed order, no clock, nothing random.
"""
import json, os, subprocess
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from meridian.common import data_root, handoff, load_config, log, parser

PY = Parser(Language(tspython.language()))
COLUMNS = ["run_id", "repo", "path", "line", "col", "symbol", "role", "scope", "text", "junk", "file_kind"]
FAIL_COLUMNS = ["run_id", "repo", "path", "error"]
EDIT_ROLES = ("import", "call", "alias")   # the roles that make a line someone must edit (the Django 265)

# Keyword-argument and parameter names (url='/x/', def view(request, url)) can never be a use of the tracked
# function. Kept as 'reference' for now, to match the stand-in the resolver was built on. Counted in the log.
KEEP_NAME_ONLY = True


def tracked(cfg):
    """{repo: {old name: module}} for every dataset in meridian.toml that lists symbols (same as meridian.resolve)."""
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
    """Same rule as meridian.resolve: 'docs/' is a prefix, '*.po' a pattern."""
    return any(path.startswith(p) if p.endswith("/") else fnmatch(path, p) for p in patterns)


def under(path, pattern):
    """'tests/' matches a path segment anywhere (tests/x.py, wagtail/admin/tests/x.py). 'test_*.py' a file name."""
    if pattern.endswith("/"):
        return ("/" + pattern) in ("/" + path)
    return fnmatch(path.rsplit("/", 1)[-1], pattern)


def junk(path, source, rules):
    if any(under(path, p) for p in rules.get("test", [])):
        return "test"
    marker = rules.get("migration_marker")
    if marker and marker.encode() in source:
        return "migration"
    if any(under(path, p) for p in rules.get("example", [])):
        return "example"
    return "none"


def files(repo_dir, scanner):
    """[(path, file_kind)] in a fixed order. .git and skip_paths are never read."""
    skip = scanner.get("skip_paths", [])
    embedded = scanner.get("embedded_python", [])
    found = []
    for top, dirs, names in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            rel = (Path(top) / name).relative_to(repo_dir).as_posix()
            if skipped(rel, skip):
                continue
            if name.endswith(".py"):
                found.append((rel, "py"))
            elif any(fnmatch(name, e) for e in embedded):
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
    """Same rules as tests/standin_candidates.py."""
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


def scope(node):
    names = []
    up = node.parent
    while up is not None:
        if up.type in ("function_definition", "class_definition"):
            names.append(up.child_by_field_name("name").text.decode("utf-8", errors="replace"))
        up = up.parent
    return ".".join(reversed(names)) or "<module>"


def scan_repo(run_id, repo, repo_dir, symbols, scanner):
    """(candidate rows, parse_failure rows, counts) for one repo."""
    rows, failures, counts = [], [], Counter()
    rules = scanner.get("junk", {})
    for rel, kind in files(repo_dir, scanner):
        source = (repo_dir / rel).read_bytes()
        tree = PY.parse(source)
        names = [n for n in walk(tree.root_node)
                 if n.type == "identifier" and n.text.decode("utf-8", errors="replace") in symbols]
        counts["files"] += 1
        if tree.root_node.has_error:
            failures.append(dict(zip(FAIL_COLUMNS, [run_id, repo, rel, first_error(tree.root_node)])))
            counts["names_in_failed_files"] += len(names)
            continue
        if not names:
            continue
        lines = source.decode("utf-8", errors="replace").split("\n")
        label = junk(rel, source, rules)
        for node in names:
            if name_only(node):
                counts["name_only"] += 1
                if not KEEP_NAME_ONLY:
                    continue
            line = node.start_point[0] + 1
            rows.append(dict(zip(COLUMNS, [run_id, repo, rel, line, char_col(source, node),
                                           node.text.decode("utf-8", errors="replace"), role(node), scope(node),
                                           lines[line - 1].strip(), label, kind])))
    rows.sort(key=lambda r: (r["path"], r["line"], r["col"]))
    return rows, failures, counts


def head(repo_dir):
    return subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def write_jsonl(path, rows):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def edit_lines(rows, file_kind):
    return {(r["path"], r["line"]) for r in rows if r["role"] in EDIT_ROLES and r["file_kind"] == file_kind}


def run(run_id, cfg, root=None):
    snap = Path(root or data_root()) / "snapshots" / run_id
    manifest_path = snap / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"no snapshot: {manifest_path} is missing. Run meridian.collect first")
    manifest = json.loads(manifest_path.read_text())
    wanted, keys = tracked(cfg), answer_keys(cfg)
    scanner = cfg.get("scanner", {})
    rows, failures = [], []
    for repo, sha in sorted(manifest["repos"].items()):
        if repo not in wanted:
            continue
        repo_dir = snap / "repos" / repo.replace("/", "__")
        if head(repo_dir) != sha:
            raise SystemExit(f"{repo}: checkout is at {head(repo_dir)!r}, the manifest says {sha}")
        got, failed, counts = scan_repo(run_id, repo, repo_dir, wanted[repo], scanner)
        log(f"{repo}: {len(got)} candidates in {counts['files']} files · "
            f"roles {dict(sorted(Counter(r['role'] for r in got).items()))} · "
            f"junk {dict(sorted(Counter(r['junk'] for r in got).items()))}")
        log(f"  {len(failed)} parse failures (holding {counts['names_in_failed_files']} tracked names, excluded) · "
            f"{counts['name_only']} keyword-argument/parameter names "
            f"({'kept as reference' if KEEP_NAME_ONLY else 'not emitted'})")
        if repo in keys:
            key = keys[repo]
            py, emb = len(edit_lines(got, "py")), len(edit_lines(got, "embedded"))
            log(f"  answer key: {py} edit lines in .py (key {key.get('edit_lines')}) · "
                f"{emb} outside .py (key {key.get('outside_py')})")
        rows += got
        failures += failed
    rows.sort(key=lambda r: (r["repo"], r["path"], r["line"], r["col"]))
    failures.sort(key=lambda r: (r["repo"], r["path"]))
    out = handoff(cfg, run_id, root)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "candidate.jsonl", rows)
    write_jsonl(out / "parse_failure.jsonl", failures)
    log(f"{len(rows)} candidate rows, {len(failures)} parse failures -> {out}")
    return rows, failures


def main(argv=None):
    a = parser(__doc__).parse_args(argv)
    run(a.run_id, load_config(a.config))


if __name__ == "__main__":
    main()
