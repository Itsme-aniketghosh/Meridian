"""D4 tests: the resolver's alias calls on a fixture repo, the one command, and Django's 542 answer key.

    python -m unittest tests.test_resolve -v      # from DATA PIPELINE/

The Django test needs a snapshot with django/django in it. It uses $MERIDIAN_RUN_ID (default: dev) and is
skipped when that snapshot isn't there.
"""
import csv, json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import meridian.resolve as R
import meridian.writer as W
from meridian.common import data_root, load_config

RUN = "abcdef0123456789"
SYMBOLS = {"ugettext": "lib.translation", "ugettext_lazy": "lib.translation"}

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


def fixture_repo(path):
    for rel, text in FILES.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}

    def git(*a):
        return subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True, text=True, env=env).stdout.strip()

    git("init", "--quiet", "--initial-branch=main")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.com", "commit", "--quiet", "-m", "fixture")
    return git("rev-parse", "HEAD")


def snapshot(root, run_id=RUN):
    """root/snapshots/<run_id>/ with one repo, laid out the way Collect lays it out."""
    repo_dir = root / "snapshots" / run_id / "repos" / "fix__app"
    repo_dir.mkdir(parents=True)
    sha = fixture_repo(repo_dir)
    (root / "snapshots" / run_id / "manifest.json").write_text(json.dumps({"run_id": run_id, "repos": {"fix/app": sha}}))
    config = root / "meridian.toml"
    symbols = ", ".join(f'{{ name = "{n}", module = "{m}" }}' for n, m in SYMBOLS.items())
    config.write_text(
        '[run]\nhandoff_dir = "out/{run_id}"\n\n'
        f'[datasets.fixture]\nrepos = [{{ repo = "fix/app", ref = "{sha}" }}]\nsymbols = [{symbols}]\n\n'
        '[scanner]\nskip_paths = ["docs/"]\n'
    )
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
                self.assertEqual((r["detection"], r["via"], r["resolved_to"], r["symbol"]), (detection, via, resolved_to, symbol))
                self.assertEqual(r["resolver"], R.RESOLVER)

    def test_one_hop_reaches_the_old_name_not_the_new(self):
        hops = [r for r in self.rows if r["via"] == "assign_alias"]
        self.assertEqual(len(hops), 2)
        self.assertTrue(all(r["resolved_to"] == "lib.translation.ugettext_lazy" for r in hops))  # never gettext_lazy

    def test_skipped_paths_have_no_rows(self):
        self.assertFalse([r for r in self.rows if r["path"].startswith("docs/")])

    def test_bindings(self):
        src = b"from x import ugettext as _, ugettext_lazy\nT = ugettext_lazy\nfrom y import ugettext as ugettext\n"
        binds = R.bindings(R.PY.parse(src).root_node, {"ugettext", "ugettext_lazy"})
        self.assertEqual(binds, {"_": [("ugettext", "import_alias")], "T": [("ugettext_lazy", "assign_alias")]})


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
        schema = (HERE / "schema.sql").read_text()
        rows = W.read_rows(self.root / "out" / RUN / "resolution.jsonl", W.tables(schema)["resolution"], RUN)
        self.assertEqual(len(rows), len(EXPECTED))
        db = sqlite3.connect(":memory:")
        db.executescript(schema)
        for _, r in rows:  # CHECK constraints: detection and via are closed lists
            db.execute(f"INSERT INTO resolution ({', '.join(r)}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
        db.close()

    def test_checkout_not_at_the_manifest_sha_halts(self):
        manifest = self.root / "snapshots" / RUN / "manifest.json"
        manifest.write_text(json.dumps({"run_id": RUN, "repos": {"fix/app": "0" * 40}}))
        r = self.resolve()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the manifest says", r.stderr)


RUN_ID = os.environ.get("MERIDIAN_RUN_ID", "dev")
DJANGO = data_root() / "snapshots" / RUN_ID / "repos" / "django__django"
KEY = HERE.parent / "week1" / "pair1" / "results" / "alias_resolution.csv"


@unittest.skipUnless(DJANGO.exists() and KEY.exists(), f"no django/django in snapshots/{RUN_ID}/")
class DjangoAnswerKey(unittest.TestCase):
    """Week 1's 542 alias calls: same positions, and every one resolves to its old name."""

    def test_542_of_542(self):
        cfg = load_config(HERE / "meridian.toml")
        rows = R.resolve_aliases(RUN_ID, "django/django", DJANGO, R.tracked(cfg)["django/django"],
                                 cfg["scanner"]["skip_paths"])
        with open(KEY, newline="") as f:
            key = {(r["file"], int(r["line"]), int(r["col"])): r["expected_symbol"] for r in csv.DictReader(f)}
        got = {(r["path"], r["line"], r["col"]): r for r in rows}
        self.assertEqual(set(got), set(key))
        right = [k for k, r in got.items() if r["detection"] == "ast_resolved"
                 and r["resolved_to"] == f"django.utils.translation.{key[k]}"]
        self.assertEqual(len(right), cfg["datasets"]["django"]["answer_key"]["alias_calls"])  # 542


if __name__ == "__main__":
    unittest.main()
