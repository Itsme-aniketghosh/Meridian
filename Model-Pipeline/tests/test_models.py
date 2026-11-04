"""model/train.py models: seeds, shuffled labels, train-only fits, the feature matrix."""
import importlib.util
import unittest

import numpy as np

from sklearn.metrics import roc_auc_score

from model.train import (SLICES, build_model, chance_ceiling, feature_matrix, fit_and_score, fit_rows, pr_auc,
                         ticket_categories)
from tests.fake_table import fake_table

HAS_LGBM = importlib.util.find_spec("lightgbm") is not None
KINDS = ["logreg", "forest"] + (["lgbm"] if HAS_LGBM else [])


class Recorder:
    """Stands in for a model and remembers which rows it was fitted on."""

    def fit(self, X, y):
        self.fit_index = list(X.index)
        return self

    def predict_proba(self, X):
        return np.column_stack([np.zeros(len(X)), np.full(len(X), 0.5)])


class TrainOnly(unittest.TestCase):
    def test_fit_sees_exactly_the_train_rows(self):
        df = fake_table()
        rec, scores, scored = fit_and_score(df, "logreg", 1, model=Recorder())
        self.assertEqual(rec.fit_index, list(fit_rows(df).index))
        self.assertEqual(set(scored["split"]), {"val"})
        self.assertEqual(len(scores), len(scored))

    def test_test_rows_cannot_be_scored(self):
        with self.assertRaises(PermissionError):
            fit_and_score(fake_table(), "logreg", 1, score_split="test", model=Recorder())


class FeatureMatrix(unittest.TestCase):
    def test_no_slice_columns_and_fixed_ticket_columns(self):
        df = fake_table(n=200)
        cats = ticket_categories(fit_rows(df))
        X = feature_matrix(df, cats)
        self.assertFalse(set(SLICES) & set(X.columns))
        self.assertEqual([c for c in X.columns if c.startswith("ticket_type=")], [f"ticket_type={c}" for c in cats])

    def test_unseen_ticket_type_is_all_zeros(self):
        df = fake_table(n=50)
        df.loc[0, "ticket_type"] = "Epic"
        X = feature_matrix(df, ["Bug", "none"])
        self.assertEqual(X.loc[0, ["ticket_type=Bug", "ticket_type=none"]].sum(), 0)

    def test_dropping_ticket_features(self):
        X = feature_matrix(fake_table(n=50), ["Bug"], ticket_features=False)
        self.assertFalse({"fix", "has_ticket", "ticket_type=Bug"} & set(X.columns))

    def test_drop_a_group(self):
        X = feature_matrix(fake_table(n=50), ["Bug"], drop=("la", "ld"))
        self.assertFalse({"la", "ld"} & set(X.columns))


class Models(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = fake_table(n=6000, seed=3)

    def test_same_seed_same_predictions(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                _, a, _ = fit_and_score(self.df, kind, seed=1)
                _, b, _ = fit_and_score(self.df, kind, seed=1)
                # Decision 9: within 1e-9. Parallel trees can differ in the last bit (~1e-16),
                # which changes no ranking or metric.
                self.assertLessEqual(np.abs(a - b).max(), 1e-9)

    def test_learns_the_planted_signal(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                _, s, scored = fit_and_score(self.df, kind, seed=1)
                y = scored["label"].to_numpy()
                self.assertGreater(pr_auc(y, s), y.mean() + 0.05)

    def test_shuffled_labels_learn_nothing(self):
        # Three shuffles, judged on the median, so one lucky draw can't fail or pass the test on its own.
        base = fake_table(n=30000, seed=5)
        for kind in KINDS:
            with self.subTest(kind=kind):
                gaps, rocs = [], []
                for shuffle in (1, 2, 3):
                    df = base.copy()
                    df["label"] = np.random.default_rng(shuffle).permutation(df["label"].to_numpy())
                    _, s, scored = fit_and_score(df, kind, seed=1)
                    y = scored["label"].to_numpy()
                    gaps.append(pr_auc(y, s) - chance_ceiling(y))
                    rocs.append(roc_auc_score(y, s))
                self.assertLess(float(np.median(gaps)), 0.0)
                self.assertAlmostEqual(float(np.median(rocs)), 0.5, delta=0.03)

    def test_balanced_weights_keep_the_ranking_close(self):
        _, plain, scored = fit_and_score(self.df, "logreg", seed=1)
        _, bal, _ = fit_and_score(self.df, "logreg", seed=1, imbalance="balanced")
        y = scored["label"].to_numpy()
        self.assertAlmostEqual(pr_auc(y, plain), pr_auc(y, bal), delta=0.02)
        self.assertGreater(bal.mean(), plain.mean())


class BuildModel(unittest.TestCase):
    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            build_model("svm", 1)

    def test_scale_pos_weight_only_for_lgbm(self):
        with self.assertRaises(ValueError):
            build_model("forest", 1, imbalance="scale_pos_weight", pos_rate=0.2)

    @unittest.skipUnless(HAS_LGBM, "lightgbm not installed")
    def test_lgbm_is_deterministic_by_config(self):
        m = build_model("lgbm", 1)
        p = m.get_params()
        self.assertTrue(p["deterministic"])
        self.assertEqual(p["n_jobs"], 1)


if __name__ == "__main__":
    unittest.main()
