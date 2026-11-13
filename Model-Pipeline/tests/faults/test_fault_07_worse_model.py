"""Fault 07: A model worse than the champion.
Expected: Registered, not promoted, not pushed.
Why: Rollback starts with not promoting."""
import unittest
from unittest import mock

from model import registry as reg
from tests import test_registry as R


class WorseModel(R.Store):
    def test_registered_not_promoted_and_gcp_still_holds_the_champion(self):
        gcp = R.FakeGCP()
        self.s = {**self.s, "project": "p"}
        with mock.patch("model.registry.shutil.which", return_value="/bin/gcloud"):
            self.register(self.files(0.40, model_bytes=b"the champion"), push=True, runner=gcp, sha="1" * 40)
            tags = self.register(self.files(0.30), push=True, runner=gcp, sha="2" * 40)
        self.assertEqual((tags["version"], tags["promoted"]), (2, "false"))
        self.assertIn("no better than the champion", tags["reason"])
        self.assertEqual(self.aliases()["champion"], 1)
        self.assertNotIn(("defect-risk", "2" * 40), gcp.held)
        self.assertEqual(gcp.pointer()["version"], "1" * 40)
        self.assertEqual(reg.read_champion(self.champion)["version"], "1" * 40)


if __name__ == "__main__":
    unittest.main()
