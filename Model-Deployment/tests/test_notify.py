"""notify.py (S7): deploy.yml's Slack messages, sent for real to a local Slack stand-in through meridian.alert.

    python -m unittest discover -s Model-Deployment/tests -p "test_notify.py" -v
"""
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
import notify as N  # noqa: E402

# Data-Pipeline's local Slack stand-in, loaded by path: its folder on sys.path would shadow tests named like ours
_spec = importlib.util.spec_from_file_location("alert_sinks", REPO / "Data-Pipeline" / "tests" / "alert_sinks.py")
_sinks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sinks)
sinks = _sinks.sinks

DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"
V, MD5 = "85b4684ae33697e771ba4defa5cc4cb5618bcd6a", "2a0c9cc656b43fc4e471c70223c4b4ca"
RUN = "https://github.com/x/y/actions/runs/1"
URL = "https://meridian-risk-abc-ue.a.run.app"


class Notify(unittest.TestCase):
    def run_main(self, args, webhook=None):
        """(exit code, stdout, the slack_sent output line) with MERIDIAN_SLACK_WEBHOOK set to webhook, or unset."""
        env = {k: v for k, v in os.environ.items() if k != "MERIDIAN_SLACK_WEBHOOK"}
        if webhook:
            env["MERIDIAN_SLACK_WEBHOOK"] = webhook
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, env, clear=True):
            out, gh = io.StringIO(), Path(d) / "out"
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = N.main(args + ["--run-url", RUN, "--github-output", str(gh)])
            return code, out.getvalue(), gh.read_text()

    def test_deployed_reaches_slack_with_the_model_and_the_url(self):
        with sinks() as s:
            code, _, gh = self.run_main(["deployed", "--version", V, "--md5", MD5, "--pr-auc", "0.4067",
                                         "--revision", "meridian-risk-00003-biw", "--url", URL], s.webhook)
            self.assertEqual((code, gh), (0, "slack_sent=slack\n"))
            [body] = s.slack
        text = body["text"]
        self.assertTrue(text.startswith("Meridian: new model deployed"), text)
        for want in (V[:12], MD5, "0.4067", "meridian-risk-00003-biw serves 100%", URL, RUN):
            self.assertIn(want, text)

    def test_rolled_back_says_what_failed_and_what_serves(self):
        with sinks() as s:
            self.run_main(["rolled_back", "--version", V, "--revision", "meridian-risk-00004-bad",
                           "--serving", "meridian-risk-00003-biw", "--why", "the live smoke test", "--health", "200"],
                          s.webhook)
            text = s.slack[0]["text"]
        self.assertIn("deploy rolled back", text)
        self.assertIn("meridian-risk-00004-bad failed after the traffic move: the live smoke test", text)
        self.assertIn("meridian-risk-00003-biw serves 100% again (/health 200)", text)

    def test_canary_failed_says_its_share_why_and_what_serves(self):
        with sinks() as s:
            self.run_main(["canary_failed", "--version", V, "--revision", "meridian-risk-00004-new", "--percent", "10",
                           "--serving", "meridian-risk-00003-biw", "--why", "error rate 4/198 = 2.02%, over 1.00%",
                           "--health", "200"], s.webhook)
            text = s.slack[0]["text"]
        self.assertTrue(text.startswith("Meridian: canary failed, rolled back"), text)
        self.assertIn("meridian-risk-00004-new took 10% of traffic and failed: error rate 4/198", text)
        self.assertIn("meridian-risk-00003-biw serves 100% again (/health 200)", text)

    def test_stopped_and_rollback_failed(self):
        with sinks() as s:
            self.run_main(["stopped", "--version", V, "--step", "Smoke test, the revision (main)",
                           "--serving", "meridian-risk-00003-biw"], s.webhook)
            self.run_main(["rollback_failed", "--version", V, "--revision", "r2", "--serving", "r1"], s.webhook)
            stopped, failed = (b["text"] for b in s.slack)
        self.assertIn("no traffic moved", stopped)
        self.assertIn("failed at: Smoke test, the revision (main)", stopped)
        self.assertIn("meridian-risk-00003-biw keeps serving", stopped)
        self.assertIn("ROLLBACK FAILED, move the traffic by hand", failed)

    def test_no_webhook_is_logged_only_and_passes(self):
        code, out, gh = self.run_main(["deployed", "--version", V, "--url", URL])
        self.assertEqual((code, gh), (0, "slack_sent=none\n"))
        self.assertIn("no channel (logged only)", out)

    def test_a_broken_channel_never_fails_the_deploy(self):
        code, _, gh = self.run_main(["rolled_back", "--version", V], "http://127.0.0.1:9/nothing")
        self.assertEqual((code, gh), (0, "slack_sent=none\n"))
        code, _, gh = self.run_main(["deployed", "--version", V], "not a url")
        self.assertEqual((code, gh), (0, "slack_sent=none\n"))

    def test_an_alert_module_that_raises_never_fails_the_deploy(self):
        with mock.patch("meridian.alert.send", side_effect=RuntimeError("boom")), redirect_stderr(io.StringIO()):
            self.assertEqual(N.send("s", [], "failure"), [])

    def test_no_people_out(self):
        with sinks() as s:
            self.run_main(["stopped", "--version", V, "--step", "a check by someone@example.com"], s.webhook)
            text = s.slack[0]["text"]
        self.assertNotIn("@", text)
        self.assertIn(V[:12], text)

    def test_deploy_yml_sends_on_main_only_and_never_fails_on_it(self):
        text = DEPLOY_YML.read_text(encoding="utf-8")
        step = text[text.index("- name: Slack"):text.index("- name: Summary")]
        self.assertIn("Model-Deployment/notify.py", step)
        self.assertIn("env.ON_MAIN == 'true'", step)
        self.assertIn("continue-on-error: true", step)
        self.assertIn("secrets.MERIDIAN_SLACK_WEBHOOK", step)
        self.assertNotIn("secrets.MERIDIAN_SLACK_WEBHOOK", text.replace(step, ""))   # no other step sees it


if __name__ == "__main__":
    unittest.main()
