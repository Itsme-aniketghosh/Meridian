"""pmap: the seeds, the model families and the tuning studies run in separate processes. Same numbers as a loop."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import importlib.util

import numpy as np

from model.train import evaluate, fit_and_score, pmap, seed_noise, tune
from tests.fake_table import fake_table


def square(x):
    return x * x


class Parallel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = fake_table(n=3000, seed=4)

    def one_and_three(self, run):
        """run(out_dir) with one process, then with three."""
        out = []
        for workers in ("1", "3"):
            with mock.patch.dict(os.environ, {"MERIDIAN_WORKERS": workers}), tempfile.TemporaryDirectory() as d:
                out.append(run(Path(d)))
        return out

    def test_order_is_kept(self):
        with mock.patch.dict(os.environ, {"MERIDIAN_WORKERS": "3"}):
            self.assertEqual(pmap(square, [(i,) for i in range(7)]), [i * i for i in range(7)])

    def test_seed_noise_is_the_same(self):
        one, three = self.one_and_three(
            lambda d: seed_noise(self.df, "forest", {"n_estimators": 50, "min_samples_leaf": 10}, d))
        self.assertEqual(one["pr_auc"], three["pr_auc"])

    def test_evaluate_is_the_same(self):
        models = {"logreg": None, "forest": {"n_estimators": 50}}
        one, three = self.one_and_three(lambda d: evaluate(self.df, 1, d, models, "t", reps=50))
        self.assertEqual(one["results"], three["results"])

    def test_tuning_is_the_same(self):
        one, three = self.one_and_three(
            lambda d: pmap(tune, [(self.df, kind, 1, None, 4, 2, 4) for kind in ("logreg", "forest")]))
        self.assertEqual([r["trials"] for r in one], [r["trials"] for r in three])

    @unittest.skipUnless(importlib.util.find_spec("lightgbm"), "lightgbm not installed")
    def test_threads_never_change_a_prediction(self):
        # decision 9's bar (1e-9) for every thread count we might run with
        got = {}
        for n in ("1", "4"):
            with mock.patch.dict(os.environ, {"MERIDIAN_THREADS": n}):
                for kind in ("lgbm", "forest"):
                    got[kind, n] = fit_and_score(self.df, kind, 1, {"n_estimators": 60})[1]
        for kind in ("lgbm", "forest"):
            self.assertLessEqual(float(np.max(np.abs(got[kind, "1"] - got[kind, "4"]))), 1e-9, kind)


if __name__ == "__main__":
    unittest.main()
