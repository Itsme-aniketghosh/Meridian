"""Fault 10: Rollback.
Expected: `champion` back to `previous`, locally and in Artifact Registry.
Why: The guideline asks for it."""
import unittest
from unittest import mock

from model import registry as reg
from tests import test_registry as R


class Rollback(R.Store):
    def test_champion_json_the_alias_the_model_file_and_the_gcp_pointer_go_back(self):
        gcp = R.FakeGCP()
        self.s = {**self.s, "project": "p"}
        with mock.patch("model.registry.shutil.which", return_value="/bin/gcloud"):
            self.register(self.files(0.40, model_bytes=b"first"), push=True, runner=gcp, sha="1" * 40)
            first = reg.read_champion(self.champion)
            self.register(self.files(0.45, model_bytes=b"second"), push=True, runner=gcp, sha="2" * 40)
            self.assertEqual(gcp.pointer()["version"], "2" * 40)
            reg.rollback(self.s, self.champion, push=True, runner=gcp, say=self.said.append)
        self.assertEqual(self.aliases(), {"champion": 1, "previous": None})
        self.assertEqual(reg.read_champion(self.champion), first)
        self.assertEqual((self.champion.parent / first["model_file"]).read_bytes(), b"first")
        self.assertEqual(gcp.pointer()["version"], "1" * 40)


if __name__ == "__main__":
    unittest.main()
