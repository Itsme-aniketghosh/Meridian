"""Tuning: the fold objective, the decision 6 stop rule, search spaces, and Optuna end to end."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from model.train import (DEFAULT_SEARCH, best_so_far, curve_is_flat, fit_rows, fold_pr_auc, main, rows,
                         search_space, suggest, tune)
from tests.fake_table import fake_table

HAS_OPTUNA = importlib.util.find_spec("optuna") is not None


class FakeTrial:
    """Answers suggest_* with the low end, or the first choice, so suggest() can be checked without Optuna."""

    def suggest_float(self, name, low, high, log=False):
        return low

    def suggest_int(self, name, low, high, step=1, log=False):
        return low

    def suggest_categorical(self, name, choices):
        return choices[0]


class StopRule(unittest.TestCase):
    def test_best_so_far_never_drops(self):
        self.assertEqual(best_so_far([0.1, 0.3, 0.2, 0.4, 0.35]), [0.1, 0.3, 0.3, 0.4, 0.4])

    def test_flat_when_the_last_window_adds_less_than_gain(self):
        values = [0.30] * 10 + [0.40] + [0.401] * 30
        self.assertTrue(curve_is_flat(values, window=30, gain=0.002))

    def test_not_flat_when_the_last_window_improves(self):
        values = [0.30] * 20 + [0.31] * 20
        self.assertFalse(curve_is_flat(values, window=30, gain=0.002))

    def test_too_few_trials_is_never_flat(self):
        self.assertFalse(curve_is_flat([0.3] * 30, window=30))


class Spaces(unittest.TestCase):
    def test_every_kind_has_a_space(self):
        self.assertEqual(set(DEFAULT_SEARCH), {"logreg", "forest", "lgbm"})

    def test_suggest_maps_none(self):
        params = suggest(FakeTrial(), {"max_depth": {"type": "categorical", "choices": ["none", 8]}})
        self.assertIsNone(params["max_depth"])

    def test_suggest_covers_every_default_parameter(self):
        for kind, space in DEFAULT_SEARCH.items():
            self.assertEqual(set(suggest(FakeTrial(), space)), set(space), kind)

    def test_unknown_type_is_refused(self):
        with self.assertRaises(ValueError):
            suggest(FakeTrial(), {"x": {"type": "complex"}})

    def test_model_toml_overrides_one_parameter(self):
        merged = search_space({"search": {"lgbm": {"num_leaves": {"type": "int", "low": 4, "high": 16}}}})
        self.assertEqual(merged["lgbm"]["num_leaves"]["high"], 16)
        self.assertEqual(merged["lgbm"]["learning_rate"], DEFAULT_SEARCH["lgbm"]["learning_rate"])
        self.assertEqual(DEFAULT_SEARCH["lgbm"]["num_leaves"]["high"], 128)

    def test_unknown_kind_in_model_toml(self):
        with self.assertRaises(ValueError):
            search_space({"search": {"svm": {}}})


class FoldObjective(unittest.TestCase):
    def test_refuses_anything_but_train(self):
        df = fake_table(n=2000)
        with self.assertRaises(PermissionError):
            fold_pr_auc(rows(df, "train", "val"), "logreg", 1, None)

    def test_three_folds_and_their_mean(self):
        mean, folds = fold_pr_auc(fit_rows(fake_table(n=3000)), "logreg", 1, None)
        self.assertEqual(len(folds), 3)
        self.assertAlmostEqual(mean, sum(folds) / 3)


@unittest.skipUnless(HAS_OPTUNA, "optuna not installed")
class Tune(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = fake_table(n=3000, seed=4)
        cls.a = tune(cls.df, "logreg", seed=1, start=6, step=4, max_trials=10, window=3, gain=0.5)

    def test_same_seed_same_study(self):
        b = tune(self.df, "logreg", seed=1, start=6, step=4, max_trials=10, window=3, gain=0.5)
        self.assertEqual(self.a["best_params"], b["best_params"])
        self.assertEqual(self.a["curve"], b["curve"])

    def test_stops_at_the_first_flat_batch(self):
        # gain=0.5 can't be reached, so the curve is flat after the first batch of 6.
        self.assertEqual(self.a["n_trials"], 6)
        self.assertEqual(self.a["stopped"], "flat")

    def test_runs_to_the_cap_when_never_flat(self):
        r = tune(self.df, "logreg", seed=1, start=4, step=3, max_trials=10, window=3, gain=-1)
        self.assertEqual(r["n_trials"], 10)
        self.assertEqual(r["stopped"], "max_trials")

    def test_val_and_test_rows_change_nothing(self):
        only_train = fit_rows(self.df)
        b = tune(only_train, "logreg", seed=1, start=6, step=4, max_trials=10, window=3, gain=0.5)
        self.assertEqual(self.a["curve"], b["curve"])

    def test_record_is_complete(self):
        self.assertEqual(len(self.a["trials"]), self.a["n_trials"])
        self.assertEqual(self.a["curve"][-1], self.a["best_value"])
        self.assertEqual(len(self.a["best_folds"]), 3)

    def test_on_trial_sees_every_trial(self):
        seen = []
        tune(self.df, "logreg", seed=1, start=3, max_trials=3, window=30,
             on_trial=lambda n, p, v, f: seen.append(n))
        self.assertEqual(seen, [0, 1, 2])


@unittest.skipUnless(HAS_OPTUNA, "optuna not installed")
class TuneCommand(unittest.TestCase):
    def test_writes_the_study_and_the_tuned_val_summary(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            fake_table(n=3000, seed=6).to_csv(d / "f.csv", index=False)
            main(["--tune", "logreg", "--features", str(d / "f.csv"), "--config", str(d / "none.toml"),
                  "--out", str(d / "r"), "--start", "3", "--step", "2", "--max-trials", "5", "--reps", "100"])
            study = json.loads((d / "r" / "tuning_logreg.json").read_text())
            tuned = json.loads((d / "r" / "tuned_val.json").read_text())
        self.assertLessEqual(study["n_trials"], 5)
        self.assertEqual(tuned["tag"], "tuned")
        self.assertEqual(tuned["params"]["logreg"], study["best_params"])
        self.assertIn("logreg", tuned["results"])


if __name__ == "__main__":
    unittest.main()
