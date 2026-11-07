"""Integration (packets/README.md): Chaitali's example output, and the others' examples that gate.py reads.

Each producer commits an example of its file in tests/contracts/. This checks mine is current and complete, and
that every other example gate.py reads has the keys model/contracts.py lists. An example not committed yet is
skipped and says whose it is; once it lands, a missing key fails here, before integration day."""
import json
import unittest
from pathlib import Path

from model import contracts
from tests.contracts import make_bias_report

HERE = Path(__file__).resolve().parent / "contracts"
OTHERS = {"metrics.json": ("metrics", "Rochan"), "seed_noise.json": ("seed_noise", "Rachna"),
          "candidate.json": ("candidate", "Rachna"), "champion.json": ("champion", "Rochan")}


class MyExample(unittest.TestCase):
    def test_bias_report_has_every_key_the_gate_reads(self):
        doc = json.loads((HERE / "bias_report.json").read_text(encoding="utf-8"))
        self.assertEqual(contracts.missing(doc, "bias_report"), [])

    def test_example_is_what_bias_py_writes_today(self):
        committed = (HERE / "bias_report.json").read_text(encoding="utf-8")
        self.assertEqual(committed, make_bias_report.text(),
                         "bias.py's output changed: run python -m tests.contracts.make_bias_report and commit it")

    def test_example_has_a_flag_and_fails(self):
        # the example plants noise on module core, so the gate's consumers see a failing report's shape too
        doc = json.loads((HERE / "bias_report.json").read_text(encoding="utf-8"))
        self.assertIn("module=core", {f["slice"] for f in doc["flags"]})
        self.assertFalse(doc["pass"])


class Checker(unittest.TestCase):
    def test_a_missing_key_is_named(self):
        doc = json.loads((HERE / "bias_report.json").read_text(encoding="utf-8"))
        del doc["pass"]
        del doc["slices"]["module"]["core"]["recall"]["ci"]
        gone = contracts.missing(doc, "bias_report")
        self.assertIn("pass", gone)
        self.assertIn("slices.module.core.recall.ci", gone)

    def test_metrics_paths_follow_its_candidate(self):
        doc = {"split": "val", "n": 10, "positives": 2, "candidate": {"column": "lgbm"}, "reference": "lines_x_files",
               "models": {"lgbm": {"pr_auc": {"value": 0.4, "interval": [0.3, 0.5]},
                                   "vs_lines_x_files": {"pr_auc_difference": 0.1, "interval": [0.0, 0.2]}},
                          "lines_x_files": {"pr_auc": {"value": 0.3}}}}
        self.assertEqual(contracts.missing(doc, "metrics"), [])
        del doc["models"]["lgbm"]["vs_lines_x_files"]
        self.assertIn("models.lgbm.vs_lines_x_files.pr_auc_difference", contracts.missing(doc, "metrics"))


class OthersExamples(unittest.TestCase):
    """One test per example gate.py reads. Skipped, naming the owner, until the example is committed."""

    def check(self, name):
        kind, owner = OTHERS[name]
        path = HERE / name
        if not path.exists():
            self.skipTest(f"{name} not committed yet ({owner}, due Nov 8)")
        self.assertEqual(contracts.missing(json.loads(path.read_text(encoding="utf-8")), kind), [])

    def test_metrics(self):
        self.check("metrics.json")

    def test_seed_noise(self):
        self.check("seed_noise.json")

    def test_candidate(self):
        self.check("candidate.json")

    def test_champion(self):
        self.check("champion.json")


if __name__ == "__main__":
    unittest.main()
