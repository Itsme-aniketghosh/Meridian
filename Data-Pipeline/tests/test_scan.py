"""D3-D6 tests: the scanner on a fixture repo (every golden case), the one command, and Django's answer key.

    python -m unittest tests.test_scan -v      # from Data-Pipeline/

The Django test needs a snapshot: $MERIDIAN_RUN_ID (default: dev). It is skipped when django/django isn't in it:

    MERIDIAN_RUN_ID=973531f7fd4cfd5e python -m unittest tests.test_scan -v
"""
import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import meridian.scan as S
import meridian.writer as W
import tests.hand_check_edit_sites as H
from meridian.common import data_root, load_config, read_jsonl

RUN = "abcdef0123456789"
SYMBOLS = {"ugettext": "lib.translation", "ugettext_lazy": "lib.translation", "is_anonymous": "lib.auth"}
MARKERS = ["DeprecationWarning", "PendingDeprecationWarning", r"RemovedIn\w*Warning", "(?i)deprecated"]
SCANNER = {
    "skip_paths": ["docs/"],
    "embedded_python": ["*.sample"],
    "keep_name_only": True,
    "edit_roles": ["import", "call", "alias"],
    "deprecation_markers": MARKERS,
    "junk": {"test": ["tests/", "test_*.py", "*_tests.py"], "example": ["examples/"],
             "migration_marker": "class Migration(migrations.Migration)"},
}

FILES = {
    "lib/__init__.py": "",
    "lib/translation.py": (
        "def gettext(m):\n"
        "    return m\n"
        "\n"
        "ugettext = gettext\n"                         # 4 definition: the old name made, not used
        "ugettext_lazy = gettext\n"                    # 5 definition
    ),
    "lib/compat.py": "from lib.translation import ugettext\n",   # 1 import: the re-export
    "app/__init__.py": "",
    "app/direct.py": (
        "from lib.translation import ugettext\n"       # 1 import
        "from lib import translation\n"
        "\n"
        "A = ugettext('a')\n"                          # 4 call
        "B = translation.ugettext('b')\n"              # 5 call through an attribute
        "C = translation.ugettext\n"                   # 6 attribute, not called
        "# ugettext in a comment\n"                    # 7 no row
        "D = 'ugettext'\n"                             # 8 no row: a string
        "__all__ = ['ugettext']\n"                     # 9 no row: a string
        "E = getattr(translation, 'ugettext')('e')\n"  # 10 no row: getattr is an expected miss
    ),
    "app/aliases.py": (
        "from lib.translation import ugettext_lazy as _\n"   # 1 import
        "T = ugettext_lazy\n"                                # 2 alias: _ = ugettext_lazy style
        "X = _('x')\n"                                       # 3 no row: alias calls are the resolver's
    ),
    "app/multiline.py": (
        "from lib.translation import (\n"
        "    ugettext,\n"                              # 2 import: continuation line
        "    ugettext_lazy,\n"                         # 3 import
        ")\n"
    ),
    "app/via_compat.py": (
        "from lib.compat import ugettext\n"            # 1 import through the re-export
        "Y = ugettext('y')\n"                          # 2 call
    ),
    "app/names.py": (
        "def test_ungettext_lazy():\n"                 # 1 no row: a longer name
        "    pass\n"
        "def view(request, ugettext=None):\n"          # 3 reference: a parameter name
        "    return dict(ugettext=1)\n"                # 4 reference: a keyword-argument name, scope view
    ),
    "app/methods.py": (
        "user.is_anonymous = lambda: True\n"           # 1 attribute: a method use that isn't a call
        "if user.is_anonymous():\n"                    # 2 call
        "    pass\n"
    ),
    "app/unicode.py": "S = '\u00e9'; T = ugettext('t')\n",   # 1 call: col 13 in characters, 14 in bytes
    "app/tests/helpers.py": "from lib.translation import ugettext\n",            # junk test: tests/ deep in the path
    "examples/demo.py": "from lib.translation import ugettext\n",                # junk example
    "app/migrations/0002_x.py": (
        "from lib.translation import ugettext\n"       # junk migration
        "class Migration(migrations.Migration):\n"
        "    pass\n"
    ),
    "tests/i18n/code.sample": (
        "from lib.translation import ugettext\n"       # embedded, junk test
        "Z = ugettext('z')\n"
    ),
    "app/broken.py": "from lib.translation import ugettext\ndef (:\n",          # parse failure: excluded
    "docs/conf.py": "from lib.translation import ugettext\n",                    # skipped
    "app/Test_Case.py": "from lib.translation import ugettext\n",               # not test_*.py: case counts on every OS
    "app/scopes.py": (
        "def get_text_list(list_, last_word=ugettext_lazy('or')):\n"   # 1 call, <module>: a default runs outside
        "    return ugettext('x')\n"                                    # 2 call, get_text_list
        "class C:\n"
        "    def ugettext(self):\n"                                     # 4 definition, C: a def's name is outside it
        "        pass\n"
    ),
    "app/excepts.py": (
        "try:\n"
        "    A = ugettext('a')\n"                      # 2 in_except 0
        "except ImportError:\n"
        "    A = ugettext_lazy('a')\n"                 # 4 in_except 1
        "finally:\n"
        "    if A:\n"
        "        B = ugettext('b')\n"                  # 7 in_except 1: any depth
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    def g():\n"
        "        return ugettext('g')\n"               # 12 in_except 0: g's own body isn't in an except
    ),
    "app/rel/__init__.py": "",
    "app/rel/mod.py": (                                # module_import only
        "import os.path, json as j\n"
        "from . import sibling\n"
        "from ..direct import A, B as b\n"
        "from .x import (y,\n"
        "    w)\n"
        "from lib import *\n"
        "from __future__ import annotations\n"
        "def f():\n"
        "    import lib.translation\n"
    ),
    "lib/more.py": (                                   # deprecation: what must not count, and the qualname
        "import warnings\n"
        "def arg(x=None):\n"
        "    if x is not None:\n"
        "        warnings.warn('x is going', DeprecationWarning)\n"           # 4 no row: an argument, not arg()
        "class Msg:\n"
        "    def m(self):\n"
        "        warnings.warn(self.deprecated_msg, RuntimeWarning)\n"        # 7 no row: a name, not a message
        "def g():\n"
        "    warnings.warn('Deprecated: use h')\n"                         # 9 g: any case
    ),
    "lib/pkg/__init__.py": (
        "import warnings\n"
        "class K:\n"
        "    def __init__(self):\n"
        "        warnings.warn('K is going', DeprecationWarning)\n"         # 4 K.__init__, reached by calling lib.pkg.K
    ),
    "pkg2/m.py": "from ... import x\n",                  # above the repo root: kept as written
    "app/group.py": (
        "from lib.translation import ugettext\n"
        "try:\n"
        "    pass\n"
        "except* ValueError:\n"
        "    ugettext('a')\n"                                              # 5 in_except 1: except* too
    ),
    "lib/old.py": (                                    # deprecation only
        "import warnings\n"
        "from warnings import warn\n"
        "\n"
        "def old():\n"
        "    warnings.warn('old() is going', RemovedInLib2Warning, stacklevel=2)\n"   # 5 old: the category
        "\n"
        "class Legacy:\n"
        "    def m(self):\n"
        "        warn('m is deprecated')\n"                                       # 9 Legacy.m: the message
        "\n"
        "@deprecated('use new')\n"                                               # 11 gone: the decorator
        "def gone():\n"
        "    pass\n"
        "\n"
        "def fine():\n"
        "    warnings.warn('careful', UserWarning)\n"                             # no row
        "    warnings.warn('x', category=PendingDeprecationWarning)\n"            # 17 fine: whole name, not DeprecationWarning
    ),
}

EXPECTED = {   # (path, line): (symbol, role)
    ("lib/translation.py", 4): ("ugettext", "definition"),
    ("lib/translation.py", 5): ("ugettext_lazy", "definition"),
    ("lib/compat.py", 1): ("ugettext", "import"),
    ("app/direct.py", 1): ("ugettext", "import"),
    ("app/direct.py", 4): ("ugettext", "call"),
    ("app/direct.py", 5): ("ugettext", "call"),
    ("app/direct.py", 6): ("ugettext", "attribute"),
    ("app/aliases.py", 1): ("ugettext_lazy", "import"),
    ("app/aliases.py", 2): ("ugettext_lazy", "alias"),
    ("app/multiline.py", 2): ("ugettext", "import"),
    ("app/multiline.py", 3): ("ugettext_lazy", "import"),
    ("app/via_compat.py", 1): ("ugettext", "import"),
    ("app/via_compat.py", 2): ("ugettext", "call"),
    ("app/names.py", 3): ("ugettext", "reference"),
    ("app/names.py", 4): ("ugettext", "reference"),
    ("app/methods.py", 1): ("is_anonymous", "attribute"),
    ("app/methods.py", 2): ("is_anonymous", "call"),
    ("app/unicode.py", 1): ("ugettext", "call"),
    ("app/tests/helpers.py", 1): ("ugettext", "import"),
    ("examples/demo.py", 1): ("ugettext", "import"),
    ("app/migrations/0002_x.py", 1): ("ugettext", "import"),
    ("tests/i18n/code.sample", 1): ("ugettext", "import"),
    ("tests/i18n/code.sample", 2): ("ugettext", "call"),
    ("app/Test_Case.py", 1): ("ugettext", "import"),
    ("app/scopes.py", 1): ("ugettext_lazy", "call"),
    ("app/scopes.py", 2): ("ugettext", "call"),
    ("app/scopes.py", 4): ("ugettext", "definition"),
    ("app/excepts.py", 2): ("ugettext", "call"),
    ("app/excepts.py", 4): ("ugettext_lazy", "call"),
    ("app/excepts.py", 7): ("ugettext", "call"),
    ("app/excepts.py", 12): ("ugettext", "call"),
    ("app/group.py", 1): ("ugettext", "import"),
    ("app/group.py", 5): ("ugettext", "call"),
}

IMPORTS = {   # app/rel/mod.py: (line, module, name)
    (1, "json", ""), (1, "os.path", ""), (2, "app.rel", "sibling"), (3, "app.direct", "A"), (3, "app.direct", "B"),
    (4, "app.rel.x", "w"), (4, "app.rel.x", "y"), (6, "lib", "*"), (7, "__future__", "annotations"),
    (9, "lib.translation", ""),
}

DEPRECATIONS = {   # lib/old.py: line -> (scope, marker)
    5: ("old", "RemovedInLib2Warning"), 9: ("Legacy.m", "deprecated"), 11: ("gone", "deprecated"),
    17: ("fine", "PendingDeprecationWarning"),
}


def fixture_repo(repo_dir):
    for rel, text in FILES.items():
        p = repo_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git = lambda *a: subprocess.run(["git", "-C", str(repo_dir), *a], check=True,
                                    capture_output=True, text=True).stdout.strip()
    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--quiet", "-m", "fixture")
    return git("rev-parse", "HEAD")


def snapshot(root, sha_override=None):
    """root/snapshots/<RUN>/ with one repo, laid out the way Collect lays it out, plus a config."""
    repo_dir = root / "snapshots" / RUN / "repos" / "fix__app"
    repo_dir.mkdir(parents=True)
    sha = fixture_repo(repo_dir)
    manifest = {"run_id": RUN, "repos": {"fix/app": sha_override or sha}}
    (root / "snapshots" / RUN / "manifest.json").write_text(json.dumps(manifest))
    symbols = ", ".join(f'{{ name = "{n}", module = "{m}" }}' for n, m in SYMBOLS.items())
    markers = "[" + ", ".join(f"'{m}'" for m in MARKERS) + "]"   # TOML literal strings: \w stays \w
    config = root / "meridian.toml"
    config.write_text(
        '[run]\nhandoff_dir = "out/{run_id}"\n\n'
        f'[datasets.fixture]\nrepos = [{{ repo = "fix/app", ref = "{sha}" }}]\nsymbols = [{symbols}]\n\n'
        '[scanner]\nskip_paths = ["docs/"]\nembedded_python = ["*.sample"]\nkeep_name_only = true\n'
        'edit_roles = ["import", "call", "alias"]\n'
        f'deprecation_markers = {markers}\n'
        'junk = { test = ["tests/", "test_*.py", "*_tests.py"], example = ["examples/"], '
        'migration_marker = "class Migration(migrations.Migration)" }\n'
    )
    return repo_dir, config


def run_command(root, config):
    env = {**os.environ, "MERIDIAN_DATA": str(root)}
    return subprocess.run([sys.executable, "-m", "meridian.scan", "--run-id", RUN, "--config", str(config)],
                          cwd=HERE, env=env, capture_output=True, text=True)


class Goldens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo_dir, _ = snapshot(Path(cls.tmp.name))
        cls.got = S.scan_repo(RUN, "fix/app", cls.repo_dir, SYMBOLS, SCANNER)
        cls.rows, cls.failures, cls.counts, cls.imports, cls.deprecations = cls.got
        cls.by_line = {(r["path"], r["line"]): r for r in cls.rows}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exactly_the_expected_rows(self):
        # also proves the non-rows: comments, strings, __all__, getattr, longer names, alias calls, docs/
        self.assertEqual(len(self.rows), len(EXPECTED))
        self.assertEqual(sorted(self.by_line), sorted(EXPECTED))

    def test_symbol_and_role(self):
        for key, (symbol, role) in EXPECTED.items():
            with self.subTest(key=key):
                self.assertEqual((self.by_line[key]["symbol"], self.by_line[key]["role"]), (symbol, role))

    def test_columns_are_characters(self):
        self.assertEqual(self.by_line[("app/unicode.py", 1)]["col"], 13)
        self.assertEqual(self.by_line[("app/multiline.py", 2)]["col"], 4)

    def test_scope(self):
        want = {("app/names.py", 4): "view", ("app/direct.py", 4): "<module>", ("app/scopes.py", 1): "<module>",
                ("app/scopes.py", 2): "get_text_list", ("app/scopes.py", 4): "C"}
        for key, scope in want.items():
            with self.subTest(key=key):
                self.assertEqual(self.by_line[key]["scope"], scope)

    def test_in_except(self):
        got = {line: r["in_except"] for (path, line), r in self.by_line.items() if path == "app/excepts.py"}
        self.assertEqual(got, {2: 0, 4: 1, 7: 1, 12: 0})
        self.assertEqual(self.by_line[("app/direct.py", 4)]["in_except"], 0)

    def test_module_import(self):
        self.assertEqual({(r["line"], r["module"], r["name"]) for r in self.imports if r["path"] == "app/rel/mod.py"}, IMPORTS)
        is_test = {r["path"]: r["is_test"] for r in self.imports}
        self.assertEqual((is_test["app/tests/helpers.py"], is_test["tests/i18n/code.sample"], is_test["app/direct.py"],
                          is_test["app/Test_Case.py"]), (1, 1, 0, 0))
        self.assertNotIn("app/broken.py", is_test)   # parse failure: no rows
        self.assertNotIn("docs/conf.py", is_test)
        keys = [(r["path"], r["line"], r["module"], r["name"]) for r in self.imports]
        self.assertEqual(keys, sorted(set(keys)))
        self.assertTrue(all(list(r) == S.IMPORT_COLUMNS for r in self.imports))

    def test_deprecation(self):
        self.assertEqual({r["line"]: (r["scope"], r["marker"]) for r in self.deprecations if r["path"] == "lib/old.py"},
                         DEPRECATIONS)
        self.assertEqual({r["path"] for r in self.deprecations}, {"lib/old.py", "lib/more.py", "lib/pkg/__init__.py"})
        more = {(r["path"], r["line"]): (r["scope"], r["qualname"], r["marker"]) for r in self.deprecations if r["path"] != "lib/old.py"}
        self.assertEqual(more, {("lib/more.py", 9): ("g", "lib.more.g", "Deprecated"),
                                ("lib/pkg/__init__.py", 4): ("K.__init__", "lib.pkg.K", "DeprecationWarning")})
        self.assertEqual({r["qualname"] for r in self.deprecations if (r["path"], r["line"]) == ("lib/old.py", 5)}, {"lib.old.old"})

    def test_relative_import_above_root_kept_as_written(self):
        self.assertEqual({(r["module"], r["name"]) for r in self.imports if r["path"] == "pkg2/m.py"}, {("...", "x")})

    def test_except_group_counts(self):
        self.assertEqual(self.by_line[("app/group.py", 5)]["in_except"], 1)
        self.assertTrue(all(list(r) == S.DEPRECATION_COLUMNS for r in self.deprecations))

    def test_keep_name_only_off(self):
        rows = S.scan_repo(RUN, "fix/app", self.repo_dir, SYMBOLS, {**SCANNER, "keep_name_only": False})[0]
        self.assertEqual(len(rows), len(EXPECTED) - 2)
        self.assertNotIn(("app/names.py", 3), {(r["path"], r["line"]) for r in rows})

    def test_edit_roles_from_config(self):
        self.assertEqual(S.edit_lines(self.rows, "embedded", ["call"]), {("tests/i18n/code.sample", 2)})

    def test_unreadable_folder_halts(self):
        with self.assertRaises(OSError):
            S.files(self.repo_dir / "no such folder", SCANNER)

    def test_junk(self):
        want = {("app/tests/helpers.py", 1): "test", ("examples/demo.py", 1): "example",
                ("app/migrations/0002_x.py", 1): "migration", ("tests/i18n/code.sample", 1): "test",
                ("app/direct.py", 1): "none", ("app/Test_Case.py", 1): "none"}
        for key, label in want.items():
            with self.subTest(key=key):
                self.assertEqual(self.by_line[key]["junk"], label)

    def test_embedded_python(self):
        kinds = {r["path"]: r["file_kind"] for r in self.rows}
        self.assertEqual(kinds["tests/i18n/code.sample"], "embedded")
        self.assertEqual(kinds["app/direct.py"], "py")

    def test_parse_failure_is_a_row_and_excluded(self):
        self.assertEqual([f["path"] for f in self.failures], ["app/broken.py"])
        self.assertFalse([r for r in self.rows if r["path"] == "app/broken.py"])
        self.assertEqual(self.counts["names_in_failed_files"], 1)

    def test_keyword_and_parameter_names_counted(self):
        self.assertEqual(self.counts["name_only"], 2)

    def test_text_is_the_stripped_line(self):
        self.assertEqual(self.by_line[("app/multiline.py", 2)]["text"], "ugettext,")

    def test_columns_match_the_contract(self):
        self.assertTrue(all(list(r) == S.COLUMNS for r in self.rows))
        self.assertTrue(all(r["run_id"] == RUN for r in self.rows))

    def test_same_input_same_rows(self):
        self.assertEqual(S.scan_repo(RUN, "fix/app", self.repo_dir, SYMBOLS, SCANNER), self.got)

    def test_hand_check_saves_utf8(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(H, "OUT", Path(tmp) / "key.csv"):
            H.save({("a.py", 1): {"path": "a.py", "line": 1, "note": "é"}})
            self.assertIn("é", H.OUT.read_bytes().decode("utf-8"))   # not cp1252 on Windows


class OneCommand(unittest.TestCase):
    def test_writes_every_table_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_dir, config = snapshot(root)
            r = run_command(root, config)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = root / "out" / RUN
            rows = read_jsonl(out / "candidate.jsonl")
            fails = read_jsonl(out / "parse_failure.jsonl")
            self.assertEqual(len(rows), len(EXPECTED))
            self.assertEqual([f["path"] for f in fails], ["app/broken.py"])
            schema = (HERE / "schema.sql").read_text(encoding="utf-8")
            db = sqlite3.connect(":memory:")
            db.executescript(schema)
            for t, key in S.KEYS.items():   # sorted by primary key, and every row fits schema.sql (CHECKs included)
                checked = W.read_rows(out / f"{t}.jsonl", W.tables(schema)[t], RUN)
                self.assertEqual([r for _, r in checked], read_jsonl(out / f"{t}.jsonl"), t)
                for _, r in checked:
                    db.execute(f"INSERT INTO {t} ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
            db.close()
            first = {t: (out / f"{t}.jsonl").read_bytes() for t in S.KEYS}
            self.assertEqual(run_command(root, config).returncode, 0)
            self.assertEqual({t: (out / f"{t}.jsonl").read_bytes() for t in S.KEYS}, first)   # same run, same bytes

    def test_wrong_checkout_halts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, config = snapshot(root, sha_override="0" * 40)
            r = run_command(root, config)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("checkout is at", r.stdout + r.stderr)

    def test_no_snapshot_halts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "meridian.toml"
            config.write_text('[run]\nhandoff_dir = "out/{run_id}"\n')
            r = run_command(root, config)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no snapshot", r.stdout + r.stderr)


RUN_ID = os.environ.get("MERIDIAN_RUN_ID", "dev")
DJANGO = data_root() / "snapshots" / RUN_ID / "repos" / "django__django"
KEY = HERE.parent / "week1" / "pair1" / "results" / "edit_sites.csv"


@unittest.skipUnless(DJANGO.exists() and KEY.exists(), f"no django/django in snapshot {RUN_ID}")
class DjangoAnswerKey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = load_config(HERE / "meridian.toml")
        symbols = S.tracked(cfg)["django/django"]
        cls.key = cfg["datasets"]["django"]["answer_key"]
        cls.roles = cfg["scanner"]["edit_roles"]
        cls.rows, cls.failures, *_ = S.scan_repo(RUN_ID, "django/django", DJANGO, symbols, cfg["scanner"])

    def test_265_of_265_and_0_false(self):
        with open(KEY, newline="", encoding="utf-8") as f:
            key = {(r["file"], int(r["line"])) for r in csv.DictReader(f)}
        got = S.edit_lines(self.rows, "py", self.roles)
        self.assertEqual(len(key), self.key["edit_lines"])
        self.assertEqual(sorted(key - got), [], "missed")
        self.assertEqual(sorted(got - key), [], "false finds")

    def test_2_lines_outside_py(self):
        self.assertEqual(len(S.edit_lines(self.rows, "embedded", self.roles)), self.key["outside_py"])

    def test_no_tracked_name_lost_to_parse_failures(self):
        self.assertEqual([f["path"] for f in self.failures], ["tests/i18n/commands/not_utf8.sample"])


if __name__ == "__main__":
    unittest.main()
