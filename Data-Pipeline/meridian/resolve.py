"""Resolve · step 1 · D4–D6: what each name really is, and who calls it. See CONTRACTS.md.

    python -m meridian.resolve --run-id <run_id> --config meridian.toml

Reads the code in snapshots/<run_id>/ (every repo in the manifest whose dataset lists symbols) and Rachna's
out/<run_id>/candidate.jsonl. Writes, sorted by primary key, for the writer:
    out/<run_id>/resolution.jsonl   one row per candidate (same key), plus one per alias call the scanner can't see
    out/<run_id>/call_edge.jsonl    caller -> callee: every confirmed call of a tracked symbol, then up to
                                    [resolver] call_edge_depth levels of the callers of the functions holding one

Call edges (D6). Only calls Jedi confirms count, never imports. Depth 1 is a confirmed call of the old symbol
(caller's function -> the symbol); depth 2 is a resolved call of a function holding one; depth 3 one more level up.
Module and class bodies get their edges but nobody calls them, so the walk stops there. A call to a class counts as
a call of its __init__. Scope follows meridian.scan: a default value or decorator runs in the outer scope. A Jedi
failure while finding callers is counted in the log. On the team run (before dynamic-config): 2,054 edges, about
76 s and 1.2 GB.

receiver_from. For a call candidate recv.name(...) where recv's only binding in its scope is recv = F(...), and Jedi
lands F on exactly one def: that def as 'repo:path:scope'. Otherwise NULL, never a guess.

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

Environment. One jedi.Project per repo, with an explicit sys_path: the repo, then the snapshot repos it depends on,
then the standard library only. No site-packages and no working directory, so the result doesn't depend on which
virtualenv is active or what is installed. Jedi runs inside this process (InterpreterEnvironment). When a repo uses
a tracked module it doesn't contain (the packages use django.conf.urls), the snapshot repo that does contain it goes
on that path: the dependency comes from the same pinned snapshot, with nothing installed. Files are the scanner's
list (meridian.scan.files), .py only.

Same run, same bytes. Every question gets a fresh jedi.Script: with one Script per file, what Jedi had inferred for
earlier names changed a later answer from run to run (1 row in 3,451 on the packages). And parso never evicts during
a run (below): otherwise Jedi failed on names whose file had just been evicted, 30 on the packages, depending on what
was parsed before. Now repeated runs and any PYTHONHASHSEED give the same bytes. Parsing stays cached within a repo
and is emptied between repos, and tree-sitter trees are kept only for files a question comes back to.

Cache (settings.toml [cache] resolve, off by default). A file's rows (its candidates and alias calls) are reused from
snapshots/_cache/resolve/<repo>.json when this code, the versions, the repo's symbols and rules, its dependencies'
SHAs (Django, for the packages), every folder and file name in the checkout and the file's candidates are the same,
and so is the blob of every repo file read while resolving it last time: the file, what it imports and re-exports,
however deep Jedi went, and any file Jedi searched. Imports aren't guessed from module_import: what Jedi read is
recorded. call_edge is never cached: every run rebuilds it from the rows. Same bytes either way.

Same bytes on every OS. To guess a parameter, Jedi reads the calls of its function in the first file of a project
walk that has one, and the walk listed folders in the filesystem's order: NTFS by name, APFS by a hash of the name. On
70dd1dabfed544b8 Windows had 5 call edges macOS didn't (django's AdminField.label_tag landing on BoundField.label_tag)
and 2 allauth rows with another resolved_to. Jedi's walk now uses APFS's order on every OS (below).
"""
import functools
import importlib.machinery
import json
import os
import sys
import sysconfig
import types
import unicodedata
from collections import Counter
from importlib.metadata import version
from pathlib import Path

import jedi
import jedi.file_io
import parso.cache
import parso.file_io
from jedi.inference.compiled.subprocess import functions as jedi_functions

from meridian import scan as S
from meridian.common import (Cache, blob_sha, cache_key, cache_on, data_root, handoff, load_config, log, parser,
                             read_jsonl, repo_checkout, source_hash, write_jsonl)
from meridian.scan import PY, char_col, tracked, walk

# parso (pinned in requirements.txt) evicts parsed files from memory once it holds 600, judging age by the file's
# mtime. Every file in a fresh checkout looks old, so a file Jedi has just parsed can be evicted mid-question, and
# Jedi then fails with KeyError on that path. How often depends on what was parsed before, so it broke "same run,
# same bytes". Never evict during a run.
parso.cache._CACHED_SIZE_TRIGGER = float("inf")

# Jedi (pinned) looks up a submodule by its last name only, and asks the builtin importer too, which ignores the
# package path: django/contrib/postgres/fields/array.py came back as the builtin `array` ("not importable"), so
# nothing in it could be reached. Which names are builtin differs by OS (array is on Windows, not on Linux), so the
# edges did too. A submodule is a file in its package, never a builtin: search only the package's folders for it.
_jedi_find_module = jedi_functions._find_module


def _find_module(string, path=None, full_name=None, is_global_search=True):
    if is_global_search:
        return _jedi_find_module(string, path, full_name, is_global_search)
    meta_path, sys.meta_path = sys.meta_path, [importlib.machinery.PathFinder]
    try:
        return _jedi_find_module(string, path, full_name, is_global_search)
    finally:
        sys.meta_path = meta_path


jedi_functions._find_module = _find_module

# [cache] resolve only: while _reads is a set, every file Jedi gets the content of goes in it. Jedi gets each one as a
# parso FileIO (read from disk, or KnownContentFileIO with what the importer read), and Repo.source adds its own.
_reads = None
_file_io_init = parso.file_io.FileIO.__init__


def _file_io(self, path):
    _file_io_init(self, path)
    if _reads is not None:
        _reads.add(str(self.path))


parso.file_io.FileIO.__init__ = _file_io


# Jedi (pinned) guesses a parameter from the calls of its function in the first file of a project walk (os.walk) that
# has one. os.walk lists a folder in the filesystem's order, so which caller it used, and so the answers, depended on
# the OS: Windows (NTFS, by name) had 5 more django edges than macOS. Walk in one fixed order on every OS. The order is
# macOS's own (APFS, case-insensitive, its default), so the team's numbers, made on a Mac, stay.
_CRC32C = [functools.reduce(lambda c, _: c >> 1 ^ (0x82F63B78 if c & 1 else 0), range(8), i) for i in range(256)]


@functools.cache
def _apfs_order(name):
    """Where APFS lists a name in its folder: by a 22-bit CRC-32C of the name case-folded and decomposed (NFD)."""
    crc = 0xFFFFFFFF
    for b in unicodedata.normalize("NFD", name.casefold()).encode("utf-32-le"):
        crc = _CRC32C[(crc ^ b) & 0xFF] ^ crc >> 8
    return crc & 0x3FFFFF, len(name.encode()) + 1, name


def _walk(top):
    for root, folders, files in os.walk(top):
        folders.sort(key=_apfs_order)
        files.sort(key=_apfs_order)
        yield root, folders, files


jedi.file_io.os = types.SimpleNamespace(**{**vars(os), "walk": _walk})  # only Jedi's folder walk changes

RESOLVER = f"jedi=={jedi.__version__}"
COLUMNS = ["run_id", "repo", "path", "line", "col", "symbol", "detection", "via", "resolved_to", "resolver",
           "receiver_from"]
STDLIB = sorted({sysconfig.get_paths()[k] for k in ("stdlib", "platstdlib")})
ENVIRONMENT = [Path(p).resolve() for p in STDLIB] + [Path(jedi.__file__).resolve().parent]  # with Jedi's own stubs


def py_files(repo_dir, skip):
    """The scanner's file list ([scanner] skip_paths), .py only: one walk, one rule for case and folders."""
    return [p for p, kind in S.files(repo_dir, {"skip_paths": skip}) if kind == "py"]


def node_at(src, root, line, col):
    """The smallest tree-sitter node at line:col, col in characters (Jedi's)."""
    text = src.split(b"\n")[line - 1].decode("utf-8", errors="replace")
    point = (line - 1, len(text[:col].encode()))
    return root.descendant_for_point_range(point, point)


def is_binding(node):
    """Does this identifier (re)bind its name: a target, an import, a def, a parameter? Reads don't."""
    parent = node.parent
    if parent.type == "keyword_argument":
        return False
    if S.role(node) in ("import", "definition") or S.name_only(node):
        return True
    if parent.type in ("pattern_list", "tuple_pattern", "list_pattern", "as_pattern_target", "global_statement",
                       "nonlocal_statement"):
        return True
    field = {"augmented_assignment": "left", "for_statement": "left", "for_in_clause": "left",
             "named_expression": "name"}.get(parent.type)
    return field is not None and S.same(parent.child_by_field_name(field), node)


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
        self.deps = [Path(p) for p in extra_paths]
        self.env = jedi.InterpreterEnvironment()                      # no helper subprocess: see "Environment"
        self.project = jedi.Project(repo_dir, sys_path=[str(p) for p in [repo_dir, *self.deps, *STDLIB]])
        self.code, self.trees, self.failures = {}, {}, 0
        parso.cache.parser_cache.clear()  # a new repo: nothing parsed for the last one is asked about again

    def source(self, path):
        if _reads is not None:
            _reads.add(str(self.dir / path))
        return (self.dir / path).read_bytes()

    def parse(self, path):
        src = self.source(path)
        return src, PY.parse(src).root_node

    def tree(self, path):
        """parse(), kept: only for files a question comes back to. Whole-repo walks use parse()."""
        if path not in self.trees:
            self.trees[path] = self.parse(path)
        return self.trees[path]

    def goto(self, path, line, col, follow_imports=True):
        """A fresh jedi.Script per question: what Jedi inferred for one name can't change its answer for the next.
        A crash in Jedi is a missing answer, not a missing row: logged, counted in self.failures, []."""
        if path not in self.code:
            self.code[path] = self.source(path).decode("utf-8", errors="replace")
        try:
            script = jedi.Script(code=self.code[path], path=str(self.dir / path), project=self.project,
                                 environment=self.env)
            return script.goto(line, col, follow_imports=follow_imports)
        except Exception as e:
            self.failures += 1
            log(f"  jedi failed at {path}:{line}:{col}: {type(e).__name__}: {e}")
            return []

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
        """If line:col sits in a `from X import ...` statement, X as an absolute module (`.translation` resolved like
        meridian.scan's module_import does). Otherwise None."""
        node = node_at(*self.tree(path), line, col)
        while node is not None and node.type != "import_from_statement":
            node = node.parent
        return next(iter(S.imports(path, [node])))[1] if node is not None else None

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
        names = self.goto(c["path"], c["line"], col)
        if not names:
            return "ast_unresolved", "none", None
        expected = f"{self.symbols[c['symbol']]}.{c['symbol']}"
        accepted = {expected} if self.score_on_old_name else set(self.targets)  # off: any tracked symbol counts
        if hits := sorted({n.full_name for n in names if n.full_name in accepted}):
            module = self.imported_from(c["path"], c["line"], col)
            return "ast_resolved", ("reexport" if module and module != self.symbols[c["symbol"]] else "direct"), hits[0]
        return "dropped", "none", self.first(names)

    def receiver_from(self, c, repo):
        """For a call candidate recv.name(...) where recv's only binding in its scope is recv = F(...): F's def as
        'repo:path:scope', when Jedi lands F on exactly one def. Otherwise None: never a guess."""
        col = self.locate(c["path"], c["line"], c["col"], c["symbol"])
        if c["role"] != "call" or col is None:
            return None
        src, root = self.tree(c["path"])
        name = node_at(src, root, c["line"], col)
        attr = name.parent
        if attr.type != "attribute" or not S.same(attr.child_by_field_name("attribute"), name):
            return None
        recv = attr.child_by_field_name("object")
        if recv.type != "identifier":
            return None
        home = S.scopes(recv)[:1]  # recv's own def/class, or [] for the module
        params = home[0].child_by_field_name("parameters") if home and home[0].type == "function_definition" else None
        if params is not None and any(n.type == "identifier" and n.text == recv.text and S.name_only(n)
                                      for n in walk(params)):
            return None  # also a parameter: its value depends on the caller
        found = [n for n in walk(home[0] if home else root) if n.type == "identifier" and n.text == recv.text
                 and is_binding(n) and [d.id for d in S.scopes(n)[:1]] == [d.id for d in home]]
        if len(found) != 1 or found[0].parent.type != "assignment":
            return None
        right = found[0].parent.child_by_field_name("right")
        fn = right.child_by_field_name("function") if right is not None and right.type == "call" else None
        fn = fn.child_by_field_name("attribute") if fn is not None and fn.type == "attribute" else fn
        if fn is None or fn.type != "identifier":
            return None
        landed = {self.def_id(n, repo) for n in self.goto(c["path"], fn.start_point[0] + 1, char_col(src, fn))
                  if n.type == "function"}
        return landed.pop() if len(landed) == 1 and None not in landed else None

    def def_id(self, name, repo):
        """'repo:path:scope' of the def a Jedi answer lands on, in this repo or a snapshot dependency, else None."""
        for base, owner in [(self.dir, repo), *((d, d.name.replace("__", "/", 1)) for d in self.deps)]:
            try:
                path = Path(name.module_path).resolve().relative_to(base.resolve()).as_posix()
            except (TypeError, ValueError):
                continue
            src = (base / path).read_bytes()
            node = node_at(src, PY.parse(src).root_node, name.line, name.column)
            if node.parent.type == "function_definition" and S.same(node.parent.child_by_field_name("name"), node):
                return f"{owner}:{path}:{S.scope(node.parent.child_by_field_name('body'))}"
        return None

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
        node = node_at(src, root, line, col)
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
        names = self.goto(path, line, col)
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
            raise SystemExit(f"{repo} {c['path']}:{c['line']}: candidate symbol {c['symbol']!r} "
                             "isn't tracked in meridian.toml")
        detection, via, resolved_to = r.resolve_candidate(c)
        rows.append(dict(zip(COLUMNS, [run_id, repo, c["path"], c["line"], c["col"], c["symbol"], detection, via,
                                       resolved_to, RESOLVER, r.receiver_from(c, repo)])))
    return rows


def resolve_aliases(run_id, repo, repo_dir, symbols, skip=(), r=None, paths=None):
    """One resolution row per alias call in the repo (or in paths only)."""
    r = r or Repo(repo_dir, symbols)
    rows = []
    for path in py_files(repo_dir, skip) if paths is None else paths:
        src, root = r.parse(path)
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
            rows.append(dict(zip(COLUMNS,
                                 [run_id, repo, path, line, col, symbol, detection, via, resolved_to, RESOLVER, None])))
    return rows


def names_in(repo_dir):
    """Every folder and file name in the checkout but .git, in a fixed order: all Jedi can find, never what's inside."""
    found = []
    for top, dirs, names in os.walk(repo_dir, onerror=S.fail):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        found.append([Path(top).relative_to(repo_dir).as_posix(), dirs, sorted(names)])
    return found


# Read outside parso's FileIO: a buildout.cfg makes Jedi read bin/ scripts' first lines, and Jedi imports compiled or
# bytecode-only modules for real. A repo holding any of them is never cached.
UNCACHEABLE = ("buildout.cfg", *importlib.machinery.EXTENSION_SUFFIXES, *importlib.machinery.BYTECODE_SUFFIXES)


def resolve_cached(run_id, repo, r, candidates, files, cache, base):
    """resolve_candidates and resolve_aliases file by file, for [cache] resolve: (candidate rows, alias rows).

    A file's rows are what Jedi and Repo make of the files they read while resolving it, given the checkout's names
    (in base, with the code, versions, config and dependency SHAs). So they are reused when base and the file's
    candidates are the same and every repo file read for it last time has the same blob SHA: a changed import, re-export
    or anything else it read redoes it. A read in a dependency or the Python install is covered by base. A file that
    read anything else, or had a Jedi failure, is never kept. call_edge is not cached: run() rebuilds it from these
    rows.
    """
    global _reads
    by_path, blobs, places = {}, {}, {}
    for c in candidates:
        by_path.setdefault(c["path"], []).append(c)
    files = set(files)
    home, outside = r.dir.resolve(), [p.resolve() for p in r.deps] + ENVIRONMENT

    def blob(rel):
        if rel not in blobs:
            p = r.dir / rel
            blobs[rel] = blob_sha(p.read_bytes()) if p.is_file() else None
        return blobs[rel]

    def place(p):  # repo-relative path, "" for a dependency or the Python install, None for anywhere else
        if p not in places:
            q = Path(p).resolve()
            places[p] = q.relative_to(home).as_posix() if q.is_relative_to(home) else \
                "" if any(q.is_relative_to(o) for o in outside) else None
        return places[p]

    cands, aliases, todo = [], [], sorted(files | set(by_path))
    for path in todo:
        cs = by_path.get(path, [])
        key = cache_key(base, path, [{k: v for k, v in c.items() if k != "run_id"} for c in cs])
        if hit := cache.get(path, lambda v: v["key"] == key and all(blob(p) == b for p, b in v["inputs"].items())):
            got = [[{"run_id": run_id, **x} for x in rows] for rows in hit["rows"]]
        else:
            failures, _reads = r.failures, {str(r.dir / path)}
            try:
                got = [resolve_candidates(run_id, repo, r.dir, r.symbols, cs, r),
                       resolve_aliases(run_id, repo, r.dir, r.symbols, r=r, paths=[path]) if path in files else []]
                spots = {place(p) for p in _reads}
            finally:
                _reads = None
            if r.failures == failures and None not in spots:
                cache.put(path, {"key": key, "inputs": {p: blob(p) for p in sorted(spots - {""})},
                                 "rows": [[{k: v for k, v in x.items() if k != "run_id"} for x in rows]
                                          for rows in got]})
        taken = {(x["path"], x["line"], x["col"]) for x in got[0]}
        cands += got[0]
        # a candidate already covers that spot
        aliases += [x for x in got[1] if (x["path"], x["line"], x["col"]) not in taken]
    log(f"  cache: {len(cache.hits)} of {len(todo)} files reused, "
        f"{len(cache.new) - len(cache.hits)} resolved and kept, {len(todo) - len(cache.new)} resolved and not kept")
    return cands, aliases


def load_candidates(out, run_id):
    path = out / "candidate.jsonl"
    if not path.exists():
        raise SystemExit(f"no candidate table: {path} is missing. Run meridian.scan first")
    rows = read_jsonl(path)
    if wrong := sorted({c["run_id"] for c in rows} - {run_id}):
        raise SystemExit(f"{path} has rows for run_id {wrong}, not {run_id!r}")
    return rows


def dependency_paths(snap, manifest, repo, symbols):
    """Snapshot repos that contain a tracked module this repo uses but doesn't have itself (packages -> django)."""
    paths = []
    for top in sorted({m.split(".")[0] for m in symbols.values()}):
        if (repo_checkout(snap, repo) / top / "__init__.py").exists():
            continue
        paths += [repo_checkout(snap, other) for other in sorted(manifest["repos"])
                  if other != repo and (repo_checkout(snap, other) / top / "__init__.py").exists()]
    return paths


EDGE_COLUMNS = ["run_id", "repo", "caller_path", "caller_scope", "line", "col", "callee_path", "callee_repo",
                "callee_scope", "introduced_sha"]


def scope_of(root, src, line, col):
    """(dotted scope, callers) for the code at line:col, scope as in meridian.scan. callers: [(called name, line Jedi
    lands on)] that run it, empty for a module or class body. An __init__ also runs when its class is called."""
    node = node_at(src, root, line, col)
    chain = S.scopes(node)
    if not chain or chain[0].type != "function_definition":
        return S.scope(node), []
    names = [chain[0].child_by_field_name("name")]
    if names[0].text == b"__init__" and len(chain) > 1 and chain[1].type == "class_definition":
        names.append(chain[1].child_by_field_name("name"))
    return S.scope(node), [(n.text.decode(), n.start_point[0] + 1) for n in names]


def call_sites(r, files):
    """{called name: [(path, line, col)]} for every call in the repo: f(...) and x.f(...)."""
    index = {}
    for path in files:
        src, root = r.parse(path)
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
    A call counts only when Jedi lands on that very function's def (or, for an __init__, its class). Module and class
    bodies get edges but have no callers, so they stop the walk. Imports never make edges.
    introduced_sha: the commit that last changed the caller's line, by join's blame (--first-parent -w) at the
    checkout's HEAD, which run() has checked is the manifest's SHA. So an edge and the call site on its line name the
    same commit.
    """
    from meridian.join import blame
    edges, frontier, seen, answers = {}, {}, set(), {}

    def add(path, line, col, callee_repo, callee_path, callee_scope):
        src, root = r.tree(path)
        scope, callers = scope_of(root, src, line, col)
        edges[(path, line, col, callee_repo, callee_path, callee_scope)] = scope
        if callers and (path, callers[0][1]) not in seen:
            seen.add((path, callers[0][1]))
            frontier[(path, callers[0][1])] = (scope, callers)

    for use in uses:
        add(*use)
    index = None
    for _ in range(depth - 1):
        targets, frontier = sorted(frontier.items()), {}
        if not targets:
            break
        index = index if index is not None else call_sites(r, files)
        for (t_path, _), (t_scope, callers) in targets:
            for called, lands in callers:
                for site in index.get(called, []):
                    if site not in answers:  # one Jedi question per call site, however many functions share its name
                        answers[site] = {r.where(n)[:2] for n in r.goto(*site) if r.where(n)}
                    if (t_path, lands) in answers[site]:
                        add(*site, repo, t_path, t_scope)
    lines = {}
    for path, line, *_ in edges:
        lines.setdefault(path, set()).add(line)
    made = {path: blame(r.dir, "HEAD", path, ls) for path, ls in sorted(lines.items())}
    return [dict(zip(EDGE_COLUMNS,
                     [run_id, repo, path, scope, line, col, callee_path, callee_repo, callee_scope, made[path][line]]))
            for (path, line, col, callee_repo, callee_path, callee_scope), scope in sorted(edges.items())]


def summary(rows):
    c = Counter(x["detection"] for x in rows)
    return (f"ast_resolved {c['ast_resolved']} · ast_unresolved {c['ast_unresolved']} · dropped {c['dropped']} · "
            f"via {dict(sorted(Counter(x['via'] for x in rows).items()))}")


def run(run_id, cfg, root=None):
    snap = Path(root or data_root()) / "snapshots" / run_id
    manifest_path = snap / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"no snapshot: {manifest_path} is missing. Run meridian.collect first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:  # 0 would act as 1, "3" would crash
        raise SystemExit(f"[resolver] call_edge_depth must be a whole number of at least 1, not {depth!r}")
    if not settings.get("keep_unresolved", True):  # CONTRACTS.md rule 9: nothing is silently dropped
        raise SystemExit("[resolver] keep_unresolved = false would drop rows silently, "
                         "which CONTRACTS.md rule 9 forbids")
    rules = dict(one_hop=settings.get("one_hop_assignment", True),
                 score_on_old_name=settings.get("score_on_old_name", True))
    roles = {(c["repo"], c["path"], c["line"], c["col"]): c["role"] for cs in by_repo.values() for c in cs}
    cached = cache_on("resolve")
    rows, edges = [], []
    for repo, sha in sorted(manifest["repos"].items()):
        if repo not in wanted:
            continue
        repo_dir = repo_checkout(snap, repo)
        if S.head(repo_dir) != sha:
            raise SystemExit(f"{repo}: checkout is at {S.head(repo_dir)}, the manifest says {sha}")
        deps = dependency_paths(snap, manifest, repo, wanted[repo])
        r = Repo(repo_dir, wanted[repo], deps, **rules)
        repo_cands = sorted(by_repo.get(repo, []), key=lambda c: (c["path"], c["line"], c["col"]))
        # shortcut: every name in the checkout is in base, so adding or deleting any file redoes the whole repo. If that
        # costs too much, record the folders Jedi and importlib list for a file and key on those names only
        names = names_in(repo_dir) if cached else []
        if names and not any(n.endswith(UNCACHEABLE) for _, _, ns in names for n in ns):
            cache = Cache("resolve", repo, root)
            base = cache_key(source_hash(__file__, S.__file__), [version(d) for d in ("jedi", "parso", "tree-sitter",
                             "tree-sitter-python")], sys.version, STDLIB, wanted[repo], rules,
                             [[d.name, S.head(d)] for d in deps], names)
            cands, aliases = resolve_cached(run_id, repo, r, repo_cands, py_files(repo_dir, skip), cache, base)
            cache.save()
        else:
            cands = resolve_candidates(run_id, repo, repo_dir, wanted[repo], repo_cands, r)
            taken = {(x["path"], x["line"], x["col"]) for x in cands}
            aliases = [x for x in resolve_aliases(run_id, repo, repo_dir, wanted[repo], skip, r)
                       if (x["path"], x["line"], x["col"]) not in taken]  # a candidate already covers that spot
            if cached:
                log(f"  cache: not used, the checkout holds a file Jedi reads outside parso ({', '.join(UNCACHEABLE)})")
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
        failed = r.failures
        # shortcut: with [cache] resolve on, call_edge's Jedi questions are still all asked. If resolve stays slow,
        # cache each call site's answer under its file's recorded reads, as resolve_cached does for rows
        found = call_edges(run_id, repo, r, uses, py_files(repo_dir, skip), depth)
        log(f"  {len(found)} call edges ({len(uses)} from confirmed calls, "
            f"{len(found) - len(uses)} from their callers) · "
            f"{r.failures - failed} Jedi failures while finding callers · "
            f"{sum(x['receiver_from'] is not None for x in cands)} receiver_from")
        edges += found
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "resolution.jsonl", rows, ("repo", "path", "line", "col"))
    write_jsonl(out / "call_edge.jsonl", edges,
                ("repo", "caller_path", "line", "col", "callee_repo", "callee_path", "callee_scope"))
    log(f"{len(rows)} resolution rows, {len(edges)} call edges -> {out} ({RESOLVER})")
    return rows


def main(argv=None):
    a = parser(__doc__).parse_args(argv)
    run(a.run_id, load_config(a.config))


if __name__ == "__main__":
    main()
