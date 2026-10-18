"""Stand-in for Rachna's `candidate` table, so the resolver can be built and scored before meridian.scan lands.
Delete it once out/<run_id>/candidate.jsonl comes from the real scanner.

    python -m tests.standin_candidates --run-id dev --config meridian.toml     # from DATA PIPELINE/

Same idea as week 1's scan_sites.py, but one row per identifier and in the contract's shape (schema.sql, candidate):
every identifier that is a tracked old name, with its role, enclosing scope, junk label, and file kind. Comments and
strings are never identifiers, so they never become rows. Columns count characters, like Jedi.
"""
import json
from fnmatch import fnmatch
from pathlib import Path

from meridian.common import data_root, handoff, load_config, log, parser
from meridian.resolve import PY, char_col, skipped, tracked, walk, write_jsonl

COLUMNS = ["run_id", "repo", "path", "line", "col", "symbol", "role", "scope", "text", "junk", "file_kind"]


def role(node):
    up = node
    while up is not None:
        if up.type in ("import_statement", "import_from_statement"):
            return "import"
        up = up.parent
    parent = node.parent
    if parent.type in ("function_definition", "class_definition") and parent.child_by_field_name("name").id == node.id:
        return "definition"
    if parent.type == "call" and parent.child_by_field_name("function").id == node.id:
        return "call"
    if parent.type == "attribute" and parent.child_by_field_name("attribute").id == node.id:
        grand = parent.parent
        if grand is not None and grand.type == "call" and grand.child_by_field_name("function").id == parent.id:
            return "call"
        return "attribute"
    while parent.type in ("assignment", "pattern_list", "expression_list"):
        if parent.type == "assignment":
            if parent.child_by_field_name("right") is not None and parent.child_by_field_name("right").id == node.id:
                return "alias"
            return "definition"  # left side, possibly one of several targets: gettext_lazy = ugettext_lazy = ...
        node, parent = parent, parent.parent
    return "reference"


def scope(node):
    names = []
    up = node.parent
    while up is not None:
        if up.type in ("function_definition", "class_definition"):
            names.append(up.child_by_field_name("name").text.decode())
        up = up.parent
    return ".".join(reversed(names)) or "<module>"


def junk(path, source, rules):
    if any(path.startswith(p) if p.endswith("/") else fnmatch(Path(path).name, p) for p in rules.get("test", [])):
        return "test"
    if rules.get("migration_marker", "\0").encode() in source:
        return "migration"
    return "none"


def candidates(run_id, repo, repo_dir, symbols, scanner):
    skip = scanner.get("skip_paths", [])
    embedded = scanner.get("embedded_python", [])
    files = []
    for p in sorted(repo_dir.rglob("*")):
        rel = p.relative_to(repo_dir).as_posix()
        if not p.is_file() or rel.startswith(".git/") or skipped(rel, skip):
            continue
        if rel.endswith(".py"):
            files.append((rel, "py"))
        elif any(fnmatch(p.name, e) for e in embedded):
            files.append((rel, "embedded"))
    rows = []
    for rel, kind in files:
        source = (repo_dir / rel).read_bytes()
        lines = source.decode("utf-8", errors="replace").split("\n")
        for node in walk(PY.parse(source).root_node):
            if node.type == "identifier" and node.text.decode() in symbols:
                line = node.start_point[0] + 1
                rows.append(dict(zip(COLUMNS, [run_id, repo, rel, line, char_col(source, node), node.text.decode(),
                                               role(node), scope(node), lines[line - 1].strip(),
                                               junk(rel, source, scanner.get("junk", {})), kind])))
    return sorted(rows, key=lambda r: (r["repo"], r["path"], r["line"], r["col"]))


def main(argv=None):
    a = parser(__doc__).parse_args(argv)
    cfg = load_config(a.config)
    snap = data_root() / "snapshots" / a.run_id
    manifest = json.loads((snap / "manifest.json").read_text())
    rows = []
    for repo, symbols in sorted(tracked(cfg).items()):
        if repo in manifest["repos"]:
            rows += candidates(a.run_id, repo, snap / "repos" / repo.replace("/", "__"), symbols, cfg.get("scanner", {}))
    out = handoff(cfg, a.run_id)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "candidate.jsonl", rows)
    log(f"stand-in: {len(rows)} candidate rows -> {out / 'candidate.jsonl'}")


if __name__ == "__main__":
    main()
