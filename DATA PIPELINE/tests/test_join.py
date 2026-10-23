"""Join tests: call-site IDs and matching across two runs (insert, move, rename, duplicate, split, removal, new),
ticket_map's four methods, coverage, and the one command. A fixture repo with six commits; run R1 is the first
commit, run R2 the last.

    python -m unittest tests.test_join -v      # from DATA PIPELINE/
"""
import contextlib, io, json, os, shutil, subprocess, sys, tempfile, textwrap, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.history as H
import meridian.join as J
import meridian.scan as S
import meridian.writer as W
from meridian.common import load_config, read_jsonl, write_jsonl

REPO, DIR = "acme/shop", "acme__shop"
IMPORT = "from django.utils.translation import ugettext\n"
CONFIG = textwrap.dedent("""\
    [run]
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    handoff_dir = "out/{run_id}"

    [datasets.shop]
    repos = [{ repo = "acme/shop", ref = "HEAD" }]
    tracker = "jira"
    key_regex = '\\bSHOP-\\d+\\b'
    symbols = [{ name = "ugettext", module = "django.utils.translation" }]

    [scanner]
    embedded_python = ["*.sample"]
    skip_paths = ["docs/"]
    keep_name_only = true
    edit_roles = ["import", "call", "alias"]
    deprecation_markers = ['DeprecationWarning']
    junk = { test = ["tests/"], example = ["examples/"], migration_marker = "class Migration(migrations.Migration)" }

    [join]
    call_site_id = ["repo", "symbol", "path", "scope", "normalized_line", "occurrence"]
    normalize_line = "strip_whitespace"
    match_order = ["git_line_map_with_renames", "same_repo_symbol_scope_normalized_line"]
    fuzzy_ticket_matching = false
    """)

# (date, message, {path: text or None to delete}); R1 = commit 1, R2 = commit 6
COMMITS = [
    ("2026-01-01T00:00:00Z", "start", {
        "a.py": IMPORT + "\ndef f():\n    return ugettext('a')\n\ndef g():\n    return ugettext('b')\n    return ugettext('b')\n",
        "old.py": IMPORT + "def h():\n    return ugettext('c')\n",
        "big.py": IMPORT + "def k():\n    return ugettext('k')\ndef m():\n    return ugettext('m')\n",
        "gone.py": IMPORT + "def z():\n    return ugettext('z')\n"}),
    ("2026-02-01T00:00:00Z", "SHOP-1 insert two comment lines", {
        "a.py": IMPORT + "# one\n# two\n\ndef f():\n    return ugettext('a')\n\ndef g():\n    return ugettext('b')\n    return ugettext('b')\n"}),
    ("2026-03-01T00:00:00Z", "move f below g, rename old.py", {
        "a.py": IMPORT + "# one\n# two\n\ndef g():\n    return ugettext('b')\n    return ugettext('b')\n\ndef f():\n    return ugettext('a')\n",
        "old.py": None,
        "new.py": IMPORT + "def h():\n    return ugettext('c')\n"}),
    ("2026-04-01T00:00:00Z", "split big.py", {
        "big.py": IMPORT + "def k():\n    return ugettext('k')\n",
        "small.py": IMPORT + "def m():\n    return ugettext('m')\n"}),
    ("2026-05-01T00:00:00Z", "remove z's call", {
        "gone.py": IMPORT + "def z():\n    return 'z'\n"}),
    ("2026-06-01T00:00:00Z", "SHOP-1 a new call and a third duplicate", {
        "a.py": IMPORT + "# one\n# two\n\ndef g():\n    return ugettext('b')\n    return ugettext('b')\n    return ugettext('b')\n"
                         "    return ugettext('new')\n\ndef f():\n    return ugettext('a')\n",
        "m1/src/acme/Foo.java": "class Foo {}\n", "m2/src/acme/Foo.java": "class Foo {}\n"}),
]

TICKETS = {  # key: (summary, description)
    "SHOP-1": ("Add the new call", ""),
    "SHOP-2": ("Rename", "see new.py, and nothing.py"),
    "SHOP-3": ("Crash", 'Traceback:\n  File "/srv/app/a.py", line 12, in f\n'),
    "SHOP-4": ("Port off django.utils.translation.ugettext", ""),
    "SHOP-5": ("Unrelated", "no code here"),
    "SHOP-6": ("Java crash", "at acme.Foo.run(Foo.java:3)"),
}


def sh(co, *args, env=None):
    return subprocess.run(["git", "-C", str(co), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **(env or {})}).stdout


def make_repo(src):
    src.mkdir(parents=True)
    sh(src, "init", "-q", "-b", "main")
    shas = []
    for date, msg, files in COMMITS:
        for path, text in files.items():
            p = src / path
            if text is None:
                if path == "old.py":
                    sh(src, "mv", "old.py", "new.py")  # git sees a rename
                    (src / "new.py").write_text(COMMITS[2][2]["new.py"])
                else:
                    p.unlink()
                continue
            if path == "new.py":
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        sh(src, "add", "-A")
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_NAME": "Ann", "GIT_AUTHOR_EMAIL": "ann@example.com",
               "GIT_COMMITTER_NAME": "Ann", "GIT_COMMITTER_EMAIL": "ann@example.com"}
        sh(src, "commit", "-q", "-m", msg, env=env)
        shas.append(sh(src, "rev-parse", "HEAD").strip())
    return shas


def build_run(root, cfg, src, run_id, sha, snapshot_at, tickets=(), commit_ticket=()):
    co = root / "snapshots" / run_id / "repos" / DIR
    subprocess.run(["git", "clone", "-q", str(src), str(co)], check=True)
    sh(co, "checkout", "-q", sha)
    (root / "snapshots" / run_id / "manifest.json").write_text(json.dumps({"run_id": run_id, "repos": {REPO: sha}}))
    out = root / "out" / run_id
    out.mkdir(parents=True)
    cands, failures, *_ = S.scan_repo(run_id, REPO, co, {"ugettext": "django.utils.translation"}, cfg["scanner"])
    commits, files = H.read_repo(co, REPO, sha, run_id)
    tables = {
        "run": [{"run_id": run_id, "snapshot_at": snapshot_at, "repo_shas": json.dumps({REPO: sha})}],
        "candidate": cands, "parse_failure": failures, "git_commit": commits, "commit_file": files,
        "resolution": [{k: c[k] for k in ("run_id", "repo", "path", "line", "col", "symbol")} |
                       {"detection": "ast_resolved", "via": "direct", "resolved_to": "django.utils.translation.ugettext",
                        "resolver": "fixture", "receiver_from": None} for c in cands],
        "file_owner": [{"run_id": run_id, "repo": REPO, "path": "a.py", "owner_team": "checkout", "share": 1.0,
                        "top_person_id": None, "n_commits": 1, "last_commit_at": None, "basis": "repo"}],
        "file_bug_fixes": [{"run_id": run_id, "repo": REPO, "path": "a.py", "bug_fixes": 2, "fix_keys": "[]",
                            "window_start": "", "window_end": snapshot_at}],
        "ticket": [{"run_id": run_id, "key": k, "tracker": "jira", "summary": s, "description": d} for k, (s, d) in tickets],
        "commit_ticket": list(commit_ticket),
    }
    for name, rows in tables.items():
        write_jsonl(out / f"{name}.jsonl", rows)
    return out


def quiet(f, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return f(*a, **k)


class TwoRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "meridian.toml").write_text(CONFIG)
        cls.config = cls.tmp / "meridian.toml"
        cls.cfg = load_config(cls.config)
        cls.shas = make_repo(cls.tmp / "src")
        build_run(cls.tmp, cls.cfg, cls.tmp / "src", "R1", cls.shas[0], COMMITS[0][0])
        cls.r1 = quiet(J.main, "R1", cls.config, cls.tmp)[0]
        ct = [{"run_id": "R2", "repo": REPO, "sha": cls.shas[5], "key": "SHOP-1", "source": "subject"}]
        cls.out2 = build_run(cls.tmp, cls.cfg, cls.tmp / "src", "R2", cls.shas[5], COMMITS[5][0], sorted(TICKETS.items()), ct)
        cls.r2, cls.tmap, cls.cov = quiet(J.main, "R2", cls.config, cls.tmp, prev_run="R1")
        last = J.newest(cls.r2)
        cls.now = {(s["path"], s["line"]): s for s in last.values() if s["status"] == "present"}
        cls.first = {(s["path"], s["line"]): s for s in cls.r1}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def same(self, before, after):
        self.assertEqual(self.first[before]["call_site_id"], self.now[after]["call_site_id"])

    def test_first_run_writes_every_site_present(self):
        self.assertEqual(len(self.r1), 11)  # 4 imports + 7 calls
        self.assertTrue(all(s["status"] == "present" and s["run_id"] == "R1" for s in self.r1))
        self.assertTrue(all(s["effective_date"] == COMMITS[0][0] for s in self.r1))
        self.assertEqual(len({s["call_site_id"] for s in self.r1}), 11)

    def test_insert_lines_above_keeps_the_id(self):   # g's calls shift down 2, up 3 (f moved away): git's line map
        self.same(("a.py", 7), ("a.py", 6))
        self.assertEqual(self.now[("a.py", 6)]["run_id"], "R2")  # the line changed, so a new version

    def test_move_keeps_the_id(self):                 # f's call moved below g: same scope and text
        self.same(("a.py", 4), ("a.py", 12))
        self.assertEqual(self.now[("a.py", 12)]["effective_date"], COMMITS[2][0])  # blame: the move

    def test_rename_keeps_the_id(self):
        self.same(("old.py", 1), ("new.py", 1))
        self.same(("old.py", 3), ("new.py", 3))

    def test_duplicates_keep_their_ids_and_a_new_one_gets_its_own(self):
        self.same(("a.py", 7), ("a.py", 6))
        self.same(("a.py", 8), ("a.py", 7))
        ids = {self.now[("a.py", n)]["call_site_id"] for n in (6, 7, 8)}
        self.assertEqual(len(ids), 3)

    def test_split_file_keeps_the_moved_sites_id(self):
        self.same(("big.py", 3), ("big.py", 3))
        self.same(("big.py", 5), ("small.py", 3))
        self.assertNotIn(self.now[("small.py", 1)]["call_site_id"], {s["call_site_id"] for s in self.r1})

    def test_removed_site_is_a_removed_version_dated_by_its_commit(self):
        rid = self.first[("gone.py", 3)]["call_site_id"]
        rows = [r for r in self.r2 if r["call_site_id"] == rid]
        self.assertEqual([r["status"] for r in rows], ["present", "removed"])
        self.assertEqual(rows[1]["effective_date"], COMMITS[4][0])
        self.assertEqual(rows[1]["run_id"], "R2")

    def test_new_site_is_dated_by_its_commit(self):
        s = self.now[("a.py", 9)]
        self.assertEqual(s["effective_date"], COMMITS[5][0])
        self.assertNotIn(s["call_site_id"], {r["call_site_id"] for r in self.r1})

    def test_append_only_unchanged_sites_get_no_new_version(self):
        unchanged = self.first[("gone.py", 1)]["call_site_id"]  # the import stays at gone.py:1
        self.assertEqual([r["run_id"] for r in self.r2 if r["call_site_id"] == unchanged], ["R1"])
        self.assertEqual(sum(r["run_id"] == "R1" for r in self.r2), len(self.r1))  # history kept as written

    def test_owner_and_bug_fixes_from_the_file(self):
        self.assertEqual(self.now[("a.py", 6)]["owner_team"], "checkout")
        self.assertEqual(self.now[("a.py", 6)]["bug_fixes"], 2)
        self.assertEqual(self.now[("new.py", 3)]["owner_team"], "unowned")
        self.assertEqual(self.now[("new.py", 3)]["bug_fixes"], 0)

    def test_ticket_map_methods(self):
        got = {(r["key"], r["target_kind"], r["target"], r["method"]) for r in self.tmap}
        site = lambda path, line: self.now[(path, line)]["call_site_id"]
        self.assertIn(("SHOP-1", "path", f"{REPO}:a.py", "commit_key"), got)
        self.assertIn(("SHOP-2", "path", f"{REPO}:new.py", "path"), got)
        self.assertNotIn(("SHOP-2", "path", f"{REPO}:nothing.py", "path"), got)
        self.assertIn(("SHOP-3", "path", f"{REPO}:a.py", "stack_trace"), got)
        self.assertIn(("SHOP-3", "call_site", site("a.py", 12), "stack_trace"), got)
        symbol = {t for k, kind, t, m in got if k == "SHOP-4" and m == "symbol"}
        self.assertEqual(len(symbol), len([s for s in self.now.values()]))  # every present ugettext site
        self.assertIn(("SHOP-5", "unmapped", "", "none"), got)
        self.assertIn(("SHOP-6", "unmapped", "", "none"), got)  # two Foo.java: ambiguous, not a match
        self.assertEqual({k for k, *_ in got}, set(TICKETS))

    def test_coverage(self):
        cov = {c["metric"]: (c["numerator"], c["denominator"]) for c in self.cov}
        self.assertEqual(cov["call_sites_resolved"], (len(self.now), len(self.now)))
        self.assertEqual(cov["call_sites_matched"], (10, 11))
        self.assertEqual(cov["call_sites_removed"], (1, 11))
        self.assertEqual(cov["stack_frames_ambiguous"][0], 1)
        self.assertEqual(cov["tickets_mapped"], (4, 6))

    def test_second_runs_block_loads_with_the_first_runs_versions(self):
        block = self.tmp / "block" / "R2"
        block.mkdir(parents=True)
        shutil.copy(self.out2 / "call_site.jsonl", block)
        (block / "run.jsonl").write_text(json.dumps({"run_id": "R2", "snapshot_at": COMMITS[5][0], "repo_shas": "{}",
                                                     "source_hashes": "{}", "config_hash": "x", "image_digest": "x",
                                                     "status": "complete"}) + "\n")
        W.write(block, self.tmp / "block.sqlite")
        import sqlite3
        got = dict(sqlite3.connect(self.tmp / "block.sqlite").execute("SELECT run_id, count(*) FROM call_site GROUP BY run_id"))
        self.assertEqual(got["R1"], len(self.r1))
        self.assertEqual(got["R2"], sum(r["run_id"] == "R2" for r in self.r2))

    def test_rerun_gives_the_same_bytes(self):
        before = {p.name: p.read_bytes() for p in self.out2.glob("*.jsonl")}
        quiet(J.main, "R2", self.config, self.tmp, prev_run="R1")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out2.glob("*.jsonl")})


class Units(unittest.TestCase):
    def test_line_map(self):
        diff = J.parse_diff(textwrap.dedent("""\
            diff --git a/x.py b/x.py
            index 1..2 100644
            --- a/x.py
            +++ b/x.py
            @@ -2,0 +3,2 @@
            +a
            +b
            @@ -5 +7 @@
            -old
            +new
            @@ -9 +11,0 @@
            --- not a header
            diff --git a/y.py b/z.py
            similarity index 100%
            rename from y.py
            rename to z.py
            diff --git a/gone.py b/gone.py
            deleted file mode 100644
            --- a/gone.py
            +++ /dev/null
            @@ -1 +0,0 @@
            -x
            """))
        self.assertEqual(J.map_line(diff, "x.py", 2), ("x.py", 2))
        self.assertEqual(J.map_line(diff, "x.py", 3), ("x.py", 5))
        self.assertIsNone(J.map_line(diff, "x.py", 5))
        self.assertEqual(J.map_line(diff, "x.py", 6), ("x.py", 8))
        self.assertIsNone(J.map_line(diff, "x.py", 9))            # the deleted '-- not a header' line
        self.assertEqual(J.map_line(diff, "x.py", 10), ("x.py", 11))
        self.assertEqual(J.map_line(diff, "y.py", 4), ("z.py", 4))
        self.assertIsNone(J.map_line(diff, "gone.py", 1))
        self.assertEqual(J.map_line(diff, "other.py", 9), ("other.py", 9))

    def test_only_edit_roles_the_resolver_kept(self):
        c = lambda line, role: dict(repo=REPO, path="p.py", line=line, col=0, scope="<module>", symbol="s", role=role,
                                    junk="none", in_except=0, text=f"x{line}")
        r = lambda line, det: dict(repo=REPO, path="p.py", line=line, col=0, detection=det)
        sites, n = J.current_sites([c(1, "call"), c(2, "call"), c(3, "reference"), c(4, "import")],
                                   [r(1, "ast_resolved"), r(2, "dropped"), r(3, "ast_resolved"), r(4, "ast_unresolved")],
                                   ["import", "call", "alias"])
        self.assertEqual([(s["line"], s["detection"]) for s in sites], [(1, "ast_resolved"), (4, "ast_unresolved")])
        self.assertEqual(n, {"dropped": 1, "not_edit_role": 1})

    def test_settings_the_code_doesnt_do_stop(self):
        cfg = {"join": {"call_site_id": J.ID_FIELDS, "normalize_line": "strip_whitespace", "match_order": J.MATCH_ORDER,
                        "fuzzy_ticket_matching": True}}
        with self.assertRaises(SystemExit):
            J.check_settings(cfg)


if __name__ == "__main__":
    unittest.main()
