"""model/sensitivity.py: SHAP's guarantee, decisions 10-12's rules, grouped permutation, and the command."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from model import sensitivity as S
from model.train import evaluate, fit_and_score, inputs_for, pick_candidate, rows, save_model
from tests.fake_table import fake_table

HAS_SHAP = importlib.util.find_spec("shap") is not None
HAS_OPTUNA = importlib.util.find_spec("optuna") is not None
HAS_LGBM = importlib.util.find_spec("lightgbm") is not None


class Shap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = fake_table(n=2000, seed=3)
        cls.val = rows(cls.df, "val")

    @unittest.skipUnless(HAS_SHAP, "shap not installed")
    def test_values_sum_to_the_prediction_minus_the_base(self):
        """The packet's test: on a tiny tree model, SHAP values add up to the model's output."""
        kinds = {"forest": {"n_estimators": 20, "max_depth": 4}, "logreg": None}
        if HAS_LGBM:
            kinds["lgbm"] = {"n_estimators": 20, "num_leaves": 8}
        for kind, params in kinds.items():
            with self.subTest(kind=kind):
                model, _, _ = fit_and_score(self.df, kind, 1, params)
                X = inputs_for(model, self.val)
                values, base, _ = S.shap_values(model, X, kind)
                self.assertEqual(values.shape, X.shape)
                self.assertLess(S.additivity_error(model, X, values, base, kind), 1e-6)

    def test_smallest_stable_sample(self):
        rng = np.random.default_rng(0)
        cols = [f"f{i}" for i in range(12)]
        scale = np.arange(12, 0, -1, dtype=float)      # a clear ranking every sample keeps
        values = rng.normal(0, 1, (3000, 12)) * scale
        r = S.smallest_stable_sample(values, cols, seed=1, sizes=(2000, 1000, 500))
        self.assertEqual(r["smallest_stable"], 500)
        self.assertEqual(r["top"], cols[:10])
        flat = rng.normal(0, 1, (3000, 12))             # no ranking: small samples scramble it
        r = S.smallest_stable_sample(flat, cols, seed=1, sizes=(2000, 1000, 500))
        self.assertGreater(r["smallest_stable"], 500)

    def test_size_share(self):
        imp = pd.Series({"la": 3.0, "nf": 1.0, "age": 4.0})
        self.assertAlmostEqual(S.size_share(imp), 0.5)

    def test_slice_shap_reads_the_flagged_slices(self):
        val = pd.DataFrame({"module": ["core", "core", "sql", "sql"]})
        values = np.array([[1.0, 0.0], [1.0, 0.0], [0.0, 2.0], [0.0, 2.0]])
        flags = [{"slice": "module=core", "metric": "recall"}, {"slice": "module=core", "metric": "pr_auc"}]
        out = S.slice_shap(values, ["a", "b"], val, flags)
        self.assertEqual(list(out), ["module=core"])
        self.assertEqual(out["module=core"]["n"], 2)
        self.assertEqual(list(out["module=core"]["top"])[0], "a")


class Permutation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df = fake_table(n=4000, seed=5)
        cls.model, _, scored = fit_and_score(cls.df, "forest", 1, {"n_estimators": 60})
        cls.X = inputs_for(cls.model, scored)
        cls.y = scored["label"].to_numpy()

    def test_units_group_the_correlated_columns_and_ticket_type(self):
        u = S.units(list(self.X.columns))
        self.assertEqual(u["lines (la+ld)"], ["la", "ld"])
        self.assertEqual(u["spread (nf+nd+ns)"], ["nf", "nd", "ns"])
        self.assertTrue(all(c.startswith("ticket_type=") for c in u["ticket_type"]))
        self.assertNotIn("la", u)
        flat = [c for cols in u.values() for c in cols]
        self.assertEqual(sorted(flat), sorted(self.X.columns))

    def test_signal_matters_and_noise_doesnt(self):
        _, perm = S.permutation_importance(self.model, self.X, self.y, seed=1, repeats=3)
        # fake_table plants most of its signal in nf and prior_bug_fixes; entropy is pure noise
        self.assertGreater(perm["spread (nf+nd+ns)"][0], 0.05)
        self.assertGreater(perm["prior_bug_fixes"][0], 0.05)
        self.assertLess(abs(perm["entropy"][0]), 0.02)

    def test_compare_rankings_flags_a_group_shap_underrates(self):
        perm = {"lines (la+ld)": (0.10, 0), "spread (nf+nd+ns)": (0.01, 0), "age": (0.05, 0), "exp": (0.04, 0),
                "nuc": (0.0, 0)}
        shap_units = {"lines (la+ld)": 0.01, "spread (nf+nd+ns)": 0.0, "age": 0.5, "exp": 0.4, "nuc": 0.3}
        r = S.compare_rankings(perm, shap_units)
        self.assertEqual(r["groups_shap_underrates"], ["lines (la+ld)"])


class Sweep(unittest.TestCase):
    def test_points_stay_in_range_and_round_ints(self):
        pts = S.sweep_points("n_estimators", 200, {"type": "int", "low": 100, "high": 600, "step": 100})
        self.assertEqual(pts[0], ("tuned", 200))
        self.assertEqual(dict(pts)["-50%"], 100)
        self.assertEqual(dict(pts)["+50%"], 300)
        self.assertEqual(dict(pts)["high"], 600)
        self.assertTrue(all(isinstance(v, int) for _, v in pts))
        clipped = S.sweep_points("min_samples_leaf", 5, {"type": "int", "low": 5, "high": 200, "log": True})
        self.assertTrue(all(5 <= v <= 200 for _, v in clipped))
        self.assertEqual(len({v for _, v in clipped}), len(clipped))

    def test_categorical_points_are_the_choices(self):
        pts = S.sweep_points("max_depth", 8, {"type": "categorical", "choices": ["none", 8, 16]})
        self.assertEqual(pts, [("choice", None), ("tuned", 8), ("choice", 16)])

    def test_verdict(self):
        pts = [{"label": "tuned", "pr_auc": 0.40}, {"label": "-25%", "pr_auc": 0.401},
               {"label": "+25%", "pr_auc": 0.399}, {"label": "-50%", "pr_auc": 0.398},
               {"label": "+50%", "pr_auc": 0.402}, {"label": "low", "pr_auc": 0.30}]
        v = S.sweep_verdict(pts, sd=0.0021)
        self.assertAlmostEqual(v["spread_50"], 0.004)
        self.assertTrue(v["safe_to_retrain"])
        self.assertAlmostEqual(v["spread_full"], 0.102)
        peak = [dict(p) for p in pts]
        peak[4]["pr_auc"] = 0.35
        self.assertFalse(S.sweep_verdict(peak, sd=0.0021)["safe_to_retrain"])
        cat = [{"label": "tuned", "pr_auc": 0.40}, {"label": "choice", "pr_auc": 0.39}]
        v = S.sweep_verdict(cat, sd=0.0021)
        self.assertIsNone(v["spread_50"])
        self.assertIsNone(v["safe_to_retrain"])          # no +-% for a choice: reported, not judged
        self.assertAlmostEqual(v["spread_full"], 0.01)

    def test_charts_take_a_parameter_mixing_text_and_numbers(self):
        """The real forest's max_features is "sqrt", 0.3 or 0.5; the trials chart crashed on it."""
        trials = [{"params": {"max_features": v, "n_estimators": n}, "value": 0.3 + 0.01 * i}
                  for i, (v, n) in enumerate([("sqrt", 100), (0.3, 200), (0.5, 300), ("sqrt", 400)])]
        with tempfile.TemporaryDirectory() as d:
            written = S.charts(Path(d), hparams={"max_features": 0.9, "n_estimators": 0.1}, trials=trials,
                               sweeps={"max_features": [{"label": "tuned", "value": "sqrt", "pr_auc": 0.4},
                                                        {"label": "choice", "value": 0.3, "pr_auc": 0.39}]}, sd=0.002)
            self.assertIn("hparam_slices.png", written)
            self.assertIn("sweep_max_features.png", written)

    def test_sweep_refits_one_parameter_at_a_time(self):
        df = fake_table(n=2000, seed=7)
        space = {"n_estimators": {"type": "int", "low": 20, "high": 80, "step": 10}}
        out = S.sweep(df, "forest", {"n_estimators": 40, "max_depth": 4}, 1, space, ["n_estimators"])
        values = [p["value"] for p in out["n_estimators"]]
        self.assertEqual(values[0], 40)
        self.assertIn(80, values)
        self.assertTrue(all(0 <= p["pr_auc"] <= 1 for p in out["n_estimators"]))


@unittest.skipUnless(HAS_OPTUNA, "optuna not installed")
class Hparams(unittest.TestCase):
    def test_importance_from_saved_trials(self):
        rng = np.random.default_rng(0)
        space = {"a": {"type": "float", "low": 0.0, "high": 1.0}, "b": {"type": "int", "low": 1, "high": 9},
                 "c": {"type": "categorical", "choices": ["none", 8]}}
        trials = []
        for _ in range(40):
            p = {"a": float(rng.random()), "b": int(rng.integers(1, 10)), "c": ["none", 8][int(rng.integers(0, 2))]}
            trials.append({"params": p, "value": p["a"] * 2 + rng.normal(0, 0.01)})   # only a matters
        imp = S.param_importance(trials, space, seed=1)
        self.assertEqual(max(imp, key=imp.get), "a")
        self.assertEqual(imp, S.param_importance(trials, space, seed=1))


class Command(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.d = Path(cls.tmp.name)
        cls.df = fake_table(n=3000, seed=8)
        cls.features = d / "f.csv"
        cls.df.to_csv(cls.features, index=False)
        cls.out = d / "r"
        evaluate(cls.df, 1, cls.out, {"forest": {"n_estimators": 40, "max_depth": 6}}, "t", reps=50)
        pick_candidate(cls.out, "t", 1, reps=50)
        save_model(cls.df, cls.out / "candidate.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_parts(self, *parts):
        return S.main(["--candidate", str(self.out / "candidate.json"), "--config", str(self.d / "none.toml"),
                       "--features", str(self.features), "--out", str(self.out), "--no-mlflow", "--parts", *parts])

    def test_permutation_writes_its_report_and_chart(self):
        r = self.run_parts("permutation")
        self.assertIn("lines (la+ld)", r["permutation"]["drop"])
        self.assertTrue((self.out / "permutation_importance.png").exists())
        self.assertIn("permutation", json.loads((self.out / "sensitivity.json").read_text()))

    @unittest.skipUnless(HAS_SHAP, "shap not installed")
    def test_shap_writes_beeswarm_dependence_and_decision_10(self):
        r = self.run_parts("shap", "permutation")
        self.assertLess(r["shap"]["additivity_max_error"], 1e-6)
        self.assertIn("smallest_stable", r["shap"]["sample"])
        self.assertTrue((self.out / "shap_beeswarm.png").exists())
        self.assertEqual(len(list(self.out.glob("shap_dependence_*.png"))), 5)
        self.assertIn("vs_shap", r["permutation"])

    def test_a_changed_model_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            c = json.loads((self.out / "candidate.json").read_text())
            (Path(d) / "model.joblib").write_bytes(b"not the model")
            (Path(d) / "candidate.json").write_text(json.dumps(c))
            with self.assertRaisesRegex(SystemExit, "not the judged model"):
                S.load_candidate(Path(d) / "candidate.json")

    def test_a_candidate_without_a_model_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "candidate.json").write_text(json.dumps({"kind": "forest", "params": {}}))
            with self.assertRaisesRegex(SystemExit, "--save-model"):
                S.load_candidate(Path(d) / "candidate.json")


if __name__ == "__main__":
    unittest.main()
