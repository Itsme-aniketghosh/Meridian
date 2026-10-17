"""D4–D5 tests: the resolver on a fixture repo (candidates and alias calls), the one command, and Django's answer keys.

    python -m unittest tests.test_resolve -v      # from DATA PIPELINE/

The Django and package tests need a snapshot: $MERIDIAN_RUN_ID (default: dev). Each is skipped when its repos
aren't in that snapshot. To run them on the team data (the package test takes about 20 s):

    MERIDIAN_RUN_ID=973531f7fd4cfd5e python -m unittest tests.test_resolve -v
"""
import csv, json, os, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import meridian.resolve as R
import meridian.writer as W
from meridian.common import data_root, load_config
from tests.score_packages import score
from tests.standin_candidates import candidates as standin

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


SCANNER = {"skip_paths": ["docs/"]}


def snapshot(root, run_id=RUN):
    """root/snapshots/<run_id>/ with one repo, laid out the way Collect lays it out, and a candidate table."""
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
    (root / "out" / run_id).mkdir(parents=True)
    R.write_jsonl(root / "out" / run_id / "candidate.jsonl", standin(run_id, "fix/app", repo_dir, SYMBOLS, SCANNER))
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


class Candidates(unittest.TestCase):
    """D5: one row per candidate, scored on its own old name."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo_dir, _, _ = snapshot(Path(cls.tmp.name))
        cls.cands = standin(RUN, "fix/app", cls.repo_dir, SYMBOLS, SCANNER)
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
                self.assertEqual((c["role"], r["detection"], r["via"], r["resolved_to"]), (role, detection, via, resolved_to))

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
        schema = (HERE / "schema.sql").read_text()
        rows = W.read_rows(self.root / "out" / RUN / "resolution.jsonl", W.tables(schema)["resolution"], RUN)
        candidates = (self.root / "out" / RUN / "candidate.jsonl").read_text().splitlines()
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

    def test_checkout_not_at_the_manifest_sha_halts(self):
        manifest = self.root / "snapshots" / RUN / "manifest.json"
        manifest.write_text(json.dumps({"run_id": RUN, "repos": {"fix/app": "0" * 40}}))
        r = self.resolve()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("the manifest says", r.stderr)


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
        rows = R.resolve_aliases(RUN_ID, "django/django", DJANGO, self.symbols, self.cfg["scanner"]["skip_paths"], self.repo)
        with open(KEY, newline="") as f:
            key = {(r["file"], int(r["line"]), int(r["col"])): r["expected_symbol"] for r in csv.DictReader(f)}
        got = {(r["path"], r["line"], r["col"]): r for r in rows}
        self.assertEqual(set(got), set(key))
        right = [k for k, r in got.items() if r["detection"] == "ast_resolved"
                 and r["resolved_to"] == f"django.utils.translation.{key[k]}"]
        self.assertEqual(len(right), self.cfg["datasets"]["django"]["answer_key"]["alias_calls"])  # 542

    def test_every_edit_line_resolves(self):
        cands = standin(RUN_ID, "django/django", DJANGO, self.symbols, self.cfg["scanner"])
        rows = R.resolve_candidates(RUN_ID, "django/django", DJANGO, self.symbols, cands, self.repo)
        resolved = [r for r in rows if r["detection"] == "ast_resolved"]
        with open(EDIT, newline="") as f:
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
                  & set(json.loads((SNAP / "manifest.json").read_text())["repos"] if (SNAP / "manifest.json").exists() else ()))


@unittest.skipUnless(PACKAGES, f"no django_packages repos in snapshots/{RUN_ID}/")
class PackagesScore(unittest.TestCase):
    """D5 bar on the packages: precision >= 0.98, and recall >= 0.95 (05-test), against their own migrations."""

    def test_precision_and_recall(self):
        manifest = json.loads((SNAP / "manifest.json").read_text())
        wanted = R.tracked(CFG)
        cands, rows = [], []
        for repo in PACKAGES:
            repo_dir = SNAP / "repos" / repo.replace("/", "__")
            c = standin(RUN_ID, repo, repo_dir, wanted[repo], CFG["scanner"])
            r = R.Repo(repo_dir, wanted[repo], R.dependency_paths(SNAP, manifest, repo, wanted[repo]))
            rows += R.resolve_candidates(RUN_ID, repo, repo_dir, wanted[repo], c, r)
            cands += c
        _, total = score(RUN_ID, CFG, cands, rows)
        self.assertGreaterEqual(total["resolved_real"] / total["resolved"], 0.98)
        self.assertGreaterEqual(total["real_resolved"] / total["real"], 0.95)


if __name__ == "__main__":
    unittest.main()
