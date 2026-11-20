"""S5 drift: a planted shift flags, a clean sample doesn't (Model-Deployment/packets/vishwa-monitor).

    python -m unittest discover -s tests -t .        # from Model-Deployment/
"""
import unittest

import numpy as np
import pandas as pd

from monitor import drift

CFG = drift.load_config()


def table(n, seed):
    """A fake feature table with the model's input columns, shaped like Spark's: counts, skewed."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({c: rng.negative_binomial(2, 0.1, n) for c in drift.NUMERIC})
    df["entropy"] = rng.uniform(0, 2, n)
    df["age"] = rng.exponential(20, n)
    for flag in ("fix", "has_ticket", "is_minor"):
        df[flag] = rng.integers(0, 2, n)
    df["ticket_type"] = rng.choice(["Bug", "Improvement", "none"], n, p=[0.3, 0.5, 0.2])
    return df


class Drift(unittest.TestCase):
    def test_identical_is_zero(self):
        ref = table(5000, 1)
        self.assertAlmostEqual(drift.psi(ref["la"], ref["la"]), 0, places=9)
        self.assertAlmostEqual(drift.psi(ref["ticket_type"], ref["ticket_type"]), 0, places=9)

    def test_clean_sample_does_not_flag(self):
        for n_ref in (5000, 800):   # both branches of Evidently's stattest choice
            with self.subTest(n_ref=n_ref):
                r = drift.drift_report(table(n_ref, 1), table(500, 2), CFG["drift"])
                self.assertFalse(r["retrain"], r["reasons"])
                self.assertEqual(r["psi_flags"], [])

    def test_planted_shift_flags(self):
        for n_ref in (5000, 800):
            with self.subTest(n_ref=n_ref):
                cur = drift.simulate(table(500, 2), CFG["simulate"])
                r = drift.drift_report(table(n_ref, 1), cur, CFG["drift"])
                self.assertTrue(r["retrain"])
                self.assertEqual(set(r["psi_flags"]), set(CFG["simulate"]["columns"]))  # la, ld, nf, nd

    def test_one_top_feature_is_enough(self):
        """PSI on a single top feature retrains, even with every other column clean."""
        ref, cur = table(5000, 1), table(500, 2)
        cur["prior_bug_fixes"] = 0
        r = drift.drift_report(ref, cur, CFG["drift"])
        self.assertEqual(r["psi_flags"], ["prior_bug_fixes"])
        self.assertTrue(r["retrain"])
        self.assertFalse(r["dataset_drift"])

    def test_a_non_top_feature_alone_does_not_retrain(self):
        ref, cur = table(5000, 1), table(500, 2)
        cur["exp"] = cur["exp"] * 10
        r = drift.drift_report(ref, cur, CFG["drift"])
        self.assertTrue(r["features"]["exp"]["drifted"])
        self.assertGreaterEqual(r["features"]["exp"]["psi"], 0.2)
        self.assertFalse(r["retrain"])

    def test_current_rows_window(self):
        df = pd.DataFrame({"split": ["train", "none", "none", "none"],
                           "authored_at": ["2020-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
                                           "2026-09-15T00:00:00Z", "2026-10-01T00:00:00Z"]})
        self.assertEqual(len(drift.current_rows(df, 0)), 3)
        self.assertEqual(list(drift.current_rows(df, 30)["authored_at"]),
                         ["2026-09-15T00:00:00Z", "2026-10-01T00:00:00Z"])

    def test_features_are_the_model_inputs(self):
        from model.train import CATEGORICAL, NUMERIC
        self.assertEqual(drift.FEATURES, NUMERIC + CATEGORICAL)
        self.assertLessEqual(set(CFG["drift"]["top_features"]), set(drift.FEATURES))


if __name__ == "__main__":
    unittest.main()
