"""Fault 02: A size-only model.
Expected: Gate fails, or passes only if it really beats lines x files.
Why: The bar can't be beaten by a renamed baseline."""
import unittest

from tests.faults._gate import GateFault, planted


class SizeOnly(GateFault):
    def test_lines_x_files_renamed_fails_the_bar(self):
        _, reference, _, _ = planted("size only")
        self.set_pr_auc(reference, reference)  # the baseline's own scores under a model's name: lift 0
        code, result, slack, _, _ = self.gate(skip=("champion.json",))
        self.assertEqual((code, self.failed(result), len(slack)), (1, ["bar"], 1))

    def test_a_size_only_forest_passes_only_above_lines_x_files(self):
        value, reference, _, _ = planted("size only")
        self.set_pr_auc(value, reference)
        code, result, _, _, _ = self.gate(skip=("champion.json",))
        self.assertEqual(code == 0, value > reference, f"size only {value:.4f} vs lines x files {reference:.4f}")
        if code:
            self.assertEqual(self.failed(result), ["bar"])


if __name__ == "__main__":
    unittest.main()
