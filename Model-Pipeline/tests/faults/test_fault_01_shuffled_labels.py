"""Fault 01: Shuffled labels.
Expected: PR-AUC near the positive rate; gate fails; Slack alert.
Why: Proves the gate catches a model that learned nothing."""
import unittest

from tests.faults._gate import GateFault, planted


class ShuffledLabels(GateFault):
    def test_a_model_on_shuffled_labels_is_at_chance_and_fails_the_gate_with_one_alert(self):
        value, reference, rate, ceiling = planted("shuffled labels")
        self.assertLessEqual(value, ceiling, f"PR-AUC {value:.4f}, positive rate {rate:.4f}, chance's 99th pct "
                                             f"{ceiling:.4f}")
        self.set_pr_auc(value, reference)
        code, result, slack, mail, _ = self.gate(skip=("champion.json",))  # a first model: only the bar can stop it
        self.assertEqual(code, 1)
        self.assertEqual(self.failed(result), ["bar"])
        self.assertEqual((len(slack), len(mail)), (1, 1))
        self.assertIn("bar:", slack[0]["text"])


if __name__ == "__main__":
    unittest.main()
