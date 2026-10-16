"""Resolve · step 1 · D4: what each name really is, starting with the alias calls the scanner can't see. See CONTRACTS.md.

    python -m meridian.resolve --run-id <run_id> --config meridian.toml

Reads the code in snapshots/<run_id>/ (every repo in the manifest whose dataset lists symbols).
Writes out/<run_id>/resolution.jsonl, one row per alias call, sorted by primary key, for the writer.

Alias calls. The scanner finds old names. It can't see a call made through a second name:
    from django.utils.translation import ugettext_lazy as _     ->  via = 'import_alias'
    _ = ugettext_lazy                                            ->  via = 'assign_alias'
    _("Hello")                                                   <-  the call, a new row
For every call to a name bound like that in its file, Jedi (pinned) is asked where the name leads
(goto, following imports). Jedi stops at a plain `_ = ugettext_lazy`, so the one-hop rule
([resolver] one_hop_assignment) follows the right side once more. An answer counts only if it lands on
the old name itself ([resolver] score_on_old_name): ugettext_lazy, never gettext_lazy, which the old name
is a second name for.

    ast_resolved    lands on a tracked symbol
    ast_unresolved  Jedi has no answer. Kept, never dropped silently ([resolver] keep_unresolved)
    dropped         lands on something else, e.g. a `_` that a function redefines

Environment. One jedi.Project per repo, on the interpreter running this part, so the result doesn't depend
on which virtualenv happens to be active.
"""
import json, subprocess, sys
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path

import jedi
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from meridian.common import data_root, handoff, load_config, log, parser

RESOLVER = f"jedi=={jedi.__version__}"
COLUMNS = ["run_id", "repo", "path", "line", "col", "symbol", "detection", "via", "resolved_to", "resolver"]
PY = Parser(Language(tspython.language()))


def tracked(cfg):
    """{repo: {old name: module}} for every dataset in meridian.toml that lists symbols."""
    out = {}
    for ds in cfg.get("datasets", {}).values():
        symbols = {s["name"]: s["module"] for s in ds.get("symbols", [])}
        for r in ds.get("repos", []) if symbols else []:
            out[r["repo"]] = symbols
    return out


def skipped(path, patterns):
    """Same paths the scanner skips ([scanner] skip_paths): 'docs/' is a prefix, '*.po' a pattern."""
    return any(path.startswith(p) if p.endswith("/") else fnmatch(path, p) for p in patterns)


def py_files(repo_dir, patterns):
    files = []
    for p in repo_dir.rglob("*.py"):
        rel = p.relative_to(repo_dir).as_posix()
        if ".git/" not in rel + "/" and not skipped(rel, patterns):
            files.append(rel)
    return sorted(files)


def walk(node):
    stack = [node]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def char_col(source, node):
    """Column in characters, which is what Jedi counts. Tree-sitter counts bytes."""
    start = node.start_byte - node.start_point[1]
    return len(source[start:node.start_byte].decode("utf-8", errors="replace"))


def bindings(root, olds):
    """{alias: sorted [(old name, via)]}: every second name this file gives an old name."""
    found = {}
    for node in walk(root):
        if node.type == "import_from_statement":
            for item in node.children_by_field_name("name"):
                if item.type == "aliased_import":
                    real = item.child_by_field_name("name").text.decode()
                    alias = item.child_by_field_name("alias").text.decode()
                    if real in olds and alias != real:
                        found.setdefault(alias, set()).add((real, "import_alias"))
        elif node.type == "assignment":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is not None and right is not None and left.type == "identifier" and right.type == "identifier":
                if right.text.decode() in olds:
                    found.setdefault(left.text.decode(), set()).add((right.text.decode(), "assign_alias"))
    return {alias: sorted(b) for alias, b in found.items()}


def alias_calls(root, source, binds):
    """[(line, col, alias)] for every call `alias(...)` in the file. Jedi decides later which binding applies."""
    calls = []
    for node in walk(root):
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and fn.text.decode() in binds:
                calls.append((fn.start_point[0] + 1, char_col(source, fn), fn.text.decode()))
    return sorted(calls)


class Repo:
    """One repo of the snapshot: its own jedi.Project, and every file parsed at most once."""

    def __init__(self, repo_dir, symbols):
        self.dir = repo_dir
        self.symbols = symbols                                        # {old name: module}
        self.targets = {f"{m}.{n}": n for n, m in symbols.items()}   # full name -> old name
        self.env = jedi.create_environment(sys.executable, safe=False)
        self.project = jedi.Project(repo_dir)
        self.scripts, self.trees = {}, {}

    def source(self, path):
        return (self.dir / path).read_bytes()

    def tree(self, path):
        if path not in self.trees:
            src = self.source(path)
            self.trees[path] = (src, PY.parse(src).root_node)
        return self.trees[path]

    def goto(self, path, line, col):
        if path not in self.scripts:
            self.scripts[path] = jedi.Script(code=self.source(path).decode("utf-8", errors="replace"),
                                             path=str(self.dir / path), project=self.project, environment=self.env)
        return self.scripts[path].goto(line, col, follow_imports=True)

    def where(self, name):
        """A Jedi answer as (repo-relative path, line, col), or None when it's outside the repo."""
        try:
            path = Path(name.module_path).resolve().relative_to(self.dir.resolve()).as_posix()
        except (TypeError, ValueError):
            return None
        return path, name.line, name.column

    def one_hop(self, name):
        """If Jedi stopped at `alias = old_name`, where does old_name lead? Otherwise None."""
        spot = self.where(name)
        if spot is None or spot[1] is None:
            return None
        path, line, col = spot
        src, root = self.tree(path)
        text = src.split(b"\n")[line - 1].decode("utf-8", errors="replace")
        point = (line - 1, len(text[:col].encode()))
        node = root.descendant_for_point_range(point, point)
        assign = node.parent if node is not None else None
        if node is None or node.type != "identifier" or assign is None or assign.type != "assignment":
            return None
        left, right = assign.child_by_field_name("left"), assign.child_by_field_name("right")
        if left is None or left.id != node.id or right is None or right.type != "identifier":
            return None
        if right.text.decode() not in self.symbols:
            return None
        return self.goto(path, right.start_point[0] + 1, char_col(src, right))

    def resolve(self, path, line, col):
        """(detection, how, resolved_to) for the name at path:line:col. how: 'direct', 'one_hop', or None."""
        try:
            names = self.goto(path, line, col)
        except Exception as e:  # a crash in Jedi is a missing answer, not a missing row
            log(f"  jedi failed at {path}:{line}:{col}: {type(e).__name__}: {e}")
            names = []
        if not names:
            return "ast_unresolved", None, None
        hits = sorted({n.full_name for n in names if n.full_name in self.targets})
        if hits:
            return "ast_resolved", "direct", hits[0]
        for n in names:
            hop = [h.full_name for h in (self.one_hop(n) or []) if h.full_name in self.targets]
            if hop:
                return "ast_resolved", "one_hop", sorted(hop)[0]
        first = names[0]
        spot = self.where(first)
        return "dropped", None, first.full_name or (f"{spot[0]}:{spot[1]}" if spot else first.name)


def resolve_aliases(run_id, repo, repo_dir, symbols, skip=()):
    """One resolution row per alias call in the repo."""
    r = Repo(repo_dir, symbols)
    rows = []
    for path in py_files(repo_dir, skip):
        src, root = r.tree(path)
        binds = bindings(root, set(symbols))
        if not binds:
            continue
        for line, col, alias in alias_calls(root, src, binds):
            detection, how, resolved_to = r.resolve(path, line, col)
            if detection == "ast_resolved":
                via = "assign_alias" if how == "one_hop" else "import_alias"
                symbol = r.targets[resolved_to]
            else:  # say which binding the file made, so the row still explains itself
                symbol, via = next((b for b in binds[alias] if b[1] == "import_alias"), binds[alias][0])
            rows.append(dict(zip(COLUMNS, [run_id, repo, path, line, col, symbol, detection, via, resolved_to, RESOLVER])))
    return rows


def head(repo_dir):
    return subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def write_jsonl(path, rows):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(path)


def run(run_id, cfg, root=None):
    snap = Path(root or data_root()) / "snapshots" / run_id
    manifest_path = snap / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"no snapshot: {manifest_path} is missing. Run meridian.collect first")
    manifest = json.loads(manifest_path.read_text())
    wanted = tracked(cfg)
    skip = cfg.get("scanner", {}).get("skip_paths", [])
    rows = []
    for repo, sha in sorted(manifest["repos"].items()):
        if repo not in wanted:
            continue
        repo_dir = snap / "repos" / repo.replace("/", "__")
        if head(repo_dir) != sha:
            raise SystemExit(f"{repo}: checkout is at {head(repo_dir)}, the manifest says {sha}")
        found = resolve_aliases(run_id, repo, repo_dir, wanted[repo], skip)
        c = Counter(x["detection"] for x in found)
        log(f"{repo}: {len(found)} alias calls · ast_resolved {c['ast_resolved']} · "
            f"ast_unresolved {c['ast_unresolved']} · dropped {c['dropped']} · "
            f"via {dict(sorted(Counter(x['via'] for x in found).items()))}")
        rows += found
    rows.sort(key=lambda x: (x["repo"], x["path"], x["line"], x["col"]))
    out = handoff(cfg, run_id, root)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "resolution.jsonl", rows)
    log(f"{len(rows)} resolution rows -> {out / 'resolution.jsonl'} ({RESOLVER})")
    return rows


def main(argv=None):
    a = parser(__doc__).parse_args(argv)
    run(a.run_id, load_config(a.config))


if __name__ == "__main__":
    main()
