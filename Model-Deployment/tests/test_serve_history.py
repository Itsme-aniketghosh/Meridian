"""deploy.toml [serve_history]: where history.pkl lives, read by the refresh job and deploy.yml, never real names.

    python -m unittest discover -s Model-Deployment/tests -p "test_serve_history.py" -v
"""
import sys
import tomllib
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
import deploy_config  # noqa: E402

DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"


class ServeHistory(unittest.TestCase):
    def setUp(self):
        with open(DEPLOY / "deploy.toml", "rb") as f:
            self.raw = tomllib.load(f)
        self.h = self.raw["serve_history"]

    def test_the_bucket_is_one_of_the_projects(self):
        self.assertIn(self.h["bucket"], self.raw["buckets"])
        self.assertNotEqual(self.h["bucket"], "tfstate")

    def test_the_object_is_under_history_so_a_prefix_grant_covers_it(self):
        self.assertTrue(self.h["object"].startswith("history/"), self.h["object"])
        self.assertNotIn("..", self.h["object"])

    def test_it_resolves_to_a_bucket_name(self):
        cfg = deploy_config.load(use_env=False)
        bucket = deploy_config.get(cfg, f"buckets.{self.h['bucket']}")
        self.assertNotIn("{project}", bucket)
        self.assertTrue(bucket.startswith(cfg["gcp"]["project"]))

    def test_deploy_yml_reads_it_and_names_no_bucket_itself(self):
        text = DEPLOY_YML.read_text(encoding="utf-8")
        self.assertIn("serve_history.bucket", text)
        self.assertIn("serve_history.object", text)
        self.assertNotIn("buckets.block", text)   # the deploy account reads no path in the block bucket of its own
        self.assertNotIn("meridian-block", text)


if __name__ == "__main__":
    unittest.main()
