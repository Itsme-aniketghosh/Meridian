"""D4–D5 tests: the resolver on a fixture repo (candidates and alias calls), the one command, and Django's answer keys.

    python -m unittest tests.test_resolve -v      # from Data-Pipeline/

The Django and package tests need a snapshot: $MERIDIAN_RUN_ID (default: dev). Each is skipped when its repos
aren't in that snapshot. To run them on the team data (the package test takes about 20 s):

    MERIDIAN_RUN_ID=973531f7fd4cfd5e python -m unittest tests.test_resolve -v
"""
import contextlib
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import jedi
import parso.cache

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import meridian.resolve as R
import meridian.scan as S
import meridian.writer as W
import tests.hand_check_aliases as HA
from meridian.common import data_root, load_config, read_jsonl, write_jsonl
from tests.score_packages import changed_lines, score

RUN = "abcdef0123456789"
SYMBOLS = {"ugettext": "lib.translation", "ugettext_lazy": "lib.translation"}
SCANNER = {"skip_paths": ["docs/"], "keep_name_only": True, "deprecation_markers": []}


def scanned(run_id, repo, repo_dir, symbols, scanner):
    """Rachna's candidate rows for one repo, from meridian.scan itself."""
    return S.scan_repo(run_id, repo, repo_dir, symbols, scanner)[0]


FILES = {
    "lib/__init__.py": "",
    "lib/translation.py": (
        "def gettext(message):\n"
        "    return message\n"
        "\n"
        "ugettext = gettext\n"                        # the old name is a second name for the new one
        "gettext_lazy = ugettext_lazy = gettext\n"    # chained, as in Django
    ),
    "app/__init__.py": "",
    "app/imports.py": (                                # import alias
        "from lib.translation import ugettext as _\n"
        "\n"
        "title = _('Hello')\n"
    ),
    "app/assigns.py": (                                # assignment alias: needs the one-hop rule
        "from lib.translation import ugettext_lazy\n"
        "\n"
        "def labels():\n"
        "    _ = ugettext_lazy\n"
        "    return [_('a'), _('b')]\n"
    ),
    "app/shadowed.py": (                               # a function redefines _ : dropped
        "from lib.translation import ugettext as _\n"
        "\n"
        "def f():\n"
        "    def _(s):\n"
        "        return s\n"
        "    return _('local')\n"
    ),
    "app/helpers.py": (
        "def ugettext(s):\n"
        "    return s\n"
    ),
    "app/other.py": (                                  # same old name, other module: dropped
        "from app.helpers import ugettext as _\n"
        "\n"
        "x = _('other')\n"
    ),
    "app/unbound.py": (                                # bound in one function, called in another: kept
        "def setup():\n"
        "    from lib.translation import ugettext as _\n"
        "    return _('in scope')\n"
        "\n"
        "def later():\n"
        "    return _('not in scope')\n"
    ),
    "docs/conf.py": (                                  # skip_paths
        "from lib.translation import ugettext as _\n"
        "y = _('skipped')\n"
    ),
    "app/direct.py": (                                 # candidates: import, call, attribute call
        "from lib import translation\n"
        "from lib.translation import ugettext\n"
        "\n"
        "a = ugettext('x')\n"
        "b = translation.ugettext('y')\n"
    ),
    "app/compat.py": "from lib.translation import ugettext\n",  # passes the old name on
    "app/via_compat.py": (                             # candidates through a re-export
        "from app.compat import ugettext\n"
        "\n"
        "z = ugettext('re')\n"
    ),
    "app/local.py": (                                  # its own ugettext: dropped
        "def ugettext(s):\n"
        "    return s\n"
        "\n"
        "w = ugettext('mine')\n"
    ),
    "app/unknown.py": (                                # a method on an unknown object: kept as unresolved
        "def f(obj):\n"
        "    return obj.ugettext('?')\n"
    ),
    "app/unicode.py": (                                # é is 1 character but 2 bytes
        "from lib.translation import ugettext\n"
        "x = 'é' + ugettext('k')\n"
    ),
    "app/sep.py": (                                    # U+2028 in the text column: not a line end in jsonl
        "from lib.translation import ugettext\n"
        "v = ugettext('a b')\n"
    ),
    "app/Views.PY": "from lib.translation import ugettext as _\n_('upper')\n",   # not .py, on Windows too
    "app/pkg.py/__init__.py": "",                      # a folder named pkg.py is not a file to read
    "lib/other.py": "from .translation import ugettext\n",   # a relative import of the module itself: direct
}

CANDIDATES = {   # (path, line): (role, detection, via, resolved_to)
    ("lib/translation.py", 4): ("definition", "ast_resolved", "direct", "lib.translation.ugettext"),
    ("lib/translation.py", 5): ("definition", "ast_resolved", "direct", "lib.translation.ugettext_lazy"),
    ("app/direct.py", 2): ("import", "ast_resolved", "direct", "lib.translation.ugettext"),
    ("app/direct.py", 4): ("call", "ast_resolved", "direct", "lib.translation.ugettext"),
    ("app/direct.py", 5): ("call", "ast_resolved", "direct", "lib.translation.ugettext"),
    ("app/assigns.py", 4): ("alias", "ast_resolved", "direct", "lib.translation.ugettext_lazy"),
    ("app/via_compat.py", 1): ("import", "ast_resolved", "reexport", "lib.translation.ugettext"),
    ("app/via_compat.py", 3): ("call", "ast_resolved", "reexport", "lib.translation.ugettext"),
    ("app/local.py", 1): ("definition", "dropped", "none", "app.local.ugettext"),
    ("app/local.py", 4): ("call", "dropped", "none", "app.local.ugettext"),
    ("app/helpers.py", 1): ("definition", "dropped", "none", "app.helpers.ugettext"),
    ("app/unknown.py", 2): ("call", "ast_unresolved", "none", None),
    ("app/unicode.py", 2): ("call", "ast_resolved", "direct", "lib.translation.ugettext"),
    ("app/sep.py", 2): ("call", "ast_resolved", "direct", "lib.translation.ugettext"),
    ("lib/other.py", 1): ("import", "ast_resolved", "direct", "lib.translation.ugettext"),
}

EXPECTED = {   # (path, line, col): (detection, via, resolved_to, symbol)
    ("app/imports.py", 3, 8): ("ast_resolved", "import_alias", "lib.translation.ugettext", "ugettext"),
    ("app/assigns.py", 5, 12): ("ast_resolved", "assign_alias", "lib.translation.ugettext_lazy", "ugettext_lazy"),
    ("app/assigns.py", 5, 20): ("ast_resolved", "assign_alias", "lib.translation.ugettext_lazy", "ugettext_lazy"),
    ("app/shadowed.py", 6, 11): ("dropped", "import_alias", "app.shadowed.f._", "ugettext"),
    ("app/other.py", 3, 4): ("dropped", "import_alias", "app.helpers.ugettext", "ugettext"),
    ("app/unbound.py", 3, 11): ("ast_resolved", "import_alias", "lib.translation.ugettext", "ugettext"),
    ("app/unbound.py", 6, 11): ("ast_unresolved", "import_alias", None, "ugettext"),
}


def fixture_repo(path, files=None):
    for rel, text in (files or FILES).items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}

    def git(*a):
        return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True,
                              env=env).stdout.strip()

    git("init", "--quiet", "--initial-branch=main")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--quiet", "-m", "fixture")
    return git("rev-parse", "HEAD")


def snapshot(root, run_id=RUN):
    """root/snapshots/<run_id>/ with one repo, laid out the way Collect lays it out, and a candidate table."""
    repo_dir = root / "snapshots" / run_id / "repos" / "fix__app"
    repo_dir.mkdir(parents=True)
    sha = fixture_repo(repo_dir)
    (root / "snapshots" / run_id / "manifest.json").write_text(json.dumps({"run_id": run_id,
                                                                           "repos": {"fix/app": sha}}))
    config = root / "meridian.toml"
    symbols = ", ".join(f'{{ name = "{n}", module = "{m}" }}' for n, m in SYMBOLS.items())
    config.write_text(
        '[run]\nhandoff_dir = "out/{run_id}"\n\n'
        f'[datasets.fixture]\nrepos = [{{ repo = "fix/app", ref = "{sha}" }}]\nsymbols = [{symbols}]\n\n'
        '[scanner]\nskip_paths = ["docs/"]\n'
    )
    (root / "out" / run_id).mkdir(parents=True)
    write_jsonl(root / "out" / run_id / "candidate.jsonl", scanned(run_id, "fix/app", repo_dir, SYMBOLS, SCANNER))
    return repo_dir, sha, config


class Aliases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo_dir, _, _ = snapshot(Path(cls.tmp.name))
        cls.rows = R.resolve_aliases(RUN, "fix/app", cls.repo_dir, SYMBOLS, skip=["docs/"])
        cls.by_key = {(r["path"], r["line"], r["col"]): r for r in cls.rows}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_alias_call_is_a_row(self):
        self.assertEqual(sorted(self.by_key), sorted(EXPECTED))

    def test_each_row(self):
        for key, (detection, via, resolved_to, symbol) in EXPECTED.items():
            with self.subTest(key=key):
                r = self.by_key[key]
                self.assertEqual((r["detection"], r["via"], r["resolved_to"], r["symbol"]),
                                 (detection, via, resolved_to, symbol))
                self.assertEqual(r["resolver"], R.RESOLVER)

    def test_one_hop_reaches_the_old_name_not_the_new(self):
        hops = [r for r in self.rows if r["via"] == "assign_alias"]
        self.assertEqual(len(hops), 2)
        self.assertTrue(all(r["resolved_to"] == "lib.translation.ugettext_lazy" for r in hops))  # never gettext_lazy

    def test_skipped_paths_have_no_rows(self):
        self.assertFalse([r for r in self.rows if r["path"].startswith("docs/")])

    def test_one_hop_switched_off(self):
        r = R.Repo(self.repo_dir, SYMBOLS, one_hop=False)
        rows = R.resolve_aliases(RUN, "fix/app", self.repo_dir, SYMBOLS, ["docs/"], r)
        self.assertEqual({row["detection"] for row in rows if row["path"] == "app/assigns.py"}, {"dropped"})

    def test_bindings(self):
        src = b"from x import ugettext as _, ugettext_lazy\nT = ugettext_lazy\nfrom y import ugettext as ugettext\n"
        binds = R.bindings(R.PY.parse(src).root_node, {"ugettext", "ugettext_lazy"})
        self.assertEqual(binds, {"_": [("ugettext", "import_alias")], "T": [("ugettext_lazy", "assign_alias")]})

    def test_files_are_the_scanners(self):   # not Views.PY, not the folder pkg.py
        self.assertEqual(R.py_files(self.repo_dir, ["docs/"]), [p for p, _ in S.files(self.repo_dir, SCANNER)])
        self.assertNotIn("app/Views.PY", R.py_files(self.repo_dir, ["docs/"]))

    def test_parso_emptied_per_repo_and_walks_keep_no_trees(self):
        parso.cache.parser_cache["stale"] = {}
        r = R.Repo(self.repo_dir, SYMBOLS)
        self.assertNotIn("stale", parso.cache.parser_cache)
        files = R.py_files(self.repo_dir, ["docs/"])
        R.resolve_aliases(RUN, "fix/app", self.repo_dir, SYMBOLS, ["docs/"], r)
        R.call_sites(r, files)
        self.assertLess(set(r.trees), set(files))   # only files a one-hop question came back to
        self.assertNotIn("app/direct.py", r.trees)

    def test_sys_path_is_repo_deps_stdlib(self):   # nothing from the active virtualenv or the working directory
        dep = self.repo_dir.parent
        r = R.Repo(self.repo_dir, SYMBOLS, [dep])
        self.assertEqual(r.project.sys_path, [str(self.repo_dir), str(dep), *R.STDLIB])
        self.assertFalse([p for p in r.project.sys_path if "site-packages" in p])

    def test_jedi_failure_is_no_answer_and_counted(self):
        r = R.Repo(self.repo_dir, SYMBOLS)
        with mock.patch.object(jedi.Script, "goto", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(r.imported_from("app/via_compat.py", 3, 4))
            self.assertEqual(r.resolve("app/imports.py", 3, 8), ("ast_unresolved", None, None))
        self.assertEqual(r.failures, 2)


class Candidates(unittest.TestCase):
    """D5: one row per candidate, scored on its own old name."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo_dir, _, _ = snapshot(Path(cls.tmp.name))
        cls.cands = scanned(RUN, "fix/app", cls.repo_dir, SYMBOLS, SCANNER)
        cls.repo = R.Repo(cls.repo_dir, SYMBOLS)
        cls.rows = R.resolve_candidates(RUN, "fix/app", cls.repo_dir, SYMBOLS, cls.cands, cls.repo)
        cls.by_line = {}
        for c, r in zip(cls.cands, cls.rows):
            cls.by_line.setdefault((r["path"], r["line"]), (c, r))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_one_row_per_candidate_same_key(self):
        self.assertEqual([(c["path"], c["line"], c["col"]) for c in self.cands],
                         [(r["path"], r["line"], r["col"]) for r in self.rows])

    def test_each_case(self):
        for key, (role, detection, via, resolved_to) in CANDIDATES.items():
            with self.subTest(key=key):
                c, r = self.by_line[key]
                self.assertEqual((c["role"], r["detection"], r["via"], r["resolved_to"]),
                                 (role, detection, via, resolved_to))

    def test_resolved_means_its_own_old_name(self):
        for r in self.rows:
            if r["detection"] == "ast_resolved":
                self.assertEqual(r["resolved_to"], f"{SYMBOLS[r['symbol']]}.{r['symbol']}")

    def test_column_in_characters_or_bytes(self):
        c = dict(self.by_line[("app/unicode.py", 2)][0])
        self.assertEqual(c["col"], 10)                       # x = 'é' + ugettext: 10 characters, 11 bytes
        for col in (10, 11):
            with self.subTest(col=col):
                self.assertEqual(self.repo.resolve_candidate({**c, "col": col})[0], "ast_resolved")

    def test_wrong_column_is_kept_unresolved(self):
        c = dict(self.by_line[("app/direct.py", 4)][0])
        self.assertEqual(self.repo.resolve_candidate({**c, "col": 0}), ("ast_unresolved", "none", None))

    def test_score_on_old_name_switched_off(self):
        with tempfile.TemporaryDirectory() as tmp:   # spelled ugettext, but it is ugettext_lazy
            repo = Path(tmp)
            fixture_repo(repo, {"lib/__init__.py": "", "lib/translation.py": FILES["lib/translation.py"],
                                "app/__init__.py": "",
                                "app/swap.py": "from lib.translation import ugettext_lazy as ugettext\n\n"
                                               "ugettext('x')\n"})
            c = {"path": "app/swap.py", "line": 3, "col": 0, "symbol": "ugettext"}
            strict = R.Repo(repo, SYMBOLS).resolve_candidate(c)
            loose = R.Repo(repo, SYMBOLS, score_on_old_name=False).resolve_candidate(c)
        self.assertEqual(strict[0], "dropped")                                       # not its own old name
        self.assertEqual((loose[0], loose[2]), ("ast_resolved", "lib.translation.ugettext_lazy"))

    def test_untracked_symbol_halts(self):
        c = dict(self.by_line[("app/direct.py", 4)][0])
        with self.assertRaises(SystemExit):
            R.resolve_candidates(RUN, "fix/app", self.repo_dir, SYMBOLS, [{**c, "symbol": "gettext"}], self.repo)


class Command(unittest.TestCase):
    """The one command: same run -> same bytes, rows fit schema.sql, and a moved checkout halts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo_dir, self.sha, self.config = snapshot(self.root)
        self.env = {**os.environ, "MERIDIAN_DATA": str(self.root)}

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self):
        return subprocess.run([sys.executable, "-m", "meridian.resolve", "--run-id", RUN, "--config", str(self.config)],
                              cwd=HERE, env=self.env, capture_output=True, text=True)

    def test_same_run_same_bytes(self):
        out = self.root / "out" / RUN / "resolution.jsonl"
        first = self.resolve()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = out.read_bytes()
        self.assertEqual(self.resolve().returncode, 0)
        self.assertEqual(out.read_bytes(), before)

    def test_rows_fit_the_schema(self):
        self.assertEqual(self.resolve().returncode, 0)
        schema = (HERE / "schema.sql").read_text(encoding="utf-8")
        rows = W.read_rows(self.root / "out" / RUN / "resolution.jsonl", W.tables(schema)["resolution"], RUN)
        candidates = read_jsonl(self.root / "out" / RUN / "candidate.jsonl")
        self.assertEqual(len(rows), len(candidates) + len(EXPECTED))  # every candidate, plus the alias calls
        db = sqlite3.connect(":memory:")
        db.executescript(schema)
        for _, r in rows:  # CHECK constraints: detection and via are closed lists
            db.execute(f"INSERT INTO resolution ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
        db.close()

    def test_no_candidate_table_halts(self):
        (self.root / "out" / RUN / "candidate.jsonl").unlink()
        r = self.resolve()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no candidate table", r.stderr)

    def test_keep_unresolved_false_halts(self):
        self.config.write_text(self.config.read_text() + "\n[resolver]\nkeep_unresolved = false\n")
        r = self.resolve()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("rule 9", r.stderr)

    def test_call_edge_depth_must_be_a_whole_number_of_at_least_1(self):
        base = self.config.read_text()
        for bad in ("0", "-1", '"3"', "2.5", "true"):
            with self.subTest(depth=bad):
                self.config.write_text(base + f"\n[resolver]\ncall_edge_depth = {bad}\n")
                r = self.resolve()
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("call_edge_depth must be a whole number", r.stderr)

    def test_checkout_not_at_the_manifest_sha_halts(self):
        manifest = self.root / "snapshots" / RUN / "manifest.json"
        manifest.write_text(json.dumps({"run_id": RUN, "repos": {"fix/app": "0" * 40}}))
        r = self.resolve()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the manifest says", r.stderr)


EDGE_FILES = {
    "lib/__init__.py": "",
    "lib/translation.py": "def gettext(m):\n    return m\n\nugettext = gettext\n",
    "app/__init__.py": "",
    "app/chain.py": (
        "from lib.translation import ugettext\n"
        "\n"
        "def level1():\n"
        "    return ugettext('a')\n"          # depth 1
        "\n"
        "def level2():\n"
        "    return level1()\n"               # depth 2
        "\n"
        "def level3():\n"
        "    return level2()\n"               # depth 3
        "\n"
        "def level4():\n"
        "    return level3()\n"               # depth 4: too far, no edge
        "\n"
        "class Labels:\n"
        "    title = ugettext('t')\n"         # class body: an edge, but nobody calls a class body
        "\n"
        "TOP = ugettext('top')\n"             # module: an edge, and the walk stops
    ),
    "app/importer.py": (
        "from app.chain import level1\n"      # an import is never an edge
        "\n"
        "def uses_it(obj):\n"
        "    obj.level1()\n"                  # unknown receiver: Jedi can't confirm it, so no edge
        "    return level1()\n"
    ),
    "app/cycle.py": (
        "from lib.translation import ugettext\n"
        "\n"
        "def ping(n):\n"
        "    return pong(n) if n else ugettext('p')\n"
        "\n"
        "def pong(n):\n"
        "    return ping(n - 1)\n"            # a cycle: the walk must stop
    ),
    "app/ctor.py": (
        "from lib.translation import ugettext\n"
        "\n"
        "class F:\n"
        "    def __init__(self):\n"
        "        self.m = ugettext('x')\n"    # depth 1, in F.__init__
        "\n"
        "def make():\n"
        "    return F()\n"                    # depth 2: calling the class runs __init__
    ),
    "app/defaults.py": (
        "from lib.translation import ugettext\n"
        "\n"
        "def get_text_list(list_, last_word=ugettext('or')):\n"   # a default runs in the module, not the function
        "    return list_\n"
        "\n"
        "def caller():\n"
        "    return get_text_list([])\n"                          # so no edge here
    ),
    "app/itertools.py": (                     # named like a builtin module, as django/contrib/postgres/fields/array.py
        "from lib.translation import ugettext\n"
        "\n"
        "def tool():\n"
        "    return ugettext('i')\n"
    ),
    "app/shadow.py": (
        "from app.itertools import tool\n"
        "\n"
        "def run():\n"
        "    return tool()\n"                 # depth 2: app.itertools is the package's file, not the builtin
    ),
}

EDGES = {   # (caller_path, caller_scope, line, callee_path, callee_scope)
    ("app/chain.py", "level1", 4, "lib/translation.py", "ugettext"),
    ("app/chain.py", "level2", 7, "app/chain.py", "level1"),
    ("app/chain.py", "level3", 10, "app/chain.py", "level2"),
    ("app/chain.py", "Labels", 16, "lib/translation.py", "ugettext"),
    ("app/chain.py", "<module>", 18, "lib/translation.py", "ugettext"),
    ("app/importer.py", "uses_it", 5, "app/chain.py", "level1"),
    ("app/cycle.py", "ping", 4, "lib/translation.py", "ugettext"),
    ("app/cycle.py", "pong", 7, "app/cycle.py", "ping"),
    ("app/cycle.py", "ping", 4, "app/cycle.py", "pong"),
    ("app/ctor.py", "F.__init__", 5, "lib/translation.py", "ugettext"),
    ("app/ctor.py", "make", 8, "app/ctor.py", "F.__init__"),
    ("app/defaults.py", "<module>", 3, "lib/translation.py", "ugettext"),
    ("app/itertools.py", "tool", 4, "lib/translation.py", "ugettext"),
    ("app/shadow.py", "run", 4, "app/itertools.py", "tool"),
}


class Edges(unittest.TestCase):
    """D6: call_edge on fixtures. Resolved calls only, never imports, depth <= 3, cycles stop."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        repo_dir = root / "snapshots" / RUN / "repos" / "fix__edges"
        repo_dir.mkdir(parents=True)
        sha = fixture_repo(repo_dir, EDGE_FILES)
        (root / "snapshots" / RUN / "manifest.json").write_text(json.dumps({"run_id": RUN,
                                                                            "repos": {"fix/edges": sha}}))
        cfg = {"run": {"handoff_dir": "out/{run_id}"}, "scanner": {}, "resolver": {"call_edge_depth": 3},
               "datasets": {"edges": {"repos": [{"repo": "fix/edges", "ref": sha}],
                                      "symbols": [{"name": "ugettext", "module": "lib.translation"}]}}}
        (root / "out" / RUN).mkdir(parents=True)
        write_jsonl(root / "out" / RUN / "candidate.jsonl",
                    scanned(RUN, "fix/edges", repo_dir, {"ugettext": "lib.translation"}, SCANNER))
        cls.log = io.StringIO()
        with contextlib.redirect_stdout(cls.log):
            R.run(RUN, cfg, root)
        cls.repo_dir, cls.sha = repo_dir, sha
        cls.path = root / "out" / RUN / "call_edge.jsonl"
        cls.rows = read_jsonl(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exact_edges(self):
        got = {(e["caller_path"], e["caller_scope"], e["line"], e["callee_path"], e["callee_scope"]) for e in self.rows}
        self.assertEqual(got, EDGES)

    def test_jedi_failures_while_finding_callers_are_counted(self):
        self.assertIn("0 Jedi failures while finding callers", self.log.getvalue())
        r = R.Repo(self.repo_dir, {"ugettext": "lib.translation"})
        use = ("app/chain.py", 4, 11, "fix/edges", "lib/translation.py", "ugettext")
        with mock.patch.object(jedi.Script, "goto", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(io.StringIO()):
            edges = R.call_edges(RUN, "fix/edges", r, [use], R.py_files(self.repo_dir, []), 3)
        self.assertEqual(len(edges), 1)       # the confirmed call; no caller could be confirmed
        self.assertEqual(r.failures, 3)       # the 3 calls named level1

    def test_no_edge_from_imports_or_past_depth_3(self):
        self.assertFalse([e for e in self.rows if e["caller_path"] == "app/importer.py" and e["line"] == 1])
        self.assertFalse([e for e in self.rows if e["caller_scope"] == "level4"])

    def test_rows_fit_the_schema(self):
        schema = (HERE / "schema.sql").read_text(encoding="utf-8")
        rows = W.read_rows(self.path, W.tables(schema)["call_edge"], RUN)
        self.assertEqual(len(rows), len(EDGES))

    def test_introduced_sha_is_the_commit_that_last_changed_the_caller_line(self):
        self.assertEqual({e["introduced_sha"] for e in self.rows}, {self.sha})  # one commit: every edge is from it
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "fix__edges"
            first = fixture_repo(repo_dir, EDGE_FILES)
            with open(repo_dir / "app/chain.py", "a", encoding="utf-8", newline="\n") as f:
                f.write("\ndef extra():\n    return level1()\n")  # a later commit adds a caller of level1
            git = lambda *a: subprocess.run(["git", "-C", str(repo_dir), "-c", "user.name=F", "-c",
                                             "user.email=f@example.com",
                                             *a], check=True, capture_output=True, text=True).stdout.strip()
            git("commit", "-qam", "extra")
            second = git("rev-parse", "HEAD")
            use = ("app/chain.py", 4, 11, "fix/edges", "lib/translation.py", "ugettext")
            edges = R.call_edges(RUN, "fix/edges", R.Repo(repo_dir, {"ugettext": "lib.translation"}), [use],
                                 R.py_files(repo_dir, []), 3)
            got = {(e["caller_scope"], e["line"], e["introduced_sha"]) for e in edges}
            self.assertIn(("level1", 4, first), got)
            self.assertIn(("level2", 7, first), got)
            self.assertIn(("extra", 21, second), got)


RECEIVER_FILES = {
    "shared/__init__.py": "",
    "shared/client.py": (
        "class Client:\n"
        "    def verify_token(self, t):\n"
        "        return t\n"
        "\n"
        "def get_client():\n"
        "    return Client()\n"
    ),
    "app/__init__.py": "",
    "app/use.py": (
        "from shared.client import Client, get_client\n"
        "\n"
        "def check(t):\n"
        "    c = get_client()\n"
        "    return c.verify_token(t)\n"      # 5: c = get_client() is c's only binding here
        "\n"
        "def twice(t):\n"
        "    c = get_client()\n"
        "    c = Client()\n"
        "    return c.verify_token(t)\n"      # 10: bound twice
        "\n"
        "def given(c=None):\n"
        "    c = get_client()\n"
        "    return c.verify_token(1)\n"      # 14: also a parameter
        "\n"
        "c = get_client()\n"
        "def other(t):\n"
        "    return c.verify_token(t)\n"      # 18: bound in another scope
        "\n"
        "k = Client()\n"
        "k.verify_token(2)\n"                 # 21: lands on a class, not a def
    ),
}

RECEIVERS = {("shared/client.py", 2): None, ("app/use.py", 5): "fix/repo:shared/client.py:get_client",
             ("app/use.py", 10): None, ("app/use.py", 14): None, ("app/use.py", 18): None, ("app/use.py", 21): None}


class ReceiverFrom(unittest.TestCase):
    """resolution.receiver_from: F of recv = F(...) for recv.name(...), only when it is certain."""

    def test_each_case(self):
        symbols = {"verify_token": "shared.client"}
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fixture_repo(repo, RECEIVER_FILES)
            rows = R.resolve_candidates(RUN, "fix/repo", repo, symbols,
                                        scanned(RUN, "fix/repo", repo, symbols, SCANNER))
        self.assertEqual({(r["path"], r["line"]): r["receiver_from"] for r in rows}, RECEIVERS)


class WalkOrder(unittest.TestCase):
    """The same answers whatever order the filesystem lists a folder in (NTFS sorts, APFS doesn't)."""

    def test_callers_found_dont_depend_on_listing_order(self):
        files = {"lib/__init__.py": "", "lib/use.py": "def use(obj):\n    return obj.run()\n"}
        # Jedi guesses obj from the first file of its walk that calls use(), so the order picks C0 or C1
        for i in range(2):
            files[f"c{i}.py"] = (f"from lib.use import use\n\nclass C{i}:\n    def run(self):\n        pass\n\n"
                                 f"use(C{i}())\n")
        walk = os.walk

        def reversed_walk(top, **kw):  # a filesystem that lists each folder the other way round
            for root, dirs, names in walk(top, **kw):
                dirs.reverse(), names.reverse()
                yield root, dirs, names

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fixture_repo(repo, files)
            ask = lambda: sorted(n.full_name for n in R.Repo(repo, SYMBOLS).goto("lib/use.py", 2, 15))
            forward = ask()
            with mock.patch("os.walk", reversed_walk):
                backward = ask()
        self.assertEqual(len(forward), 1)
        self.assertEqual(forward, backward)


U = "from lib.translation import ugettext\n"
REAL_FILES = {   # what real repos hold that the fixture above doesn't
    "lib/__init__.py": "",
    "lib/translation.py": "def gettext(m):\n    return m\n\nugettext = gettext\n",
    "app/__init__.py": "",
    "app/py2.py": U + "print 'hi'\nx = ugettext('a')\n",                     # Python 2 syntax beside it
    "app/compat.py": ("try:\n    from lib.translation import ugettext\n"   # the packages' compat pattern
                      "except ImportError:\n    ugettext = lambda s: s\n\nx = ugettext('a')\n"),
    "app/star.py": "from lib.translation import *\nx = ugettext('a')\n",
    "app/modalias.py": "import lib.translation as t\nx = t.ugettext('a')\n",
    "app/twohop.py": U + "a = ugettext\nb = a\nx = b('t')\n",              # two hops: the one-hop rule stops
    "app/deco.py": U + ("def deco(f):\n    return f\n\n@deco\ndef g():\n    return ugettext('a')\n\n"
                        "async def h():\n    return g()\n\ndef k():\n    return h()\n"),
    "app/meth.py": U + ("class C:\n    def m(self):\n        return ugettext('a')\n"
                        "    def n(self):\n        return self.m()\n\ndef use_c():\n    return C().n()\n"),
    "app/nested.py": U + ("def outer():\n    def inner():\n        return ugettext('b')\n    return inner()\n\n"
                          "def top():\n    return outer()\n"),
    "app/prop.py": U + ("class P:\n    @property\n    def label(self):\n        return ugettext('p')\n\n"
                        "def read_it():\n    return P().label\n"),                 # a property is read, not called
}
REAL_RAW = {
    "app/latin1.py": U.encode() + b"s = '\xe9'; x = ugettext('a')\n",          # not UTF-8
    "app/crlf.py": (U + "x = 1\ny = ugettext('a')\n").replace("\n", "\r\n").encode(),
}
LOOP_FILES = {   # a re-export loop: a imports from b, b from a
    **{k: REAL_FILES[k] for k in ("lib/__init__.py", "lib/translation.py", "app/__init__.py")},
    "app/cyc_a.py": "from app.cyc_b import ugettext\nx = ugettext('a')\n",
    "app/cyc_b.py": "from app.cyc_a import ugettext\n",
}


def resolve_fixture(root, files, raw=None):
    """The one command on a one-repo snapshot under root: ({(path, line): detection}, {(caller, callee)}, log)."""
    repo_dir = root / "snapshots" / RUN / "repos" / "fix__real"
    for rel, data in (raw or {}).items():
        (repo_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo_dir / rel).write_bytes(data)
    sha = fixture_repo(repo_dir, files)
    (root / "snapshots" / RUN / "manifest.json").write_text(json.dumps({"run_id": RUN, "repos": {"fix/real": sha}}))
    cfg = {"run": {"handoff_dir": "out/{run_id}"}, "scanner": {}, "resolver": {"call_edge_depth": 3},
           "datasets": {"real": {"repos": [{"repo": "fix/real", "ref": sha}],
                                 "symbols": [{"name": "ugettext", "module": "lib.translation"}]}}}
    (root / "out" / RUN).mkdir(parents=True)
    write_jsonl(root / "out" / RUN / "candidate.jsonl",
                scanned(RUN, "fix/real", repo_dir, {"ugettext": "lib.translation"}, SCANNER))
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        R.run(RUN, cfg, root)
    rows = {(r["path"], r["line"]): r["detection"] for r in read_jsonl(root / "out" / RUN / "resolution.jsonl")}
    edges = {(e["caller_scope"], e["callee_scope"]) for e in read_jsonl(root / "out" / RUN / "call_edge.jsonl")}
    return rows, edges, log.getvalue()


class RealWorldCode(unittest.TestCase):
    """The red-team probes: odd files and call shapes from real repos, through the one command, never a crash."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.rows, cls.edges, cls.log = resolve_fixture(Path(cls.tmp.name), REAL_FILES, REAL_RAW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_odd_files_still_resolve(self):
        for key in [("app/py2.py", 3), ("app/latin1.py", 2), ("app/crlf.py", 3)]:
            with self.subTest(file=key[0]):
                self.assertEqual(self.rows[key], "ast_resolved")

    def test_import_shapes(self):
        self.assertEqual(self.rows[("app/compat.py", 2)], "ast_resolved")   # the real import
        self.assertEqual(self.rows[("app/compat.py", 4)], "dropped")        # the fallback is someone else's
        self.assertEqual(self.rows[("app/compat.py", 6)], "ast_resolved")
        self.assertEqual(self.rows[("app/star.py", 2)], "ast_resolved")
        self.assertEqual(self.rows[("app/modalias.py", 2)], "ast_resolved")

    def test_two_hop_alias_call_is_not_seen(self):  # known limit of the one-hop rule: b('t') gets no row
        self.assertEqual(self.rows[("app/twohop.py", 2)], "ast_resolved")  # a = ugettext, a candidate
        self.assertNotIn(("app/twohop.py", 4), self.rows)

    def test_callers_through_decorators_async_methods_and_nesting(self):
        self.assertLessEqual({("g", "ugettext"), ("h", "g"), ("k", "h"),
                              ("C.m", "ugettext"), ("C.n", "C.m"), ("use_c", "C.n"),
                              ("outer.inner", "ugettext"), ("outer", "outer.inner"), ("top", "outer")}, self.edges)

    def test_property_reader_is_not_a_caller(self):  # known limit: only calls make edges, and p.label is no call
        self.assertIn(("P.label", "ugettext"), self.edges)
        self.assertFalse([e for e in self.edges if e[1] == "P.label"])


# Windows gives the main thread a 1 MB stack: Jedi's recursion overflows it before Python can raise RecursionError,
# and the process dies with no traceback (CI, Oct 27). No repo of 70dd1dabfed544b8 has such a loop (0 Jedi failures).
@unittest.skipIf(sys.platform == "win32", "a re-export loop overflows the C stack on Windows before RecursionError")
class ReexportLoop(unittest.TestCase):
    """Jedi recurses on a re-export loop: the rows are kept as ast_unresolved and the error is logged."""

    def test_kept_unresolved_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _, log = resolve_fixture(Path(tmp), LOOP_FILES)
        self.assertEqual({k: v for k, v in rows.items() if k[0].startswith("app/cyc_")},
                         {("app/cyc_a.py", 1): "ast_unresolved", ("app/cyc_a.py", 2): "ast_unresolved",
                          ("app/cyc_b.py", 1): "ast_unresolved"})
        self.assertIn("RecursionError", log)


class Helpers(unittest.TestCase):
    """score_packages and the alias hand check: the same output on every machine."""

    def test_changed_lines_ignore_user_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, user = Path(tmp) / "r", Path(tmp) / "gitconfig"
            repo.mkdir()
            parent = fixture_repo(repo, {"a.py": "x = 1\ny = 2\n"})
            (repo / "a.py").write_text("x = 1\ny = 3\n", encoding="utf-8")
            git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                                            text=True).stdout
            git("-c", "user.name=F", "-c", "user.email=f@example.com", "commit", "--quiet", "-am", "two")
            user.write_text("[diff]\n\tnoprefix = true\n")
            with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(user)}):
                self.assertEqual(dict(changed_lines(repo, parent, git("rev-parse", "HEAD").strip())), {"a.py": {2}})

    def test_alias_hand_check_saves_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "check.csv"
            HA.save(out, {1: {c: ("é" if c == "note" else "1") for c in HA.COLUMNS}})
            self.assertIn("é", out.read_bytes().decode("utf-8"))   # not cp1252 on Windows
            self.assertEqual(HA.load(out)[1]["note"], "é")


RUN_ID = os.environ.get("MERIDIAN_RUN_ID", "dev")
DJANGO = data_root() / "snapshots" / RUN_ID / "repos" / "django__django"
KEY = HERE.parent / "week1" / "pair1" / "results" / "alias_resolution.csv"


EDIT = HERE.parent / "week1" / "pair1" / "results" / "edit_sites.csv"


@unittest.skipUnless(DJANGO.exists() and KEY.exists() and EDIT.exists(), f"no django/django in snapshots/{RUN_ID}/")
class DjangoAnswerKey(unittest.TestCase):
    """Week 1's answer keys: the 542 alias calls, and every name on the 265 edit lines plus the 2 in code.sample."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config(HERE / "meridian.toml")
        cls.symbols = R.tracked(cls.cfg)["django/django"]
        cls.repo = R.Repo(DJANGO, cls.symbols)

    def test_542_of_542(self):
        rows = R.resolve_aliases(RUN_ID, "django/django", DJANGO, self.symbols,
                                 self.cfg["scanner"]["skip_paths"], self.repo)
        with open(KEY, newline="", encoding="utf-8") as f:
            key = {(r["file"], int(r["line"]), int(r["col"])): r["expected_symbol"] for r in csv.DictReader(f)}
        got = {(r["path"], r["line"], r["col"]): r for r in rows}
        self.assertEqual(set(got), set(key))
        right = [k for k, r in got.items() if r["detection"] == "ast_resolved"
                 and r["resolved_to"] == f"django.utils.translation.{key[k]}"]
        self.assertEqual(len(right), self.cfg["datasets"]["django"]["answer_key"]["alias_calls"])  # 542

    def test_every_edit_line_resolves(self):
        cands = scanned(RUN_ID, "django/django", DJANGO, self.symbols, self.cfg["scanner"])
        rows = R.resolve_candidates(RUN_ID, "django/django", DJANGO, self.symbols, cands, self.repo)
        resolved = [r for r in rows if r["detection"] == "ast_resolved"]
        with open(EDIT, newline="", encoding="utf-8") as f:
            edit = {(r["file"], int(r["line"])) for r in csv.DictReader(f)}
        lines = {(r["path"], r["line"]) for r in resolved}
        key = self.cfg["datasets"]["django"]["answer_key"]
        self.assertEqual(len(edit & lines), key["edit_lines"])                                             # 265
        self.assertEqual(len({l for l in lines if l[0].endswith(".sample")}), key["outside_py"])           # 2
        self.assertTrue(all(r["resolved_to"] == f"django.utils.translation.{r['symbol']}" for r in resolved))
        self.assertEqual(len(resolved), len(rows))   # Django has no look-alikes: precision and recall are both 1


SNAP = data_root() / "snapshots" / RUN_ID
CFG = load_config(HERE / "meridian.toml")
PACKAGES = sorted(set(R.tracked(CFG)) & {r["repo"] for r in CFG["datasets"]["django_packages"]["repos"]}
                  & set(json.loads((SNAP / "manifest.json").read_text())["repos"]
                        if (SNAP / "manifest.json").exists() else ()))


@unittest.skipUnless(PACKAGES, f"no django_packages repos in snapshots/{RUN_ID}/")
class PackagesScore(unittest.TestCase):
    """D5 bar on the packages: precision >= 0.98, and recall >= 0.95 (05-test), against their own migrations."""

    def test_precision_and_recall(self):
        manifest = json.loads((SNAP / "manifest.json").read_text())
        wanted = R.tracked(CFG)
        cands, rows = [], []
        for repo in PACKAGES:
            repo_dir = SNAP / "repos" / repo.replace("/", "__")
            c = scanned(RUN_ID, repo, repo_dir, wanted[repo], CFG["scanner"])
            r = R.Repo(repo_dir, wanted[repo], R.dependency_paths(SNAP, manifest, repo, wanted[repo]))
            rows += R.resolve_candidates(RUN_ID, repo, repo_dir, wanted[repo], c, r)
            cands += c
        _, total = score(RUN_ID, CFG, cands, rows)
        self.assertGreaterEqual(total["resolved_real"] / total["resolved"], 0.98)
        self.assertGreaterEqual(total["real_resolved"] / total["real"], 0.95)


OUT = data_root() / "out" / RUN_ID


@unittest.skipUnless((OUT / "call_edge.jsonl").exists() and (OUT / "resolution.jsonl").exists() and SNAP.is_dir(),
                     f"no resolve output in out/{RUN_ID}/ or no snapshots/{RUN_ID}/")
class TeamRunEdges(unittest.TestCase):
    """Every call_edge row of a real run, checked by plain text and the rows, not by Jedi or tree-sitter."""

    @classmethod
    def setUpClass(cls):
        cls.edges = read_jsonl(OUT / "call_edge.jsonl")
        cls.res = {(r["repo"], r["path"], r["line"], r["col"]): r for r in read_jsonl(OUT / "resolution.jsonl")}
        cls.names = {n for symbols in R.tracked(CFG).values() for n in symbols}
        cls.lines = {}

    def line(self, e, n=0):
        key = (e["repo"], e["caller_path"])
        if key not in self.lines:
            path = SNAP / "repos" / e["repo"].replace("/", "__") / e["caller_path"]
            self.lines[key] = path.read_text(encoding="utf-8", errors="replace").splitlines() + [""]
        return self.lines[key][e["line"] - 1 + n]

    def test_no_edge_on_an_import_line(self):
        self.assertFalse([e for e in self.edges if re.match(r"\s*(from\s+\S+\s+)?import\b", self.line(e))])

    def test_a_use_edge_sits_on_a_confirmed_use(self):
        uses = [e for e in self.edges if e["callee_scope"] in self.names]
        self.assertTrue(uses)
        self.assertFalse([e for e in uses if self.res.get((e["repo"], e["caller_path"], e["line"], e["col"]), {})
                          .get("detection") != "ast_resolved"])

    def test_a_caller_edge_calls_its_callee_by_name(self):  # an __init__: Class(...), super().__init__(...)
        bad = []
        for e in self.edges:
            *cls, name = e["callee_scope"].split(".")
            if name in self.names:
                continue
            names = [name] + cls[-1:] if name == "__init__" else [name]
            text = self.line(e) + self.line(e, 1)   # a call can break its line after the name
            if not any(re.search(r"\b" + re.escape(n) + r"\s*\(", text) for n in names):
                bad.append(e)
        self.assertEqual(bad, [])

    def test_every_edge_is_within_call_edge_depth_of_a_use(self):
        into = {}
        for e in self.edges:
            into.setdefault((e["repo"], e["callee_path"], e["callee_scope"]), []).append(e)
        level, frontier = {}, [(e, 1) for e in self.edges if e["callee_scope"] in self.names]
        while frontier:
            nxt = []
            for e, d in frontier:
                key = json.dumps(e, sort_keys=True)
                if key not in level:
                    level[key] = d
                    nxt += [(up, d + 1) for up in into.get((e["repo"], e["caller_path"], e["caller_scope"]), [])]
            frontier = nxt
        self.assertEqual(len(level), len(self.edges))
        self.assertLessEqual(max(level.values()), CFG["resolver"]["call_edge_depth"])


if __name__ == "__main__":
    unittest.main()
