"""P9 with the champion model (meridian/p9.py): it loads and scores, every way it can't is lines × files with a reason,
the push's features are data.history's, and a repo the model wasn't trained on keeps today's rule.

    python -m unittest tests.test_p9 -v      # from Data-Pipeline/

Needs Model-Pipeline's packages (pandas, scikit-learn, joblib): CI's model job has them.
"""
import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian import p9
from meridian.common import SCHEMA

HAS_MODEL_PACKAGES = all(importlib.util.find_spec(m) for m in ("pandas", "sklearn", "joblib", "pyarrow"))
SPARK = "apache/spark"      # model.toml [data] repo: the repo the champion was trained on
RUN, AT = "R1", "2024-06-01T00:00:00Z"
KEY = r"\bSPARK-\d+\b"


def block(path):
    """A small Spark history: three first-parent commits on two files, two people, one Bug ticket."""
    db = sqlite3.connect(path)
    db.executescript(SCHEMA.read_text(encoding="utf-8"))
    c2_files = [("core/a.py", 2, 1), ("sql/b.py", 5, 0)]
    commits = [("c1", "2024-01-01T00:00:00Z", "ann@x.org", "SPARK-1 start", [("core/a.py", 10, 0)]),
               ("c2", "2024-02-01T00:00:00Z", "bob@x.org", "SPARK-1 fix it", c2_files),
               ("c3", "2024-03-01T00:00:00Z", "ann@x.org", "[MINOR] tidy", [("sql/b.py", 1, 1)])]
    for seq, (sha, at, email, subject, files) in enumerate(commits):
        db.execute("INSERT INTO git_commit (run_id, repo, sha, parent_sha, seq, first_parent, is_merge, authored_at, "
                   "author_email, committer_email, subject) VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?)",
                   (RUN, SPARK, sha, None, seq, at, email, email, subject))
        db.executemany("INSERT INTO commit_file (run_id, repo, sha, path, old_path, added, deleted) "
                       "VALUES (?, ?, ?, ?, NULL, ?, ?)", [(RUN, SPARK, sha, p, a, d) for p, a, d in files])
    for email, pid in (("ann@x.org", "p1"), ("bob@x.org", "p2")):
        db.execute("INSERT INTO person_alias (run_id, email, person_id, name, generic, source) "
                   "VALUES (?, ?, ?, ?, 0, 'single')", (RUN, email, pid, pid))
    cols = [r[1] for r in db.execute("PRAGMA table_info(ticket)")]
    row = {c: None for c in cols} | {"run_id": RUN, "key": "SPARK-1", "tracker": "jira", "type": "Bug",
                                     "effective_type": "Bug", "status": "Resolved", "resolution": "Fixed",
                                     "created_at": "2023-12-01T00:00:00Z", "components": "[]", "bulk_closed": 0,
                                     "desc_empty": 0, "summary": "s"}
    for c in cols:   # any other NOT NULL column the schema has, filled with a placeholder
        if row[c] is None and any(r[1] == c and r[3] for r in db.execute("PRAGMA table_info(ticket)")):
            row[c] = 0
    db.execute(f"INSERT INTO ticket ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
    db.executemany("INSERT INTO commit_ticket (run_id, repo, sha, key, source) VALUES (?, ?, ?, 'SPARK-1', 'subject')",
                   [(RUN, SPARK, "c1"), (RUN, SPARK, "c2")])
    db.commit()
    return db


NUMSTAT = [("core/a.py", 4, 2), ("sql/b.py", 3, 0), ("img/logo.png", None, None)]
MESSAGES = [("abc", "SPARK-1 more", "")]
LXF = (9, 3, 27)


_FOREST = []


def small_forest():
    """A small forest on Model-Pipeline's fake table, trained once for all the tests: the champion's shape (Contract 1
    inputs)."""
    if not _FOREST:
        _, train = p9._model_modules()          # first: the fake table imports model
        path = p9.MODEL_PIPELINE / "tests" / "fake_table.py"
        spec = importlib.util.spec_from_file_location("mp_fake_table", path)   # by file: both pipelines have tests
        fake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fake)
        _FOREST.append(train.fit_and_score(fake.fake_table(n=1500, seed=2), "forest", 1, {"n_estimators": 30})[0])
    return _FOREST[0]


@unittest.skipUnless(HAS_MODEL_PACKAGES, "needs pandas, scikit-learn, joblib, pyarrow (Model-Pipeline's packages)")
class WithModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = Path(cls.tmp.name)
        cls.db = block(d / "meridian.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def champion(self, model_cutoff=-1.0, lxf_cutoff=0, model=None, md5=None, missing=False, name="model.joblib",
                 adopted=True, rule_md5=None, rule=True):
        """A champion.json (+ its model file) and decision 15b's p9_rule.json in a fresh folder; returns the
        champion.json path. The rule file sits next to it (run_p9 finds it there)."""
        import joblib
        folder = Path(tempfile.mkdtemp(dir=self.d))
        model = model if model is not None else self.forest()
        if not missing:
            joblib.dump(model, folder / name)
        digest = md5 or (hashlib.md5((folder / name).read_bytes()).hexdigest() if not missing else "0" * 32)
        rec = {"version": "abcdef1234567890", "run_id": "r", "model_file": name, "model_md5": digest,
               "metrics": {"at_threshold": {"threshold": None}}, "previous": None}
        (folder / "champion.json").write_text(json.dumps(rec))
        if rule:
            pick = {"model_cutoff": model_cutoff, "lxf_cutoff": lxf_cutoff, "precision": 0.8, "alerts": 30}
            (folder / "p9_rule.json").write_text(json.dumps({"adopted": adopted, "pick": pick,
                                                             "model_md5": rule_md5 or digest}))
        return folder / "champion.json"

    def forest(self):
        return small_forest()

    def run_p9(self, champion, repo=SPARK, threshold=10):
        return p9.verdict(self.db, repo, RUN, AT, NUMSTAT, MESSAGES, "ann@x.org", KEY, LXF, threshold, champion,
                          champion.parent / "p9_rule.json")

    def test_both_over_their_cutoffs_alerts_from_the_model(self):
        v = self.run_p9(self.champion(model_cutoff=-1.0, lxf_cutoff=0))
        self.assertEqual((v.rule, v.alert), ("model", True))
        self.assertIn("from the champion model (RandomForestClassifier, version abcdef1234) and 9 lines × 3 files "
                      "= 27: both over their cutoffs (-1.00 and 0, decision 15b)", v.text)

    def test_the_model_alone_never_alerts(self):
        """Model over its cutoff, lines × files under its: no alert (the model alone reaches 0.70, under 0.80)."""
        v = self.run_p9(self.champion(model_cutoff=-1.0, lxf_cutoff=1000), threshold=1)
        self.assertEqual((v.rule, v.alert), ("model", False))

    def test_lines_x_files_alone_never_alerts_while_the_rule_is_on(self):
        v = self.run_p9(self.champion(model_cutoff=1.01, lxf_cutoff=0), threshold=1)
        self.assertEqual((v.rule, v.alert), ("model", False))

    def test_a_rule_that_wasnt_adopted_or_isnt_there_is_lines_x_files_by_design(self):
        for path in (self.champion(adopted=False), self.champion(rule=False)):
            with self.subTest(path=path):
                v = self.run_p9(path)
                self.assertEqual((v.rule, v.alert, v.why, v.text),
                                 ("lines_x_files", True, None, "9 lines × 3 files = 27, over 10"))

    def test_the_score_is_the_models_on_the_push_features(self):
        _, train = p9._model_modules()
        row = p9.push_features(self.db, SPARK, RUN, AT, NUMSTAT, MESSAGES, "ann@x.org", KEY, 365)
        expected = float(train.score_with(self.forest(), row)[0])
        v = self.run_p9(self.champion())
        self.assertIn(f"defect risk {expected:.2f}", v.text)

    def test_every_way_the_model_cant_be_used_says_why(self):
        bad = {"md5 isn't champion.json's": self.champion(md5="f" * 32),
               "isn't next to champion.json": self.champion(missing=True),
               "p9_rule.json was measured on another model than the champion": self.champion(rule_md5="e" * 32)}
        unreadable = self.champion()
        (unreadable.parent / "p9_rule.json").write_text("{not json")
        bad["p9_rule.json doesn't read"] = unreadable
        corrupt = self.champion()
        (corrupt.parent / "model.joblib").write_bytes(b"not a model")
        rec = json.loads(corrupt.read_text())
        rec["model_md5"] = hashlib.md5(b"not a model").hexdigest()
        corrupt.write_text(json.dumps(rec))
        rule = json.loads((corrupt.parent / "p9_rule.json").read_text())
        rule["model_md5"] = rec["model_md5"]
        (corrupt.parent / "p9_rule.json").write_text(json.dumps(rule))
        bad["the model didn't load"] = corrupt
        broken = Path(tempfile.mkdtemp(dir=self.d)) / "champion.json"
        broken.write_text("{not json")
        (broken.parent / "p9_rule.json").write_text("{}")
        bad["champion.json doesn't read"] = broken
        for why, path in bad.items():
            with self.subTest(why):
                v = self.run_p9(path)
                self.assertEqual((v.rule, v.alert), ("lines_x_files", True))
                self.assertIn(why, v.text)
                self.assertTrue(v.text.startswith("9 lines × 3 files = 27, over 10 (lines × files: "), v.text)

    def test_a_model_that_cant_score_the_push_falls_back(self):
        from sklearn.dummy import DummyClassifier
        import pandas as pd
        odd = DummyClassifier().fit(pd.DataFrame({"not_a_feature": [0, 1]}), [0, 1])
        v = self.run_p9(self.champion(model=odd))
        self.assertEqual(v.rule, "lines_x_files")
        self.assertIn("couldn't score this push", v.text)

    def test_another_repo_keeps_lines_x_files_with_nothing_to_explain(self):
        v = self.run_p9(self.champion(), repo="acme/shop")
        self.assertEqual((v.rule, v.alert, v.why), ("lines_x_files", True, None))
        self.assertEqual(v.text, "9 lines × 3 files = 27, over 10")


@unittest.skipUnless(HAS_MODEL_PACKAGES, "needs pandas, scikit-learn, joblib, pyarrow (Model-Pipeline's packages)")
class Features(unittest.TestCase):
    """The push as data.history reads one more first-parent commit."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = block(Path(cls.tmp.name) / "meridian.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.tmp.cleanup()

    def row(self, author="ann@x.org", messages=MESSAGES):
        return p9.push_features(self.db, SPARK, RUN, AT, NUMSTAT, messages, author, KEY, 365).iloc[0]

    def test_size_from_the_numstat_with_binary_as_zero_lines(self):
        r = self.row()
        self.assertEqual((r.la, r.ld, r.nf, r.nd, r.ns), (7, 2, 3, 3, 3))

    def test_history_from_the_block(self):
        r = self.row()
        self.assertEqual(r.ndev, 2)          # ann and bob touched a.py or b.py
        self.assertEqual(r.nuc, 3)           # c1, c2, c3
        self.assertEqual(r.exp, 2)           # ann's two earlier commits
        self.assertEqual(r.prior_bug_fixes, 2)   # c1 and c2 carry SPARK-1, a Fixed Bug: both are fixes
        self.assertGreater(r.age, 0)

    def test_an_author_the_block_hasnt_seen_is_new(self):
        r = self.row(author="new@x.org")
        self.assertEqual((r.exp, r.sexp), (0, 0))

    def test_ticket_features_read_keys_as_p8_does(self):
        r = self.row()
        self.assertEqual((r.fix, r.has_ticket, r.ticket_type, r.is_minor), (1, 1, "Bug", 0))
        r = self.row(messages=[("a", "[MINOR] typo", ""), ("b", "[minor] SPARK-9 not known", "")])
        self.assertEqual((r.fix, r.has_ticket, r.ticket_type, r.is_minor), (0, 0, "none", 1))


class NoChampion(unittest.TestCase):
    """No champion configured, or none shipped yet: today's rule, word for word, and no model package is touched."""

    def test_no_champion_is_lines_x_files_as_before(self):
        for champion in (None, Path(tempfile.gettempdir()) / "no-such-dir" / "champion.json"):
            with self.subTest(champion=champion):
                v = p9.verdict(None, SPARK, RUN, AT, NUMSTAT, MESSAGES, "ann@x.org", KEY, LXF, 10, champion)
                self.assertEqual((v.rule, v.alert, v.text, v.why),
                                 ("lines_x_files", True, "9 lines × 3 files = 27, over 10", None))

    def test_under_the_bar_no_alert(self):
        v = p9.verdict(None, SPARK, RUN, AT, NUMSTAT, MESSAGES, "ann@x.org", KEY, (2, 1, 2), 10, None)
        self.assertFalse(v.alert)


HAS_SCANNER = all(importlib.util.find_spec(m) for m in ("tree_sitter", "tree_sitter_python", "jedi"))


@unittest.skipUnless(HAS_MODEL_PACKAGES and HAS_SCANNER, "needs Model-Pipeline's packages and the scanner's")
class EndToEnd(unittest.TestCase):
    """push_check itself, on tests/test_pushcheck.py's PR, with [push_check] champion and an adopted p9_rule set: the
    P9 line comes from the model, and a champion that can't load gives today's line plus why. Nothing else changes."""

    @classmethod
    def setUpClass(cls):
        from unittest import mock
        import tests.test_pushcheck as T
        cls.T, cls.tmp = T, tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.co, cls.base = d / "shop", d / "meridian.sqlite"
        cls.co.mkdir()
        T.sh(cls.co, "init", "-q")
        T.commit(cls.co, T.BASE, "ann", "start")
        T.block(cls.base, cls.co)
        T.commit(cls.co, T.HEAD, "pia", "PAY-99 add refund, see CHK-1")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.forest = small_forest()
        # the model scores only the repo it was trained on: here, the test's repo. Undone even if a test fails,
        # so it can't leak into the other classes
        patch = mock.patch.object(p9, "model_repo", return_value=(T.REPO, 365))
        patch.start()
        cls.addClassCleanup(patch.stop)

    def check(self, champion):
        import meridian.pushcheck as P
        settings = dict(self.T.SETTINGS, champion=str(champion), p9_rule=str(champion.parent / "p9_rule.json"))
        out = P.push_check(self.base, self.T.REPO, self.co, [], self.T.CFG, settings, "HEAD~1...HEAD")
        return [line for line in out.splitlines() if line.startswith("- **")]

    def write_champion(self, model_cutoff=-1.0, md5=None):
        """champion.json, its model and an adopted p9_rule.json (lines × files cutoff 0) in a fresh folder."""
        import joblib
        folder = Path(tempfile.mkdtemp(dir=self.tmp.name))
        joblib.dump(self.forest, folder / "model.joblib")
        digest = hashlib.md5((folder / "model.joblib").read_bytes()).hexdigest()
        rec = {"version": "abcdef1234567890", "run_id": "r", "model_file": "model.joblib", "model_md5": md5 or digest,
               "metrics": {"at_threshold": {"threshold": None}}, "previous": None}
        (folder / "champion.json").write_text(json.dumps(rec))
        rule = {"adopted": True, "model_md5": md5 or digest, "pick": {"model_cutoff": model_cutoff, "lxf_cutoff": 0}}
        (folder / "p9_rule.json").write_text(json.dumps(rule))
        return folder / "champion.json"

    def test_the_p9_line_comes_from_the_champion(self):
        lines = self.check(self.write_champion(-1.0))      # every push is over both cutoffs: the model's line
        p9_lines = [line for line in lines if line.startswith("- **P9 ")]
        self.assertEqual(len(p9_lines), 1)
        self.assertIn("from the champion model (RandomForestClassifier", p9_lines[0])
        others = [line for line in lines if not line.startswith("- **P9 ")]
        settings = dict(self.T.SETTINGS)
        import meridian.pushcheck as P
        before = P.push_check(self.base, self.T.REPO, self.co, [], self.T.CFG, settings, "HEAD~1...HEAD")
        self.assertEqual(others, [line for line in before.splitlines()
                                  if line.startswith("- **") and not line.startswith("- **P9 ")])

    def test_a_champion_that_cant_load_is_todays_line_plus_why(self):
        lines = self.check(self.write_champion(md5="0" * 32))
        self.assertIn("- **P9 Defect risk**: 9 lines × 5 files = 45, over 10 (lines × files: model.joblib's md5 "
                      "isn't champion.json's)", lines)


if __name__ == "__main__":
    unittest.main()
