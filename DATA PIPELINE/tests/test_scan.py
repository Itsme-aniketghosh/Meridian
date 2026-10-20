"""D3-D6 tests: the scanner on a fixture repo (every golden case), the one command, and Django's answer key.

    python -m unittest tests.test_scan -v      # from DATA PIPELINE/

The Django test needs a snapshot: $MERIDIAN_RUN_ID (default: dev). It is skipped when django/django isn't in it:

    MERIDIAN_RUN_ID=973531f7fd4cfd5e python -m unittest tests.test_scan -v
"""
import csv, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import meridian.scan as S
from meridian.common import data_root, load_config

RUN = "abcdef0123456789"
SYMBOLS = {"ugettext": "lib.translation", "ugettext_lazy": "lib.translation", "is_anonymous": "lib.auth"}
SCANNER = {
    "skip_paths": ["docs/"],
    "embedded_python": ["*.sample"],
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
    config = root / "meridian.toml"
    config.write_text(
        '[run]\nhandoff_dir = "out/{run_id}"\n\n'
        f'[datasets.fixture]\nrepos = [{{ repo = "fix/app", ref = "{sha}" }}]\nsymbols = [{symbols}]\n\n'
        '[scanner]\nskip_paths = ["docs/"]\nembedded_python = ["*.sample"]\n'
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
        cls.rows, cls.failures, cls.counts = S.scan_repo(RUN, "fix/app", cls.repo_dir, SYMBOLS, SCANNER)
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
        self.assertEqual(self.by_line[("app/names.py", 4)]["scope"], "view")
        self.assertEqual(self.by_line[("app/direct.py", 4)]["scope"], "<module>")

    def test_junk(self):
        want = {("app/tests/helpers.py", 1): "test", ("examples/demo.py", 1): "example",
                ("app/migrations/0002_x.py", 1): "migration", ("tests/i18n/code.sample", 1): "test",
                ("app/direct.py", 1): "none"}
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
        again = S.scan_repo(RUN, "fix/app", self.repo_dir, SYMBOLS, SCANNER)
        self.assertEqual(again, (self.rows, self.failures, self.counts))


class OneCommand(unittest.TestCase):
    def test_writes_both_tables_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_dir, config = snapshot(root)
            r = run_command(root, config)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = root / "out" / RUN
            rows = [json.loads(x) for x in (out / "candidate.jsonl").read_text().splitlines()]
            fails = [json.loads(x) for x in (out / "parse_failure.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), len(EXPECTED))
            self.assertEqual([f["path"] for f in fails], ["app/broken.py"])
            keys = [(x["repo"], x["path"], x["line"], x["col"]) for x in rows]
            self.assertEqual(keys, sorted(keys))
            first = (out / "candidate.jsonl").read_bytes()
            self.assertEqual(run_command(root, config).returncode, 0)
            self.assertEqual((out / "candidate.jsonl").read_bytes(), first)   # same run, same bytes

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
        cls.rows, cls.failures, _ = S.scan_repo(RUN_ID, "django/django", DJANGO, symbols, cfg["scanner"])

    def test_265_of_265_and_0_false(self):
        with open(KEY, newline="") as f:
            key = {(r["file"], int(r["line"])) for r in csv.DictReader(f)}
        got = S.edit_lines(self.rows, "py")
        self.assertEqual(len(key), self.key["edit_lines"])
        self.assertEqual(sorted(key - got), [], "missed")
        self.assertEqual(sorted(got - key), [], "false finds")

    def test_2_lines_outside_py(self):
        self.assertEqual(len(S.edit_lines(self.rows, "embedded")), self.key["outside_py"])

    def test_no_tracked_name_lost_to_parse_failures(self):
        self.assertEqual([f["path"] for f in self.failures], ["tests/i18n/commands/not_utf8.sample"])


if __name__ == "__main__":
    unittest.main()
