"""Phase 3 caches (settings.toml [cache]) on fixtures: on or off, a part writes the same bytes, and a second run after
one change equals a run from scratch, reusing everything the change didn't reach.

    python -m unittest tests.test_cache -v      # from Data-Pipeline/
"""
import contextlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.resolve as R
import meridian.scan as S
import meridian.szz as Z
import tests.test_resolve as TR
import tests.test_scan as TS
import tests.test_szz as TZ
from meridian.common import Cache, cache_on, load_config, write_jsonl

RUN, RUN2 = "abcdef0123456789", "0123456789abcdef"


def switched(root, **on):
    """settings.toml with [cache] switches, for the parts that read it."""
    path = root / "settings.toml"
    path.write_text("[cache]\n" + "".join(f"{k} = {str(v).lower()}\n" for k, v in on.items()))
    return mock.patch.dict(os.environ, {"MERIDIAN_SETTINGS": str(path)})


def quiet(fn, *a):
    """(the part's log, the cache-hit counts it logged)."""
    with contextlib.redirect_stdout(io.StringIO()) as log:
        fn(*a)
    return log.getvalue(), [int(n) for n in re.findall(r"cache: (\d+) of", log.getvalue())]


def tables(root, run_id, names):
    return {t: (root / "out" / run_id / f"{t}.jsonl").read_bytes() for t in names}


def next_snapshot(root, repo_dir_name, repo, files):
    """snapshots/RUN2: RUN's checkout with files rewritten and committed, as the next run would see it."""
    shutil.copytree(root / "snapshots" / RUN, root / "snapshots" / RUN2)
    co = root / "snapshots" / RUN2 / "repos" / repo_dir_name
    for rel, text in files.items():
        (co / rel).write_text(text, encoding="utf-8")
    git = lambda *a: subprocess.run(["git", "-C", str(co), "-c", "user.name=F", "-c", "user.email=f@example.com", *a],
                                    check=True, capture_output=True, text=True).stdout.strip()
    git("commit", "--quiet", "-am", "next")
    (root / "snapshots" / RUN2 / "manifest.json").write_text(json.dumps({"run_id": RUN2, "repos": {repo: git("rev-parse", "HEAD")}}))
    (root / "out" / RUN2).mkdir(parents=True, exist_ok=True)
    return co


class Switch(unittest.TestCase):
    def test_rebuild_turns_every_cache_off(self):
        with tempfile.TemporaryDirectory() as tmp, switched(Path(tmp), scan=True, resolve=True, szz=True):
            self.assertTrue(all(cache_on(n) for n in ("scan", "resolve", "szz")))
            with mock.patch.dict(os.environ, {"MERIDIAN_NO_CACHE": "1"}):   # what the rebuild DAG sets
                self.assertFalse(any(cache_on(n) for n in ("scan", "resolve", "szz")))


class ScanCache(unittest.TestCase):
    def test_same_bytes_and_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, config = TS.snapshot(root)
            cfg = load_config(config)
            quiet(S.run, RUN, cfg, root)
            off = tables(root, RUN, S.KEYS)
            with switched(root, scan=True):
                _, first = quiet(S.run, RUN, cfg, root)
                self.assertEqual(tables(root, RUN, S.KEYS), off)          # first run with the cache: same bytes
                _, second = quiet(S.run, RUN, cfg, root)
                self.assertEqual(tables(root, RUN, S.KEYS), off)
            files = len(S.files(root / "snapshots" / RUN / "repos" / "fix__app", cfg["scanner"]))
            self.assertEqual((first, second), ([0], [files]))             # then every file reused

            next_snapshot(root, "fix__app", "fix/app", {"app/direct.py": "from lib.translation import ugettext\nugettext('n')\n"})
            quiet(S.run, RUN2, cfg, root)
            scratch = tables(root, RUN2, S.KEYS)
            with switched(root, scan=True):
                _, third = quiet(S.run, RUN2, cfg, root)
            self.assertEqual(tables(root, RUN2, S.KEYS), scratch)         # one file changed: same as from scratch
            self.assertEqual(third, [files - 1])                          # and only that file scanned again
            self.assertIn(b'"run_id": "0123456789abcdef"', scratch["candidate"])


class ResolveCache(unittest.TestCase):
    """Caveat 2: a file's resolution depends on what it imports. Change only an imported module: the files that read
    it are redone, the rest reused, and the rows equal a run from scratch."""

    def resolve(self, root, cfg, run_id, cached):
        """(resolution and call_edge bytes, the repo files the cache reused or None when off)"""
        made = []
        with switched(root, resolve=cached), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(R, "Cache", lambda *a: made.append(Cache(*a)) or made[-1]):
            R.run(run_id, cfg, root)
        return tables(root, run_id, ("resolution", "call_edge")), made[0].hits if made else None

    def check(self, files, repo, changed, redone):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = repo.replace("/", "__")
            co = root / "snapshots" / RUN / "repos" / name
            co.mkdir(parents=True)
            sha = TR.fixture_repo(co, files)
            (root / "snapshots" / RUN / "manifest.json").write_text(json.dumps({"run_id": RUN, "repos": {repo: sha}}))
            (root / "out" / RUN).mkdir(parents=True)
            cfg = {"run": {"handoff_dir": "out/{run_id}"}, "scanner": {"skip_paths": ["docs/"]}, "resolver": {"call_edge_depth": 3},
                   "datasets": {"d": {"repos": [{"repo": repo, "ref": sha}],
                                      "symbols": [{"name": n, "module": m} for n, m in TR.SYMBOLS.items()]}}}
            write_jsonl(root / "out" / RUN / "candidate.jsonl", TR.scanned(RUN, repo, co, TR.SYMBOLS, TR.SCANNER))
            scratch, _ = self.resolve(root, cfg, RUN, False)
            self.assertEqual(self.resolve(root, cfg, RUN, True), (scratch, []))   # first run with the cache: same bytes
            kept = sorted(Cache("resolve", repo, root).old)
            self.assertGreater(len(kept), 5)
            again, hits = self.resolve(root, cfg, RUN, True)
            self.assertEqual((again, sorted(hits)), (scratch, kept))               # nothing changed: every file reused

            co2 = next_snapshot(root, name, repo, changed)
            write_jsonl(root / "out" / RUN2 / "candidate.jsonl", TR.scanned(RUN2, repo, co2, TR.SYMBOLS, TR.SCANNER))
            scratch2, _ = self.resolve(root, cfg, RUN2, False)
            second, hits = self.resolve(root, cfg, RUN2, True)
            self.assertEqual(second, scratch2)                                     # one module changed: as from scratch
            self.assertNotEqual(scratch2, {t: v.replace(RUN.encode(), RUN2.encode()) for t, v in scratch.items()})
            self.assertEqual(sorted(set(kept) - set(hits)), redone)                # only what read the change is redone

    def test_change_in_an_imported_module(self):
        # app/compat.py passed ugettext on; now it defines its own: app/via_compat.py's candidates are dropped
        self.check(TR.FILES, "fix/app", {"app/compat.py": "def ugettext(s):\n    return s\n"},
                   ["app/compat.py", "app/via_compat.py"])

    def test_change_in_a_callee_module(self):
        # app/chain.py's level1 is renamed: app/importer.py's call of it is no longer an edge. importer.py has no rows
        # of its own to redo (no tracked name), so it is reused, and its edge goes because call_edge is always rebuilt
        chain = TR.EDGE_FILES["app/chain.py"].replace("def level1():", "def level0():").replace("return level1()", "return level0()")
        self.check(TR.EDGE_FILES, "fix/edges", {"app/chain.py": chain}, ["app/chain.py"])


class SzzCache(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.co = self.tmp / "repo"
        self.sha = TZ.fixture_repo(self.co)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fixes(self, keys):
        return {("fix/repo", self.sha[s]): ({"repo": "fix/repo", "sha": self.sha[s], "parent_sha": self.sha[p]}, k)
                for s, p, k in keys}

    def rows(self, fixes, cache=False):
        cache_of = (lambda repo: Cache("szz", repo, self.tmp)) if cache else None
        with contextlib.redirect_stdout(io.StringIO()) as log:
            rows = Z.bug_inducing(fixes, lambda repo: self.co, RUN, TZ.CODE, 1, cache_of)
        path = self.tmp / "b.jsonl"
        write_jsonl(path, rows, ("repo", "fix_sha", "path", "inducing_sha"))
        return path.read_bytes(), [int(n) for n in re.findall(r"(\d+) reused from the cache", log.getvalue())]

    def test_same_bytes_and_only_new_fixes_blamed(self):
        fixes = self.fixes([("fix", "c1", ["X-1"]), ("headers", "add_only", ["X-2"])])
        off, _ = self.rows(fixes)
        self.assertEqual(self.rows(fixes, cache=True), (off, [0]))       # first run with the cache: same bytes
        self.assertEqual(self.rows(fixes, cache=True), (off, [2]))       # then both fixes reused
        # a new fix commit, and the old fix now keyed to another ticket: the key is never cached
        run = lambda *a: subprocess.run(["git", "-C", str(self.co), "-c", "user.name=A", "-c", "user.email=a@example.com", *a],
                                        check=True, capture_output=True, text=True).stdout.strip()
        (self.co / "app.py").write_text((self.co / "app.py").read_text().replace("return 3", "return 4"))
        run("commit", "--quiet", "-am", "fix2")
        self.sha["fix2"] = run("rev-parse", "HEAD")
        fixes = self.fixes([("fix", "c1", ["X-9"]), ("headers", "add_only", ["X-2"]), ("fix2", "headers", ["X-3"])])
        scratch, _ = self.rows(fixes)
        self.assertEqual(self.rows(fixes, cache=True), (scratch, [2]))   # same as from scratch, only fix2 blamed
        self.assertIn(b'"X-9"', scratch)
        self.assertIn(self.sha["fix2"].encode(), scratch)


if __name__ == "__main__":
    unittest.main()
