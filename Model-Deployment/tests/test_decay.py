"""S5 decay: a model that still beats lines x files doesn't flag, one that stopped does, and only settled windows count.

    python -m unittest discover -s tests -t . -p "test_decay.py"     # from Model-Deployment/
"""
import unittest

import numpy as np
import pandas as pd

from monitor import decay, drift

CFG = drift.load_config()["decay"]   # as signed off: half-years, 100+ positives, no drop rule
QUARTERS = {**CFG, "window": "quarter", "min_positives": 30, "max_drop": 0.05}   # the rules first written
SNAPSHOT = "2026-10-01T00:00:00Z"


def table(seed=0, per_quarter=800, rate=0.12):
    """Commits from 2024Q1 to 2026Q3. Bigger commits are a bit more often bug-inducing, so lines x files has signal."""
    rng = np.random.default_rng(seed)
    days = pd.date_range("2024-01-01", "2026-09-30", freq="D")
    when = rng.choice(days, per_quarter * 11)
    df = pd.DataFrame({"authored_at": pd.to_datetime(when).strftime("%Y-%m-%dT%H:%M:%SZ")})
    df["la"] = rng.negative_binomial(2, 0.05, len(df))
    df["ld"] = rng.negative_binomial(2, 0.2, len(df))
    df["nf"] = rng.integers(1, 6, len(df))
    size = np.log1p((df["la"] + df["ld"]) * df["nf"])
    p = 1 / (1 + np.exp(-(size - size.mean()) * 0.4 + np.log(1 / rate - 1)))
    df["label"] = (rng.random(len(df)) < p).astype(int)
    return df.sort_values("authored_at", ignore_index=True)


def oracle(rows):
    """A model that knows more than lines x files: the label, blurred."""
    rng = np.random.default_rng(1)
    return rows["label"].to_numpy() * 0.6 + rng.random(len(rows)) * 0.5


def coin(rows):
    """A model that has stopped working: noise."""
    return np.random.default_rng(2).random(len(rows))


class Decay(unittest.TestCase):
    def test_settled_windows_only(self):
        """At a 2026-10-01 snapshot with 365-day labels, 2025H1 is the newest settled half, 2025Q3 the quarter."""
        df = table()
        self.assertEqual(decay.closed_windows(df, SNAPSHOT, CFG)[0][0], "2025H1")
        self.assertEqual(decay.closed_windows(df, SNAPSHOT, QUARTERS)[0][0], "2025Q3")

    def test_min_positives(self):
        df = table()
        q = decay.window_keys(df["authored_at"], "quarter")
        df.loc[q == "2025Q3", "label"] = 0   # the newest settled quarter has no positives: skipped
        self.assertEqual(decay.closed_windows(df, SNAPSHOT, QUARTERS)[0][0], "2025Q2")
        h = decay.window_keys(df["authored_at"], "half")
        df.loc[(h == "2025H1") & (df["label"] == 1), "label"] = [1] * 99 + [0] * int(  # 99 positives: under 100
            ((h == "2025H1") & (df["label"] == 1)).sum() - 99)
        self.assertEqual(decay.closed_windows(df, SNAPSHOT, CFG)[0][0], "2024H2")

    def test_a_model_that_works_does_not_flag(self):
        r = decay.decay_report(table(), oracle, SNAPSHOT, CFG, reps=300)
        self.assertEqual(r["window"], "2025H1")
        self.assertGreater(r["paired"][0], 0)
        self.assertFalse(r["flag"], r["why"])

    def test_a_model_that_stopped_working_flags(self):
        r = decay.decay_report(table(), coin, SNAPSHOT, CFG, reps=300)
        self.assertLess(r["paired"][0], CFG["min_paired_lower"])
        self.assertTrue(r["flag"])
        self.assertIn("lower bound", r["why"])

    def test_a_drop_from_the_previous_window_flags(self):
        """Good on 2025Q2, noise on 2025Q3: flagged for the drop too, while [decay] max_drop is set. As signed off
        it isn't: the previous window is reported, and only the paired bound flags."""
        df = table()
        newest = decay.window_keys(df["authored_at"], "quarter") == "2025Q3"
        score = lambda rows: coin(rows) if newest[rows.index].all() else oracle(rows)  # noqa: E731
        r = decay.decay_report(df, score, SNAPSHOT, QUARTERS, reps=300)
        self.assertEqual(r["previous"]["window"], "2025Q2")
        self.assertIn("under 2025Q2", r["why"])
        r = decay.decay_report(df, score, SNAPSHOT, {**QUARTERS, "max_drop": None}, reps=300)
        self.assertEqual(r["previous"]["window"], "2025Q2")
        self.assertNotIn("under 2025Q2", r["why"])

    def test_nothing_settled(self):
        r = decay.decay_report(table(), oracle, "2024-06-01T00:00:00Z", CFG, reps=100)
        self.assertIsNone(r["window"])
        self.assertFalse(r["flag"])

    def test_thresholds_come_from_deploy_toml(self):
        from deploy_config import load
        self.assertEqual(CFG, load(use_env=False)["decay"])
        self.assertEqual((CFG["window"], CFG["min_positives"]), ("half", 100))
        self.assertNotIn("max_drop", CFG)
        self.assertEqual(drift.load_config()["drift"]["psi_threshold"], load(use_env=False)["drift"]["psi"])


if __name__ == "__main__":
    unittest.main()
