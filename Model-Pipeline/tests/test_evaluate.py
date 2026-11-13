"""evaluate.py (Rochan, step D): hand calculations, the bootstrap, metrics.json, and the refusals.

Every table here is made up from a seed. Nothing reads the real feature table or its test rows."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from model import evaluate as ev
from tests import mlflow_uri
from tests.contracts import make_rochan

CONTRACTS = Path(__file__).resolve().parent / "contracts"

# The 10-commit example, worked by hand (decision 2). Scores fall from the first commit to the last, no ties.
Y = np.array([1, 0, 1, 1, 0, 0, 0, 1, 1, 0])
S = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.15, 0.1])
LINES = np.array([100, 10, 50, 5, 200, 20, 1, 300, 40, 274])  # 1,000 lines, so 20% is 200

EVAL = """
[eval]
bootstrap_reps = 200
interval = 0.95
effort = 0.2
effort_ranking = "risk_per_line"
threshold = "precision"
threshold_precision = 0.8
threshold_min_alerts = 5
threshold_alert_rate = 0.05
"""
CONFIG = """
[data]
run_id = "fake0000000000aa"
window_days = 365
follow_up_end = "2025-10-01T00:00:00Z"
start_year = 2015
repo = "apache/spark"
prior_fix_days = 365

[split]
train = [2015, 2022]
val = 2023
test = 2024
test_allowed = {allowed}

[seed]
value = 1
""" + EVAL


class HandCalculation(unittest.TestCase):
    """The pass bar's "right numbers": each value below is worked out in the comment beside it."""

    def test_pr_auc(self):
        # positives at ranks 1, 3, 4, 8, 9: precision there 1/1, 2/3, 3/4, 4/8, 5/9; their mean is 25/36
        self.assertAlmostEqual(ev.pr_auc(Y, S), 25 / 36, places=12)

    def test_roc_auc(self):
        # of the 25 (bug, clean) pairs the bug scores higher in 5 + 4 + 4 + 1 + 1 = 15
        self.assertAlmostEqual(ev.roc_auc(Y, S), 15 / 25, places=12)

    def test_at_a_threshold(self):
        # flag 0.6 and up: commits 1-4, three bugs and one clean; two bugs missed
        r = ev.at_threshold(Y, S, 0.6)
        self.assertEqual((r["tp"], r["fp"], r["fn"], r["tn"]), (3, 1, 2, 4))
        self.assertAlmostEqual(r["precision"], 3 / 4)
        self.assertAlmostEqual(r["recall"], 3 / 5)
        self.assertAlmostEqual(r["f1"], 2 * 3 / (2 * 3 + 1 + 2))
        self.assertEqual(r["median_alerts_per_push"], 0.0)  # 4 of 10 pushes alert: the median push has none

    def test_recall_at_20_percent_by_risk(self):
        # read 1, 2, 3, 4: 165 lines, three bugs. Commit 5 (200 lines) doesn't fit; 35 of its lines are read, and
        # it has no bug. 3 of 5
        r = ev.recall_at_effort(Y, S, LINES, 0.2, "risk")
        self.assertAlmostEqual(r["value"], 3 / 5)
        self.assertEqual(r["commits_read_whole"], 4)

    def test_recall_at_20_percent_by_risk_per_line(self):
        # per line: commit 7 (0.3), 4 (0.12), 2 (0.08), 6 (0.02), 3 (0.014), 1 (0.009), 9 (0.00375), ...
        # lines 1, 5, 10, 20, 50, 100: 186 read, bugs in 4, 3 and 1. Commit 9 has 40 lines and a bug: 14 fit,
        # so 14/40 of a bug. (3 + 0.35) / 5
        r = ev.recall_at_effort(Y, S, LINES, 0.2, "risk_per_line")
        self.assertAlmostEqual(r["value"], 3.35 / 5)
        self.assertEqual(r["commits_read_whole"], 6)

    def test_smallest_first_with_no_model(self):
        # by size: commits 7, 4, 2, 6, 9, 3 are 1 + 5 + 10 + 20 + 40 + 50 = 126 lines, bugs in 4, 9 and 3.
        # Commit 1 (100 lines, a bug) gets the last 74 lines: 0.74 of a bug. (3 + 0.74) / 5
        w = ev.without_a_model(Y, LINES, 0.2)
        self.assertAlmostEqual(w["smallest_first"], 3.74 / 5)
        self.assertEqual((w["random_order"], w["commits_read_whole"], w["n"]), (0.2, 6, 10))

    def test_a_tie_is_read_in_part(self):
        # every commit scores the same: one group of 1,000 lines, 200 fit, so 20% of its 5 bugs
        r = ev.recall_at_effort(Y, np.ones(10), LINES, 0.2, "risk")
        self.assertAlmostEqual(r["value"], 0.2)

    def test_a_commit_with_no_lines_counts_one(self):
        lines = LINES.copy()
        lines[6] = 0
        a = ev.recall_at_effort(Y, S, lines, 0.2, "risk_per_line")
        b = ev.recall_at_effort(Y, S, np.where(lines == 0, 1, lines), 0.2, "risk_per_line")
        self.assertEqual(a, b)

    def test_threshold_rules(self):
        # best F1 flags 9 of 10: 5 bugs, 4 clean, F1 10/14 = 0.714, higher than any smaller set
        self.assertEqual(ev.choose_threshold(Y, S, "best_f1"), 0.15)
        # precision >= 0.8: only the top commit (1 of 1). With at least 2 alerts nothing reaches it
        self.assertEqual(ev.choose_threshold(Y, S, "precision", 0.8, min_alerts=1), 0.9)
        self.assertIsNone(ev.choose_threshold(Y, S, "precision", 0.8, min_alerts=2))
        # 30% of 10 commits: the top 3
        self.assertEqual(ev.choose_threshold(Y, S, "alert_rate", alert_rate=0.3), 0.7)

    def test_no_threshold_flags_nothing(self):
        r = ev.at_threshold(Y, S, None)
        self.assertEqual((r["alerts"], r["precision"], r["recall"], r["f1"]), (0, None, 0.0, 0.0))

    def test_perfect_and_random(self):
        self.assertEqual(ev.pr_auc(Y, Y), 1.0)
        rng = np.random.default_rng(7)
        y = (rng.random(10_000) < 0.12).astype(int)
        self.assertLess(abs(ev.pr_auc(y, rng.random(10_000)) - y.mean()), 0.02)

    def test_one_class_is_refused(self):
        with self.assertRaisesRegex(ValueError, "both classes"):
            ev.pr_auc(np.zeros(5), np.arange(5))


class FastMetrics(unittest.TestCase):
    """The bootstrap's own PR-AUC and ROC-AUC, on count weights, against scikit-learn's."""

    def test_same_as_scikit_learn_with_ties(self):
        for seed in range(5):
            rng = np.random.default_rng(seed)
            n = 300
            y = (rng.random(n) < 0.2).astype(float)
            s = np.round(rng.random(n) + 0.3 * y, 1)  # few distinct values: many ties
            w = rng.integers(0, 4, n).astype(float)
            order, starts = ev._tie_groups(s)
            pos, neg = ev._group_counts(y, order, starts, w[None, :])
            self.assertAlmostEqual(ev._average_precision(pos, neg)[0],
                                   average_precision_score(y, s, sample_weight=w), places=12)
            self.assertAlmostEqual(ev._roc_auc(pos, neg)[0], roc_auc_score(y, s, sample_weight=w), places=12)

    def test_a_resample_is_its_counts(self):
        rng = np.random.default_rng(3)
        y = (rng.random(200) < 0.3).astype(int)
        s = rng.random(200)
        i = rng.integers(0, 200, 200)
        order, starts = ev._tie_groups(s)
        pos, neg = ev._group_counts(y.astype(float), order, starts, np.bincount(i, minlength=200)[None, :])
        self.assertAlmostEqual(ev._average_precision(pos, neg)[0], average_precision_score(y[i], s[i]), places=12)


def stand_in(y, a, b, reps=1000, seed=0):
    """train.py's stand-in paired bootstrap (rachna-models, Nov 6), copied as the reference."""
    rng = np.random.default_rng(seed)
    diffs = []
    while len(diffs) < reps:
        i = rng.integers(0, len(y), len(y))
        if y[i].min() == y[i].max():
            continue
        diffs.append(average_precision_score(y[i], a[i]) - average_precision_score(y[i], b[i]))
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return average_precision_score(y, a) - average_precision_score(y, b), float(lo), float(hi)


class Bootstrap(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(11)
        self.y = (rng.random(800) < 0.15).astype(int)
        self.a = self.y + rng.normal(0, 0.8, 800)
        self.b = self.y + rng.normal(0, 1.2, 800)

    def test_same_numbers_as_the_stand_in(self):
        mine = ev.paired_bootstrap(self.y, self.a, self.b, reps=300, seed=5)
        for x, ref in zip(mine, stand_in(self.y, self.a, self.b, reps=300, seed=5)):
            self.assertAlmostEqual(x, ref, places=12)

    def test_same_seed_same_interval(self):
        one = ev.paired_bootstrap(self.y, self.a, self.b, reps=200, seed=4)
        self.assertEqual(one, ev.paired_bootstrap(self.y, self.a, self.b, reps=200, seed=4))
        self.assertNotEqual(one, ev.paired_bootstrap(self.y, self.a, self.b, reps=200, seed=5))

    def test_a_score_against_itself(self):
        self.assertEqual(ev.paired_bootstrap(self.y, self.a, self.a, reps=100), (0.0, 0.0, 0.0))

    def test_resamples_with_one_class_are_drawn_again(self):
        y = np.zeros(20, int)
        y[3] = 1  # one bug in 20 rows: about a third of resamples miss it
        n = sum(len(w) for w in ev.resamples(y, 500, 0))
        self.assertEqual(n, 500)
        for w in ev.resamples(y, 500, 0):
            self.assertTrue((w[:, 3] > 0).all())

    def test_chunks_dont_change_the_numbers(self):
        whole = np.concatenate(list(ev.resamples(self.y, 300, 2, chunk=300)))
        parts = np.concatenate(list(ev.resamples(self.y, 300, 2, chunk=7)))
        self.assertTrue((whole == parts).all())


def fake(n=4000, seed=0):
    """A feature table with the columns evaluate.py reads, and a scores file per split, like train.py's."""
    rng = np.random.default_rng(seed)
    year = rng.choice(range(2015, 2026), n)
    la, ld, nf = rng.poisson(40, n), rng.poisson(15, n), 1 + rng.poisson(2, n)
    prior = rng.poisson(1.5, n)
    risk = 0.004 * (la + ld) + 0.25 * nf + 0.3 * prior - 2.5
    label = (rng.random(n) < 1 / (1 + np.exp(-risk))).astype(int)
    split = np.select([year <= 2022, year == 2023, year == 2024], ["train", "val", "test"], "none")
    table = pd.DataFrame({"sha": [f"{i:040x}" for i in range(n)], "year": year, "split": split, "label": label,
                          "la": la, "ld": ld, "nf": nf, "prior_bug_fixes": prior})
    scores = {}
    for sp in ("val", "test"):
        r = table[table["split"] == sp]
        scores[sp] = pd.DataFrame({"sha": r["sha"], "label": r["label"],
                                   "lines_x_files": (r["la"] + r["ld"]) * r["nf"], "churn": r["la"] + r["ld"],
                                   "bug_fixes": r["prior_bug_fixes"],
                                   "lgbm": 1 / (1 + np.exp(-(risk[r.index] + rng.normal(0, 0.7, len(r)))))})
    return table, scores


class Command(unittest.TestCase):
    """main(): metrics.json, the charts, the refusals."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="meridian-evaluate-"))
        table, scores = fake()
        cls.features = cls.dir / "features.parquet"
        table.to_parquet(cls.features, index=False)
        for sp, df in scores.items():
            df.to_csv(cls.dir / f"fake_{sp}_scores.csv", index=False)
        (cls.dir / "candidate.json").write_text(json.dumps(
            {"kind": "lgbm", "params": {"num_leaves": 31}, "scores_file": "fake_val_scores.csv", "column": "lgbm",
             "seed": 1}))
        cls.config = cls.write_config(cls.dir / "model.toml", "false")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, True)

    @staticmethod
    def write_config(path, allowed):
        path.write_text(CONFIG.format(allowed=allowed))
        return path

    def run_main(self, *extra, out=None, config=None):
        out = out or Path(tempfile.mkdtemp(dir=self.dir))
        args = ["--config", str(config or self.config), "--features", str(self.features), "--candidate",
                str(self.dir / "candidate.json"), "--out", str(out), "--no-mlflow", *extra]
        with mock.patch("builtins.print"):
            m = ev.main(args)
        return m, out

    def test_every_number_has_its_n(self):
        m, out = self.run_main("--no-charts")
        saved = json.loads((out / "metrics.json").read_text())
        self.assertEqual(saved["split"], "val")
        self.assertEqual(set(saved["models"]), {"lines_x_files", "churn", "bug_fixes", "lgbm"})
        blocks = 0
        for name, x in saved["models"].items():
            for key, block in x.items():
                if isinstance(block, dict):
                    blocks += 1
                    self.assertIn("n", block, f"{name}.{key}")
                    self.assertIn("positive_rate", block, f"{name}.{key}")
                    self.assertEqual(block["n"], saved["n"])
        self.assertEqual(blocks, 4 * 4 + 3)  # four blocks each, and the difference for all but lines x files

    def test_same_seed_same_file(self):
        _, one = self.run_main("--no-charts")
        _, two = self.run_main("--no-charts")
        self.assertEqual((one / "metrics.json").read_bytes(), (two / "metrics.json").read_bytes())

    def test_the_candidate_and_its_scores_are_recorded(self):
        m, _ = self.run_main("--no-charts")
        self.assertEqual(m["candidate"]["column"], "lgbm")
        self.assertEqual(m["candidate"]["kind"], "lgbm")
        self.assertEqual(m["scores_md5"], ev.file_md5(self.dir / "fake_val_scores.csv"))
        self.assertEqual(m["models"]["lgbm"]["role"], "candidate")
        self.assertEqual(m["models"]["churn"]["role"], "baseline")

    def test_charts(self):
        _, out = self.run_main()
        for name in ("pr_auc", "confusion", "pr_curves"):
            self.assertGreater((out / f"{name}_val.png").stat().st_size, 10_000, name)

    def test_test_is_refused_unless_allowed(self):
        with self.assertRaises(SystemExit) as e:
            self.run_main("--split", "test", "--scores", str(self.dir / "fake_test_scores.csv"))
        self.assertIn("test_allowed", str(e.exception))

    def test_test_is_refused_on_uncommitted_code(self):
        config = self.write_config(self.dir / "allowed_dirty.toml", "true")
        with mock.patch("model.tracking.git_state", return_value=("abc", "true")):
            with self.assertRaises(SystemExit) as e:
                self.run_main("--split", "test", "--scores", str(self.dir / "fake_test_scores.csv"), config=config)
        self.assertIn("commit first", str(e.exception))

    def test_test_uses_the_val_threshold(self):
        config = self.write_config(self.dir / "allowed.toml", "true")
        with mock.patch("model.tracking.git_state", return_value=("abc", "false")):
            m, _ = self.run_main("--split", "test", "--scores", str(self.dir / "fake_test_scores.csv"),
                                 "--no-charts", config=config)
        val, _ = self.run_main("--no-charts")
        self.assertEqual(m["split"], "test")
        for name in m["models"]:
            self.assertEqual(m["models"][name]["at_threshold"]["threshold"],
                             val["models"][name]["at_threshold"]["threshold"])
        self.assertEqual(m["n"], int((pd.read_parquet(self.features)["split"] == "test").sum()))

    def test_decisions_dont_run_on_test(self):
        config = self.write_config(self.dir / "allowed2.toml", "true")
        with self.assertRaises(SystemExit) as e:
            self.run_main("--split", "test", "--decide", "4", config=config)
        self.assertIn("val only", str(e.exception))

    def test_scores_must_be_the_rows_of_the_split(self):
        good = pd.read_csv(self.dir / "fake_val_scores.csv")
        table = pd.read_parquet(self.features)
        cases = {"rows differ": good.iloc[::-1],
                 "labels differ": good.assign(label=1 - good["label"]),
                 "rows against": good.iloc[:-1],
                 "no 'lgbm' column": good.drop(columns="lgbm"),
                 "non-numeric": good.assign(churn=np.where(good.index == 3, np.nan, good["churn"]))}
        for message, df in cases.items():
            path = self.dir / "bad.csv"
            df.to_csv(path, index=False)
            with self.subTest(message), self.assertRaisesRegex(ev.ScoresError, message):
                ev.load_scores(table, path, "val", "lgbm")

    def test_a_missing_eval_key_is_refused(self):
        path = self.dir / "no_eval.toml"
        path.write_text(CONFIG.format(allowed="false").replace("threshold_min_alerts = 5\n", ""))
        with self.assertRaises(SystemExit) as e:
            self.run_main(config=path)
        self.assertIn("threshold_min_alerts", str(e.exception))

    def test_logged_to_mlflow(self):
        store = Path(tempfile.mkdtemp(dir=self.dir))
        with mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": mlflow_uri(store)}):
            out = Path(tempfile.mkdtemp(dir=self.dir))
            args = ["--config", str(self.config), "--features", str(self.features), "--candidate",
                    str(self.dir / "candidate.json"), "--out", str(out), "--no-charts"]
            with mock.patch("builtins.print"):
                ev.main(args)
            from mlflow.tracking import MlflowClient

            from model import tracking
            client = MlflowClient()
            run = client.search_runs([tracking.experiment_id(tracking.MODEL)])[0]
            self.assertEqual(run.data.tags["owner"], "rochan")
            self.assertEqual(run.data.tags["features_md5"], ev.file_md5(self.features))
            self.assertIn("val_pr_auc_lgbm", run.data.metrics)
            self.assertEqual([a.path for a in client.list_artifacts(run.info.run_id)], ["metrics.json"])


class ScoreChampion(unittest.TestCase):
    """--score-champion, the one 2024 run's first step (packets/rochan-tracking/RUN-2024.md): the frozen champion's
    model, md5-checked against champion.json and its recorded val PR-AUC, scored with the baselines on val, and on test
    only after the test run's own refusals. A made-up table and a small model fitted on it, as in Command."""

    @classmethod
    def setUpClass(cls):
        import joblib

        from model import train
        from tests.fake_table import fake_table
        cls.dir = Path(tempfile.mkdtemp(prefix="meridian-champion-"))
        cls.table = fake_table(n=3000, seed=5)
        cls.features = cls.dir / "features.parquet"
        cls.table.to_parquet(cls.features, index=False)
        model, _, _ = train.fit_and_score(cls.table, "logreg", 1)
        cls.model = cls.dir / "model.joblib"
        joblib.dump(model, cls.model)
        val = cls.table[cls.table["split"] == "val"]
        record = json.loads((CONTRACTS / "champion.json").read_text())
        record.update(model_file="model.joblib", model_md5=ev.file_md5(cls.model))
        record["metrics"]["features_md5"] = ev.file_md5(cls.features)
        record["metrics"]["pr_auc"]["value"] = float(train.pr_auc(val["label"], train.score_with(model, val)))
        cls.record = record
        cls.champion = cls.write_champion(record)
        cls.denied = Command.write_config(cls.dir / "denied.toml", "false")
        cls.allowed = Command.write_config(cls.dir / "allowed.toml", "true")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, True)

    @classmethod
    def write_champion(cls, record):
        """champion.json with the model next to it, in a folder of its own, like registry/."""
        folder = Path(tempfile.mkdtemp(dir=cls.dir))
        shutil.copyfile(cls.model, folder / "model.joblib")
        (folder / "champion.json").write_text(json.dumps(record))
        return folder / "champion.json"

    def score(self, *extra, champion=None, config=None, committed=True):
        """(out folder, SystemExit or None) for one --score-champion run."""
        out = Path(tempfile.mkdtemp(dir=self.dir))
        args = ["--score-champion", "--column", "forest", "--champion", str(champion or self.champion), "--config",
                str(config or self.denied), "--features", str(self.features), "--out", str(out), "--no-mlflow", *extra]
        with mock.patch("model.tracking.git_state", return_value=("abc", "false" if committed else "true")), \
                mock.patch("builtins.print"):
            try:
                ev.main(args)
            except SystemExit as e:
                return out, e
        return out, None

    def test_val_scores_and_a_candidate_and_no_test_row(self):
        out, stop = self.score()
        self.assertIsNone(stop)
        val = pd.read_csv(out / "champion_val_scores.csv")
        rows = self.table[self.table["split"] == "val"]
        self.assertEqual(list(val.columns), ["sha", "label", "lines_x_files", "churn", "bug_fixes", "forest"])
        self.assertEqual(val["sha"].tolist(), rows["sha"].tolist())
        candidate = json.loads((out / "champion_candidate.json").read_text())
        self.assertEqual((candidate["column"], candidate["scores_file"], candidate["model_md5"]),
                         ("forest", "champion_val_scores.csv", self.record["model_md5"]))
        self.assertFalse((out / "champion_test_scores.csv").exists())

    def test_test_scores_are_refused_unless_allowed(self):
        out, stop = self.score("--split", "test")
        self.assertIn("test_allowed = true", str(stop))
        self.assertEqual(list(out.iterdir()), [])

    def test_test_scores_are_refused_on_uncommitted_code(self):
        out, stop = self.score("--split", "test", config=self.allowed, committed=False)
        self.assertIn("commit first", str(stop))
        self.assertEqual(list(out.iterdir()), [])

    def test_the_2024_run_end_to_end(self):
        """Allowed and committed: the test scores, then evaluate's test run on them, its threshold from the champion's
        own val scores."""
        out, stop = self.score("--split", "test", config=self.allowed)
        self.assertIsNone(stop)
        test = pd.read_csv(out / "champion_test_scores.csv")
        self.assertEqual(test["sha"].tolist(), self.table[self.table["split"] == "test"]["sha"].tolist())
        common = ["--candidate", str(out / "champion_candidate.json"), "--features", str(self.features), "--no-mlflow",
                  "--no-charts"]
        with mock.patch("model.tracking.git_state", return_value=("abc", "false")), mock.patch("builtins.print"):
            m = ev.main(["--split", "test", "--scores", str(out / "champion_test_scores.csv"), "--config",
                         str(self.allowed), "--out", str(out / "test"), *common])
            v = ev.main(["--config", str(self.denied), "--out", str(out / "val"), *common])
        self.assertEqual((m["split"], m["candidate"]["column"], m["n"]), ("test", "forest", len(test)))
        self.assertEqual(m["models"]["forest"]["at_threshold"]["threshold"],
                         v["models"]["forest"]["at_threshold"]["threshold"])
        self.assertAlmostEqual(v["models"]["forest"]["pr_auc"]["value"], self.record["metrics"]["pr_auc"]["value"],
                               places=12)

    def test_a_model_that_isnt_the_champions_is_refused(self):
        champion = self.write_champion({**self.record, "model_md5": "e" * 32})
        _, stop = self.score(champion=champion)
        self.assertIn("isn't the model", str(stop))

    def test_a_table_that_isnt_the_champions_is_refused(self):
        champion = self.write_champion({**self.record, "metrics": {**self.record["metrics"], "features_md5": "f" * 32}})
        _, stop = self.score(champion=champion)
        self.assertIn("isn't the champion's feature table", str(stop))

    def test_a_model_that_doesnt_give_its_recorded_val_pr_auc_is_refused_before_any_test_row(self):
        pr_auc = {**self.record["metrics"]["pr_auc"], "value": 0.99}
        champion = self.write_champion({**self.record, "metrics": {**self.record["metrics"], "pr_auc": pr_auc}})
        out, stop = self.score("--split", "test", champion=champion, config=self.allowed)
        self.assertIn("isn't the model the record describes", str(stop))
        self.assertEqual(list(out.iterdir()), [])


class MetricsExample(unittest.TestCase):
    """tests/contracts/metrics.json, the example gate.py and registry.py test against (packets/README.md, Integration
    tests). Written by main() from a run on the fake table: python -m tests.contracts.make_rochan."""

    @classmethod
    def setUpClass(cls):
        cls.committed = json.loads((CONTRACTS / "metrics.json").read_text(encoding="utf-8"))

    def test_it_has_the_shape_main_writes(self):
        self.assertEqual(make_rochan.shape(self.committed), make_rochan.shape(make_rochan.build()[0]),
                         "metrics.json's shape changed: run python -m tests.contracts.make_rochan and announce it "
                         "in the team chat (S10)")

    def test_every_number_has_its_n(self):
        m = self.committed
        self.assertEqual(m["models"][m["candidate"]["column"]]["role"], "candidate")
        for name, x in m["models"].items():
            for key, block in x.items():
                if isinstance(block, dict):
                    self.assertEqual((block["n"], block["positive_rate"]), (m["n"], m["positive_rate"]),
                                     f"{name}.{key}")


@unittest.skipUnless((CONTRACTS / "candidate.json").exists() and (CONTRACTS / "val_scores.csv").exists(),
                     "waiting for Rachna's tests/contracts/candidate.json and val_scores.csv "
                     "(packets/README.md, Nov 8)")
class RachnaExamples(unittest.TestCase):
    """evaluate.py reads Rachna's candidate.json and val scores (Contract 4), and fails on a missing key or column.
    load_scores checks the scores against the feature table's val rows; here those rows are the example's own."""

    @classmethod
    def setUpClass(cls):
        cls.candidate = ev.read_candidate(CONTRACTS / "candidate.json")
        cls.scores = pd.read_csv(cls.candidate["scores_file"])
        cls.table = pd.DataFrame({"sha": cls.scores["sha"], "label": cls.scores["label"], "split": "val"})

    def test_the_examples_read(self):
        rows, scores = ev.load_scores(self.table, self.candidate["scores_file"], "val", self.candidate["column"])
        self.assertEqual(len(rows), len(self.scores))
        self.assertIn(self.candidate["column"], scores)
        self.assertIn(ev.REFERENCE, scores)

    def test_a_missing_key_fails(self):
        data = json.loads((CONTRACTS / "candidate.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "candidate.json"
            for key in ("scores_file", "column"):
                path.write_text(json.dumps({k: v for k, v in data.items() if k != key}), encoding="utf-8")
                with self.subTest(key), self.assertRaisesRegex(ev.ScoresError, key):
                    ev.read_candidate(path)

    def test_a_missing_column_fails(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "val_scores.csv"
            for column in ("sha", "label", self.candidate["column"], ev.REFERENCE):
                self.scores.drop(columns=column).to_csv(path, index=False)
                with self.subTest(column), self.assertRaisesRegex(ev.ScoresError, f"no '{column}' column"):
                    ev.load_scores(self.table, path, "val", self.candidate["column"])


class Paths(unittest.TestCase):
    def test_paths_in_metrics_are_posix_and_never_a_home_folder(self):
        self.assertEqual(ev._rel(ev.HERE / "reports" / "x.csv"), "reports/x.csv")
        self.assertEqual(ev._rel(Path(tempfile.gettempdir()) / "y.csv"), "y.csv")


if __name__ == "__main__":
    unittest.main()
