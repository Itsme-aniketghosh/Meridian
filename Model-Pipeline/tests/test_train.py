"""model/train.py: baselines, the table check, the test-row guard, time folds, PR-AUC."""
import unittest

import numpy as np

from model.train import (FeatureError, baseline_scores, check_table, fit_rows, pr_auc, rows, time_folds,
                         CONTRACT_COLUMNS, SLICES, NUMERIC)
from tests.fake_table import fake_table

SPLIT_YEARS = {"train": [2015, 2022], "val": 2023, "test": 2024}


def hand_table():
    """Five commits whose baseline scores are worked out by hand below."""
    df = fake_table(n=5, seed=1, years=[2020])
    df["la"] = [10, 0, 100, 5, 1]
    df["ld"] = [2, 0, 50, 5, 0]
    df["nf"] = [3, 1, 2, 10, 1]
    df["prior_bug_fixes"] = [4, 0, 1, 7, 2]
    return df


class Baselines(unittest.TestCase):
    def test_match_hand_calculation(self):
        s = baseline_scores(hand_table())
        # lines x files = (la + ld) * nf: 12*3, 0*1, 150*2, 10*10, 1*1
        self.assertEqual(s["lines_x_files"].tolist(), [36, 0, 300, 100, 1])
        # churn = la + ld
        self.assertEqual(s["churn"].tolist(), [12, 0, 150, 10, 1])
        self.assertEqual(s["bug_fixes"].tolist(), [4, 0, 1, 7, 2])

    def test_one_score_per_row_and_float(self):
        df = fake_table(n=50)
        for name, score in baseline_scores(df).items():
            self.assertEqual(len(score), len(df), name)
            self.assertEqual(score.dtype, float, name)


class CheckTable(unittest.TestCase):
    def test_fake_table_passes(self):
        check_table(fake_table(), SPLIT_YEARS)

    def test_missing_column_names_it(self):
        with self.assertRaisesRegex(FeatureError, "missing column.*prior_bug_fixes"):
            check_table(fake_table(n=20).drop(columns=["prior_bug_fixes"]))

    def test_missing_value_names_the_column(self):
        df = fake_table(n=20)
        df["la"] = df["la"].astype(float)
        df.loc[3, "la"] = np.nan
        with self.assertRaisesRegex(FeatureError, "la: 1 missing"):
            check_table(df)

    def test_bad_label(self):
        df = fake_table(n=20, years=[2020])
        df.loc[0, "label"] = 2
        with self.assertRaisesRegex(FeatureError, "label"):
            check_table(df)

    def test_unknown_split(self):
        df = fake_table(n=20)
        df.loc[0, "split"] = "holdout"
        with self.assertRaisesRegex(FeatureError, "unknown split"):
            check_table(df)

    def test_split_year_mismatch(self):
        df = fake_table(n=200)
        df.loc[df.index[df["split"] == "val"][0], "year"] = 2021
        with self.assertRaisesRegex(FeatureError, "val rows outside 2023-2023"):
            check_table(df, SPLIT_YEARS)

    def test_duplicate_sha(self):
        df = fake_table(n=20)
        df.loc[1, "sha"] = df.loc[0, "sha"]
        with self.assertRaisesRegex(FeatureError, "duplicate sha"):
            check_table(df)

    def test_slices_are_not_inputs(self):
        self.assertFalse(set(SLICES) & set(NUMERIC))


class NoTestRows(unittest.TestCase):
    def test_rows_refuses_test(self):
        with self.assertRaises(PermissionError):
            rows(fake_table(), "val", "test")

    def test_fit_rows_is_train_only(self):
        df = fake_table()
        got = fit_rows(df)
        self.assertEqual(set(got["split"]), {"train"})
        self.assertEqual(len(got), int((df["split"] == "train").sum()))
        self.assertTrue(got["year"].between(2015, 2022).all())


class TimeFolds(unittest.TestCase):
    def test_no_fold_fits_on_a_year_it_scores_or_later(self):
        train = fit_rows(fake_table())
        folds = time_folds(train, n_folds=3)
        self.assertEqual([y for _, _, y in folds], [2020, 2021, 2022])
        for fit, score, year in folds:
            self.assertTrue((train.loc[fit, "year"] < year).all())
            self.assertTrue((train.loc[score, "year"] == year).all())
            self.assertFalse(set(fit) & set(score))

    def test_too_few_years(self):
        train = fit_rows(fake_table(years=[2021, 2022]))
        with self.assertRaises(ValueError):
            time_folds(train, n_folds=3)


class PrAuc(unittest.TestCase):
    def test_perfect_ranking_is_one(self):
        self.assertEqual(pr_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]), 1.0)

    def test_random_scores_land_near_the_positive_rate(self):
        rng = np.random.default_rng(7)
        y = (rng.random(20000) < 0.17).astype(int)
        self.assertAlmostEqual(pr_auc(y, rng.random(20000)), y.mean(), delta=0.02)

    def test_one_class_is_refused(self):
        with self.assertRaises(ValueError):
            pr_auc([0, 0, 0], [0.1, 0.5, 0.9])


class FakeTableShape(unittest.TestCase):
    def test_columns_in_contract_order(self):
        self.assertEqual(list(fake_table(n=10).columns), CONTRACT_COLUMNS)

    def test_sorted_by_time_then_sha(self):
        df = fake_table(n=100)
        self.assertTrue(df.equals(df.sort_values(["authored_at", "sha"]).reset_index(drop=True)))


if __name__ == "__main__":
    unittest.main()
