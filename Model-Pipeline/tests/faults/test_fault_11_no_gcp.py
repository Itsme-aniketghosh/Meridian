"""Fault 11: No GCP credentials.
Expected: Everything runs; the push is skipped and says so.
Why: Local work never needs GCP."""
import unittest
from unittest import mock

from tests import test_registry as R


class NoGCP(R.Store):
    def test_registered_and_promoted_locally_push_skipped_and_said(self):
        gcp = R.FakeGCP(signed_in="")  # gcloud installed, no account signed in
        self.s = {**self.s, "project": "p"}
        with mock.patch("model.registry.shutil.which", return_value="/bin/gcloud"):
            tags = self.register(self.files(0.40), push=True, runner=gcp)
        self.assertEqual(tags["promoted"], "true")
        self.assertTrue(self.champion.exists())
        self.assertEqual(gcp.uploads(), [])
        self.assertIn("no gcloud account is signed in", self.said[-1])

    def test_no_gcloud_at_all(self):
        self.s = {**self.s, "project": "p"}
        with mock.patch("model.registry.shutil.which", return_value=None):
            tags = self.register(self.files(0.40), push=True, runner=R.FakeGCP())
        self.assertEqual(tags["promoted"], "true")
        self.assertIn("gcloud isn't installed", self.said[-1])


if __name__ == "__main__":
    unittest.main()
