"""The serve image's files (S2): the build-time champion check, and a Dockerfile and allowlist that agree.

    python -m unittest serve.tests.test_image -v      # from Model-Deployment/
"""
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from serve import buildcheck as B

SERVE = Path(__file__).resolve().parents[1]
ROOT = SERVE.parents[1]


def registry(d, version="v1", content=b"model bytes", recorded=None):
    (d / "model.joblib").write_bytes(content)
    md5 = recorded or hashlib.md5(content).hexdigest()
    (d / "champion.json").write_text(json.dumps({"version": version, "model_file": "model.joblib", "model_md5": md5}))
    return hashlib.md5(content).hexdigest()


class BuildCheck(unittest.TestCase):
    def test_the_champion_deploy_checked_builds(self):
        with tempfile.TemporaryDirectory() as d:
            md5 = registry(Path(d))
            self.assertEqual(B.check("v1", md5, d)[1], [])
            self.assertEqual(B.check("", "", d)[1], [])          # a local build without args: the file still checked

    def test_another_version_or_md5_or_a_changed_file_wont(self):
        with tempfile.TemporaryDirectory() as d:
            md5 = registry(Path(d))
            self.assertIn("version", B.check("v2", md5, d)[1][0])
            self.assertIn("records md5", B.check("v1", "0" * 32, d)[1][0])
        with tempfile.TemporaryDirectory() as d:
            registry(Path(d), recorded="f" * 32)                 # the file isn't what champion.json records
            self.assertTrue(any("model.joblib has md5" in p for p in B.check("v1", "f" * 32, d)[1]))

    def test_the_committed_champion_passes(self):
        record = json.loads((ROOT / "Model-Pipeline" / "registry" / "champion.json").read_text())
        self.assertEqual(B.check(record["version"], record["model_md5"])[1], [])


class Files(unittest.TestCase):
    """Every COPY source is allowed into the build, and every allowed file exists (history.pkl is built, optional)."""

    @classmethod
    def setUpClass(cls):
        dockerfile = (SERVE / "Dockerfile").read_text()
        cls.copies = []
        for line in dockerfile.splitlines():
            if line.startswith("COPY "):
                cls.copies += line.split()[1:-1]
        cls.allowed = [line[1:] for line in (SERVE / "Dockerfile.dockerignore").read_text().splitlines()
                       if line.startswith("!")]

    def is_allowed(self, path):
        return any(Path(path).match(pattern) or path == pattern for pattern in self.allowed)

    def test_every_copied_file_is_allowed(self):
        for src in self.copies:
            with self.subTest(src=src):
                if src.endswith("/"):                             # a directory: something in it must be allowed
                    self.assertTrue(any(p.startswith(src) for p in self.allowed), src)
                else:
                    self.assertTrue(self.is_allowed(src), src)

    def test_every_allowed_file_exists(self):
        for pattern in self.allowed:
            if pattern.endswith("history.pkl"):
                continue
            with self.subTest(pattern=pattern):
                self.assertTrue(list(ROOT.glob(pattern)), pattern)

    def test_no_block_or_data_goes_in(self):
        for pattern in self.allowed:
            self.assertNotRegex(pattern, r"\.sqlite|out/|snapshots/|published/")

    def test_pins_are_the_repos_own(self):
        pins = dict(re.findall(r"^([a-z][a-z0-9_-]*)==([^\s#]+)", (SERVE / "requirements.txt").read_text(), re.M))
        repo = {}
        for f in ("Data-Pipeline/requirements.txt", "Model-Pipeline/requirements.txt"):
            repo.update(re.findall(r"^([a-z][a-z0-9_-]*)==([^\s#]+)", (ROOT / f).read_text(), re.M))
        for name, version in pins.items():
            with self.subTest(name=name):
                self.assertEqual(repo.get(name), version)


if __name__ == "__main__":
    unittest.main()
