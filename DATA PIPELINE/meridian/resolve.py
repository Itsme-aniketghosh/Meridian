"""Resolve · step 1 · D4–D6: what each name really is, and who calls it. See CONTRACTS.md.

    python -m meridian.resolve --run-id <run_id> --config meridian.toml

Reads the code in snapshots/<run_id>/ (every repo in the manifest whose dataset lists symbols) and Rachna's
out/<run_id>/candidate.jsonl. Writes, sorted by primary key, for the writer:
    out/<run_id>/resolution.jsonl   one row per candidate (same key), plus one per alias call the scanner can't see
    out/<run_id>/call_edge.jsonl    caller -> callee: every confirmed call of a tracked symbol, then up to
                                    [resolver] call_edge_depth levels of the callers of the functions holding one

Call edges (D6). Only calls Jedi confirms count, never imports. Depth 1 is a confirmed call of the old symbol
(caller's function -> the symbol); depth 2 is a resolved call of a function holding one; depth 3 one more level up.
Module and class bodies get their edges but nobody calls them, so the walk stops there. On the team run:
2,054 edges, about 76 s and 1.2 GB.

Candidates (D5). Jedi (pinned) is asked where each old name leads (goto, following imports). Scored on the old
name ([resolver] score_on_old_name): the row is ast_resolved only if it lands on the candidate's own symbol,
e.g. django.utils.translation.ugettext, never the gettext it is a second name for. via is 'reexport' when the
file imports the name from some other module that passes it on (guardian.compat), otherwise 'direct'.
A candidate whose line doesn't hold its symbol at its column is kept as ast_unresolved and logged.

Alias calls (D4). The scanner finds old names. It can't see a call made through a second name:
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

Environment. One jedi.Project per repo. Jedi runs inside this process (InterpreterEnvironment), not in a helper
subprocess, so the result doesn't depend on which virtualenv happens to be active. When a repo uses a tracked
module it doesn't contain (the packages use django.conf.urls), the snapshot repo that does contain it goes on that
project's path: the dependency comes from the same pinned snapshot, with nothing installed.

Same run, same bytes. Every question gets a fresh jedi.Script: with one Script per file, what Jedi had inferred for
earlier names changed a later answer from run to run (1 row in 3,451 on the packages). And parso never evicts during
a run (below): otherwise Jedi failed on names whose file had just been evicted, 30 on the packages, depending on what
was parsed before. Now repeated runs and any PYTHONHASHSEED give the same bytes. Parsing stays cached, so the
packages still take about 19 s, with a peak of about 780 MB.
"""
import json, subprocess
from collections import Counter
from fnmatch import fnmatch
from pathlib import Path

import jedi
import parso.cache
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from meridian.common import data_root, handoff, load_config, log, parser

# parso (pinned in requirements.txt) evicts parsed files from memory once it holds 600, judging age by the file's
# mtime. Every file in a fresh checkout looks old, so a file Jedi has just parsed can be evicted mid-question, and
# Jedi then fails with KeyError on that path. How often depends on what was parsed before, so it broke "same run,
# same bytes". Never evict during a run.
parso.cache._CACHED_SIZE_TRIGGER = float("inf")

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

    def __init__(self, repo_dir, symbols, extra_paths=(), one_hop=True, score_on_old_name=True):
        self.dir = repo_dir
        self.one_hop_rule = one_hop                                   # [resolver] one_hop_assignment
        self.score_on_old_name = score_on_old_name                    # [resolver] score_on_old_name
        self.symbols = symbols                                        # {old name: module}
        self.targets = {f"{m}.{n}": n for n, m in symbols.items()}   # full name -> old name
        self.env = jedi.InterpreterEnvironment()                      # no helper subprocess: see "Environment"
        self.project = jedi.Project(repo_dir, added_sys_path=[str(p) for p in extra_paths])
        self.code, self.trees = {}, {}

    def source(self, path):
        return (self.dir / path).read_bytes()

    def tree(self, path):
        if path not in self.trees:
            src = self.source(path)
            self.trees[path] = (src, PY.parse(src).root_node)
        return self.trees[path]

    def goto(self, path, line, col, follow_imports=True):
        """A fresh jedi.Script per question: what Jedi inferred for one name can't change its answer for the next."""
        if path not in self.code:
            self.code[path] = self.source(path).decode("utf-8", errors="replace")
        script = jedi.Script(code=self.code[path], path=str(self.dir / path), project=self.project, environment=self.env)
        return script.goto(line, col, follow_imports=follow_imports)

    def locate(self, path, line, col, symbol):
        """The character column where symbol starts, accepting a column in characters or bytes. None if neither."""
        lines = self.source(path).split(b"\n")
        if not 1 <= line <= len(lines):
            return None
        raw = lines[line - 1]
        text = raw.decode("utf-8", errors="replace")
        if text[col:col + len(symbol)] == symbol:
            return col
        if raw[col:col + len(symbol)] == symbol.encode():
            return len(raw[:col].decode("utf-8", errors="replace"))
        return None

    def import_module_at(self, path, line, col):
        """If line:col sits in a `from X import ...` statement, X. Otherwise None."""
        src, root = self.tree(path)
        text = src.split(b"\n")[line - 1].decode("utf-8", errors="replace")
        point = (line - 1, len(text[:col].encode()))
        node = root.descendant_for_point_range(point, point)
        while node is not None and node.type != "import_from_statement":
            node = node.parent
        return node.child_by_field_name("module_name").text.decode() if node is not None else None

    def imported_from(self, path, line, col):
        """The module this file imports the name at line:col from, or None if the file doesn't import it."""
        if module := self.import_module_at(path, line, col):  # the candidate is the import itself
            return module
        for n in self.ordered(self.goto(path, line, col, follow_imports=False)):
            spot = self.where(n)
            if spot is not None and spot[0] == path and spot[1] is not None:
                if module := self.import_module_at(path, spot[1], spot[2]):
                    return module
        return None

    def resolve_candidate(self, c):
        """(detection, via, resolved_to) for one candidate row, scored on its own old name."""
        col = self.locate(c["path"], c["line"], c["col"], c["symbol"])
        if col is None:
            log(f"  {c['path']}:{c['line']}:{c['col']} doesn't hold {c['symbol']}: kept as ast_unresolved")
            return "ast_unresolved", "none", None
        try:
            names = self.goto(c["path"], c["line"], col)
        except Exception as e:  # a crash in Jedi is a missing answer, not a missing row
            log(f"  jedi failed at {c['path']}:{c['line']}:{col}: {type(e).__name__}: {e}")
            names = []
        if not names:
            return "ast_unresolved", "none", None
        expected = f"{self.symbols[c['symbol']]}.{c['symbol']}"
        accepted = {expected} if self.score_on_old_name else set(self.targets)  # off: any tracked symbol counts
        if hits := sorted({n.full_name for n in names if n.full_name in accepted}):
            module = self.imported_from(c["path"], c["line"], col)
            return "ast_resolved", ("reexport" if module and module != self.symbols[c["symbol"]] else "direct"), hits[0]
        return "dropped", "none", self.first(names)

    def where(self, name):
        """A Jedi answer as (repo-relative path, line, col), or None when it's outside the repo."""
        try:
            path = Path(name.module_path).resolve().relative_to(self.dir.resolve()).as_posix()
        except (TypeError, ValueError):
            return None
        return path, name.line, name.column

    def describe(self, name):
        """What a Jedi answer points at, as text: its full name, else path:line in the repo, else its bare name."""
        spot = self.where(name)
        return name.full_name or (f"{spot[0]}:{spot[1]}" if spot else name.name)

    def ordered(self, names):
        """Jedi can list several answers in a different order each run. Always walk them in the same order."""
        return sorted(names, key=lambda n: (self.describe(n), str(n.module_path), n.line or 0, n.column or 0))

    def first(self, names):
        return self.describe(self.ordered(names)[0])

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
        for n in self.ordered(names) if self.one_hop_rule else []:
            hop = [h.full_name for h in (self.one_hop(n) or []) if h.full_name in self.targets]
            if hop:
                return "ast_resolved", "one_hop", sorted(hop)[0]
        return "dropped", None, self.first(names)


def resolve_candidates(run_id, repo, repo_dir, symbols, candidates, r=None):
    """One resolution row per candidate, with the candidate's key."""
    r = r or Repo(repo_dir, symbols)
    rows = []
    for c in candidates:
        if c["symbol"] not in symbols:
            raise SystemExit(f"{repo} {c['path']}:{c['line']}: candidate symbol {c['symbol']!r} isn't tracked in meridian.toml")
        detection, via, resolved_to = r.resolve_candidate(c)
        rows.append(dict(zip(COLUMNS, [run_id, repo, c["path"], c["line"], c["col"], c["symbol"], detection, via,
                                       resolved_to, RESOLVER])))
    return rows


def resolve_aliases(run_id, repo, repo_dir, symbols, skip=(), r=None):
    """One resolution row per alias call in the repo."""
    r = r or Repo(repo_dir, symbols)
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


def load_candidates(out, run_id):
    path = out / "candidate.jsonl"
    if not path.exists():
        raise SystemExit(f"no candidate table: {path} is missing. Run meridian.scan first")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if wrong := sorted({c["run_id"] for c in rows} - {run_id}):
        raise SystemExit(f"{path} has rows for run_id {wrong}, not {run_id!r}")
    return rows


def dependency_paths(snap, manifest, repo, symbols):
    """Snapshot repos that contain a tracked module this repo uses but doesn't have itself (packages -> django)."""
    paths = []
    for top in sorted({m.split(".")[0] for m in symbols.values()}):
        if (snap / "repos" / repo.replace("/", "__") / top / "__init__.py").exists():
            continue
        paths += [snap / "repos" / other.replace("/", "__") for other in sorted(manifest["repos"])
                  if other != repo and (snap / "repos" / other.replace("/", "__") / top / "__init__.py").exists()]
    return paths


EDGE_COLUMNS = ["run_id", "repo", "caller_path", "caller_scope", "line", "col", "callee_path", "callee_repo", "callee_scope"]


def scope_of(root, src, line, col):
    """(dotted scope, is it a function, line of that def) for the code at line:col. '<module>' at top level."""
    text = src.split(b"\n")[line - 1].decode("utf-8", errors="replace")
    point = (line - 1, len(text[:col].encode()))
    up = root.descendant_for_point_range(point, point)
    names, kind, def_line = [], None, None
    while up is not None:
        if up.type in ("function_definition", "class_definition"):
            name = up.child_by_field_name("name")
            names.append(name.text.decode())
            if kind is None:
                kind, def_line = up.type, name.start_point[0] + 1
        up = up.parent
    return ".".join(reversed(names)) or "<module>", kind == "function_definition", def_line


def call_sites(r, files):
    """{called name: [(path, line, col)]} for every call in the repo: f(...) and x.f(...)."""
    index = {}
    for path in files:
        src, root = r.tree(path)
        for node in walk(root):
            if node.type == "call":
                fn = node.child_by_field_name("function")
                if fn is not None and fn.type == "attribute":
                    fn = fn.child_by_field_name("attribute")
                if fn is not None and fn.type == "identifier":
                    index.setdefault(fn.text.decode(), []).append((path, fn.start_point[0] + 1, char_col(src, fn)))
    return index


def symbol_home(snap, repo_dir, deps, module):
    """(repo, path) of the file that defines a tracked module: this repo first, then its dependencies."""
    for base in [repo_dir, *deps]:
        stem = base / Path(*module.split("."))
        for p in (stem / "__init__.py", stem.with_suffix(".py")):
            if p.exists():
                return base.name.replace("__", "/", 1), p.relative_to(base).as_posix()
    return None, None


def call_edges(run_id, repo, r, uses, files, depth):
    """Resolved caller -> callee edges, from each confirmed use of a tracked symbol up through its callers.

    uses: [(path, line, col, callee_repo, callee_path, callee_scope)], the confirmed calls of tracked symbols (depth 1).
    Then the callers of every function holding one (depth 2), and their callers (depth 3, [resolver] call_edge_depth).
    A call counts only when Jedi lands on that very function's def. Module and class bodies get edges but have no
    callers, so they stop the walk. Imports never make edges.
    """
    edges, frontier, seen, answers = {}, {}, set(), {}

    def add(path, line, col, callee_repo, callee_path, callee_scope):
        src, root = r.tree(path)
        scope, is_function, def_line = scope_of(root, src, line, col)
        row = dict(zip(EDGE_COLUMNS, [run_id, repo, path, scope, line, col, callee_path, callee_repo, callee_scope]))
        edges[(path, line, col, callee_repo, callee_path, callee_scope)] = row
        if is_function and (path, def_line) not in seen:
            seen.add((path, def_line))
            frontier[(path, def_line)] = scope

    for use in uses:
        add(*use)
    index = None
    for _ in range(depth - 1):
        targets, frontier = sorted(frontier.items()), {}
        if not targets:
            break
        index = index if index is not None else call_sites(r, files)
        for (t_path, t_line), t_scope in targets:
            for site in index.get(t_scope.split(".")[-1], []):
                if site not in answers:  # one Jedi question per call site, however many functions share its name
                    try:
                        answers[site] = {r.where(n)[:2] for n in r.goto(*site) if r.where(n)}
                    except Exception as e:
                        log(f"  jedi failed at {site[0]}:{site[1]}:{site[2]}: {type(e).__name__}: {e}")
                        answers[site] = set()
                if (t_path, t_line) in answers[site]:
                    add(*site, repo, t_path, t_scope)
    return [edges[k] for k in sorted(edges)]


def summary(rows):
    c = Counter(x["detection"] for x in rows)
    return (f"ast_resolved {c['ast_resolved']} · ast_unresolved {c['ast_unresolved']} · dropped {c['dropped']} · "
            f"via {dict(sorted(Counter(x['via'] for x in rows).items()))}")


def run(run_id, cfg, root=None):
    snap = Path(root or data_root()) / "snapshots" / run_id
    manifest_path = snap / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"no snapshot: {manifest_path} is missing. Run meridian.collect first")
    manifest = json.loads(manifest_path.read_text())
    out = handoff(cfg, run_id, root)
    by_repo = {}
    for c in load_candidates(out, run_id):
        by_repo.setdefault(c["repo"], []).append(c)
    wanted = tracked(cfg)
    if stray := sorted(set(by_repo) - (set(manifest["repos"]) & set(wanted))):
        raise SystemExit(f"candidates for repos this run can't resolve (not in the snapshot, or no symbols): {stray}")
    skip = cfg.get("scanner", {}).get("skip_paths", [])
    settings = cfg.get("resolver", {})
    depth = settings.get("call_edge_depth", 3)
    if not settings.get("keep_unresolved", True):  # CONTRACTS.md rule 9: nothing is silently dropped
        raise SystemExit("[resolver] keep_unresolved = false would drop rows silently, which CONTRACTS.md rule 9 forbids")
    rules = dict(one_hop=settings.get("one_hop_assignment", True), score_on_old_name=settings.get("score_on_old_name", True))
    roles = {(c["repo"], c["path"], c["line"], c["col"]): c["role"] for cs in by_repo.values() for c in cs}
    rows, edges = [], []
    for repo, sha in sorted(manifest["repos"].items()):
        if repo not in wanted:
            continue
        repo_dir = snap / "repos" / repo.replace("/", "__")
        if head(repo_dir) != sha:
            raise SystemExit(f"{repo}: checkout is at {head(repo_dir)}, the manifest says {sha}")
        deps = dependency_paths(snap, manifest, repo, wanted[repo])
        r = Repo(repo_dir, wanted[repo], deps, **rules)
        cands = resolve_candidates(run_id, repo, repo_dir, wanted[repo], sorted(
            by_repo.get(repo, []), key=lambda c: (c["path"], c["line"], c["col"])), r)
        taken = {(x["path"], x["line"], x["col"]) for x in cands}
        aliases = [x for x in resolve_aliases(run_id, repo, repo_dir, wanted[repo], skip, r)
                   if (x["path"], x["line"], x["col"]) not in taken]  # a candidate already covers that spot
        log(f"{repo}{' (on the path: ' + ', '.join(p.name for p in deps) + ')' if deps else ''}")
        log(f"  {len(cands)} candidates · {summary(cands)}")
        log(f"  {len(aliases)} alias calls · {summary(aliases)}")
        rows += cands + aliases
        # call_edge: confirmed calls only (candidate role 'call', and every alias call), never imports
        uses = []
        for x in cands + aliases:
            is_call = roles.get((repo, x["path"], x["line"], x["col"]), "call") == "call"
            if x["detection"] == "ast_resolved" and is_call:
                module, name = x["resolved_to"].rsplit(".", 1)
                home_repo, home_path = symbol_home(snap, repo_dir, deps, module)
                if home_path:
                    uses.append((x["path"], x["line"], x["col"], home_repo, home_path, name))
        found = call_edges(run_id, repo, r, uses, py_files(repo_dir, skip), depth)
        log(f"  {len(found)} call edges ({len(uses)} from confirmed calls, {len(found) - len(uses)} from their callers)")
        edges += found
    rows.sort(key=lambda x: (x["repo"], x["path"], x["line"], x["col"]))
    edges.sort(key=lambda e: (e["repo"], e["caller_path"], e["line"], e["col"], e["callee_repo"], e["callee_path"], e["callee_scope"]))
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "resolution.jsonl", rows)
    write_jsonl(out / "call_edge.jsonl", edges)
    log(f"{len(rows)} resolution rows, {len(edges)} call edges -> {out} ({RESOLVER})")
    return rows


def main(argv=None):
    a = parser(__doc__).parse_args(argv)
    run(a.run_id, load_config(a.config))


if __name__ == "__main__":
    main()
