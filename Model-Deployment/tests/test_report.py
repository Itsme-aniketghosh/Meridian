"""S5's D2 report: D2's fields, retrain = drift or decay, a simulated report says so, and the refresh job can read it.

    python -m unittest discover -s tests -t . -p "test_report.py"     # from Model-Deployment/
"""
import unittest

from monitor import report

DRIFT = {"n_current": 454, "drifted_share": 0.625, "retrain": True,
         "reasons": ["PSI 0.2783 >= 0.2 on la"], "window": {"days": 30, "simulated": False},
         "features": {"la": {"psi": 0.2783}, "nf": {"psi": 0.0585}}}
CLEAN = {**DRIFT, "retrain": False, "reasons": [], "drifted_share": 0.1}
DECAY = {"window": "2025Q3", "kind": "quarter", "n": 927, "positives": 52, "pr_auc": 0.2147,
         "lines_x_files": 0.1683, "difference": 0.0464, "paired": [-0.0148, 0.0988], "flag": True,
         "why": "model - lines x files lower bound -0.015 < 0.0 on 2025Q3"}
FINE = {**DECAY, "paired": [0.01, 0.09], "flag": False, "why": "model beats lines x files on 2025Q3"}
THRESHOLDS = {"drift": {"psi": 0.2}, "decay": {"min_positives": 30}}
D2_FIELDS = {"run_id", "at", "n_new", "drift", "decay", "retrain"}
D2_DRIFT = {"psi", "evidently_share", "flag", "why"}
D2_DECAY = {"window", "n", "positives", "pr_auc", "lines_x_files", "paired", "flag", "why"}


def build(drift, decay, simulated=False):
    return report.build("c5118b019d642a9b", "2026-11-21T00:00:00Z", drift, decay, THRESHOLDS, simulated)


class Report(unittest.TestCase):
    def test_d2_fields(self):
        r = build(DRIFT, DECAY)
        self.assertLessEqual(D2_FIELDS, set(r))
        self.assertLessEqual(D2_DRIFT, set(r["drift"]))
        self.assertLessEqual(D2_DECAY, set(r["decay"]))
        self.assertEqual(r["n_new"], 454)
        self.assertEqual(r["drift"]["psi"], {"la": 0.2783, "nf": 0.0585})

    def test_retrain_is_drift_or_decay(self):
        for drift, decay, want in ((DRIFT, DECAY, True), (DRIFT, FINE, True), (CLEAN, DECAY, True),
                                   (CLEAN, FINE, False)):
            with self.subTest(drift=drift["retrain"], decay=decay["flag"]):
                r = build(drift, decay)
                self.assertIs(r["retrain"], want)
                self.assertIs(r["drift"]["flag"], drift["retrain"])
                self.assertIs(r["decay"]["flag"], decay["flag"])

    def test_why_names_each_signal(self):
        self.assertIn("drift: PSI 0.2783", build(DRIFT, DECAY)["why"])
        self.assertIn("decay: model - lines x files", build(DRIFT, DECAY)["why"])
        self.assertNotIn("decay:", build(DRIFT, FINE)["why"])
        self.assertTrue(build(CLEAN, FINE)["why"].startswith("no retrain"))

    def test_simulated_says_so(self):
        r = build(DRIFT, FINE, simulated=True)
        self.assertTrue(r["simulated"])
        self.assertTrue(r["why"].startswith("SIMULATED"))
        self.assertFalse(build(DRIFT, FINE)["simulated"])

    def test_no_settled_window(self):
        r = build(CLEAN, {"window": None, "kind": "quarter", "flag": False, "why": "no quarter with settled labels"})
        self.assertFalse(r["retrain"])
        self.assertIsNone(r["decay"]["window"])

    def test_the_refresh_job_reads_it(self):
        """The fields refresh/job.py reads today, in the shape its own d2() wrapper writes, so the switch is safe."""
        from refresh import job
        theirs = job.d2("c5118b019d642a9b", "2026-11-21T00:00:00Z", {**DRIFT, "features": DRIFT["features"]})
        ours = build(DRIFT, FINE)
        self.assertLessEqual(set(theirs), set(ours))
        self.assertLessEqual(set(theirs["drift"]), set(ours["drift"]))
        for key in ("psi", "evidently_share", "flag", "why", "window"):
            self.assertEqual(ours["drift"][key], theirs["drift"][key], key)
        self.assertEqual((ours["retrain"], ours["n_new"]), (theirs["retrain"], theirs["n_new"]))


if __name__ == "__main__":
    unittest.main()
