"""Fault 08: Better only within seed noise.
Expected: Not promoted.
Why: The champion mustn't flip on noise."""
import unittest

from tests import test_registry as R


class WithinNoise(R.Store):
    def test_a_gain_inside_the_margin_is_registered_not_promoted(self):
        self.register(self.files(0.40, sd=0.01))
        tags = self.register(self.files(0.415, sd=0.01))  # +0.015, under 2 sds of seed noise (0.02)
        self.assertEqual((tags["version"], tags["promoted"], self.aliases()["champion"]), (2, "false", 1))
        self.assertIn("seed noise", tags["reason"])

    def test_a_gain_past_the_margin_is_promoted(self):
        self.register(self.files(0.40, sd=0.01))
        tags = self.register(self.files(0.43, sd=0.01))
        self.assertEqual((tags["promoted"], self.aliases()["champion"]), ("true", 2))


if __name__ == "__main__":
    unittest.main()
