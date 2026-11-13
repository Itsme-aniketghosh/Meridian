"""Fault 12: Slack webhook broken.
Expected: Gate still decides; the error is logged.
Why: An alert channel must never break the run it reports on."""
import contextlib
import io
import os
import unittest
from unittest import mock

from model import gate
from tests.faults._gate import GateFault

DEAD = {"MERIDIAN_SLACK_WEBHOOK": "http://127.0.0.1:9/hook"}  # port 9: nothing listens


class SlackDown(GateFault):
    def decide(self):
        self.write()
        (self.tmp / "s.toml").write_text('[alerts]\non = ["failure"]\ntimeout_s = 2\n', encoding="utf-8")
        env = {**DEAD, "MERIDIAN_SETTINGS": str(self.tmp / "s.toml")}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()), \
                self.assertLogs("meridian", level="ERROR") as logs:
            code, result = gate.run(self.tmp, self.tmp / "champion.json", self.tmp / "model.toml")
        return code, result, logs.output

    def test_a_failing_model_still_fails_and_the_send_error_is_logged(self):
        self.docs["bias_report.json"]["pass"] = False
        live = self.gate()[0]                 # Slack up
        code, result, logs = self.decide()    # Slack down
        self.assertEqual((live, code), (1, 1))
        self.assertEqual(result["alerted"], [])
        self.assertTrue(any("Slack failed" in line for line in logs))

    def test_a_passing_model_still_passes(self):
        self.write()
        with mock.patch.dict(os.environ, DEAD), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.run(self.tmp, self.tmp / "champion.json", self.tmp / "model.toml")[0], 0)


if __name__ == "__main__":
    unittest.main()
