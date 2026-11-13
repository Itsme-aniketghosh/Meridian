"""Fault 13: A PR from a fork.
Expected: No secrets; stops before the push.
Why: Security of the one key."""
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "model.yml"


class ForkPR(unittest.TestCase):
    """GitHub gives a fork's pull_request run no secrets and no id-token. What's left to check is ours: every step
    that signs in to Google or pushes runs only on main, never for any pull_request, fork or not."""

    def setUp(self):
        self.doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.job = next(j for j in self.doc["jobs"].values() if "ON_MAIN" in j.get("env", {}))

    def test_on_main_is_false_for_every_pull_request(self):
        self.assertIn("github.event_name != 'pull_request'", self.job["env"]["ON_MAIN"])
        self.assertIn("github.ref == 'refs/heads/main'", self.job["env"]["ON_MAIN"])

    def test_google_sign_in_and_the_push_only_run_on_main(self):
        risky = [s for s in self.job["steps"]
                 if "google-github-actions" in s.get("uses", "") or "--push" in s.get("run", "")]
        self.assertGreaterEqual(len(risky), 2, "the auth step and the registry push")
        for s in risky:
            self.assertEqual(s.get("if"), "env.ON_MAIN == 'true'", s.get("name") or s.get("uses"))

    def test_the_only_secret_is_the_slack_webhook(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        secrets = {line.split("secrets.")[1].split("}")[0].strip() for line in text.splitlines() if "secrets." in line}
        self.assertEqual(secrets, {"MERIDIAN_SLACK_WEBHOOK"})


if __name__ == "__main__":
    unittest.main()
