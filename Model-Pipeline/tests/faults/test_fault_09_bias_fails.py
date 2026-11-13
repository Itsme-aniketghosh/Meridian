"""Fault 09: Bias fails, PR-AUC better.
Expected: Gate fails.
Why: Bias is part of the pick."""
import unittest

from tests.faults._gate import GateFault


class BiasFails(GateFault):
    def test_a_better_model_that_fails_bias_fails_the_gate_on_bias_alone(self):
        self.set_pr_auc(0.50, 0.30, champion=0.40)  # beats the bar and the champion by far
        self.docs["bias_report.json"]["pass"] = False
        code, result, slack, _, _ = self.gate()
        self.assertEqual((code, self.failed(result), len(slack)), (1, ["bias"], 1))


if __name__ == "__main__":
    unittest.main()
