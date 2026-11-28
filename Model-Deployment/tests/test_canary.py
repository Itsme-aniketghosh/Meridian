"""canary.py (S3, Nov 27): the new revision's share of traffic, judged on errors and p95 latency before 100%.

A fake service stands in for Cloud Run: the service URL answers from the new model one call in ten (the split), the
tag URL always from the new one, and a fake clock makes 180 s of watching instant.

    python -m unittest discover -s Model-Deployment/tests -p "test_canary.py" -v
"""
import io
import sys
import tempfile
import tomllib
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
import canary as C  # noqa: E402

NEW, OLD = "85b4684ae33697e771ba4defa5cc4cb5618bcd6a", "70dd0000000000000000000000000000000000aa"
SERVICE, TAG = "https://meridian-risk-abc-ue.a.run.app", "https://c-85b4684ae336---meridian-risk-abc-ue.a.run.app"


class FakeService:
    """Cloud Run with the new revision at `share` of 10 calls; `canary` answers (status, ms) for the new revision."""
    def __init__(self, canary=lambda n: (200, 130), share=1, old_version=OLD):
        self.canary, self.share, self.old, self.n, self.t = canary, share, old_version, 0, 0.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.001)

    def __call__(self, url, path, body=None, timeout=60):
        if path == "/health":
            return 200, {"ok": True, "model": {"version": self.old, "md5": "x"}}, 0.05
        self.n += 1
        if url == SERVICE and self.n % 20 >= self.share * 2:     # the old revision's share of the service URL
            return 200, {"risk": 0.49, "model": {"version": self.old, "md5": "x"}}, 0.12
        status, ms = self.canary(self.n)
        if status is None:
            raise urllib.error.URLError("timed out")
        reply = {"risk": 0.49, "model": {"version": NEW, "md5": "y"}} if status == 200 else {"error": "boom"}
        return status, reply, ms / 1000


class Canary(unittest.TestCase):
    def run_main(self, fake, *extra):
        with tempfile.TemporaryDirectory() as d:
            gh = Path(d) / "out"
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = C.main(["--url", SERVICE, "--tag-url", TAG, "--version", NEW, "--github-output", str(gh),
                               *extra], caller=fake, clock=fake.clock, sleep=fake.sleep)
            lines = dict(x.split("=", 1) for x in gh.read_text().splitlines())
        return code, lines, out.getvalue() + err.getvalue()

    def test_a_healthy_canary_passes_and_the_split_is_seen(self):
        code, out, text = self.run_main(FakeService())
        self.assertEqual(code, 0, text)
        self.assertEqual(out["canary_passed"], "true")
        self.assertEqual(out["canary_errors"], "0")
        self.assertEqual(out["canary_p95_ms"], "130.0")
        self.assertGreater(int(out["canary_new_via_service"]), 0)
        self.assertLess(int(out["canary_new_via_service"]), int(out["canary_via_service"]))   # a share, not all
        self.assertGreaterEqual(int(out["canary_calls"]), 300)   # 180 s at 1 tick a second, 2 calls a tick

    def test_errors_over_one_percent_fail(self):
        code, out, text = self.run_main(FakeService(canary=lambda n: (500, 100) if n % 25 == 0 else (200, 130)))
        self.assertEqual(code, 1)
        self.assertIn("error rate", out["canary_why"])
        self.assertIn("canary: failed", text)

    def test_calls_that_never_answer_count_as_errors(self):
        code, out, _ = self.run_main(FakeService(canary=lambda n: (None, 0) if n % 10 == 0 else (200, 130)))
        self.assertEqual(code, 1)
        self.assertIn("error rate", out["canary_why"])

    def test_a_slow_canary_fails_on_p95(self):
        code, out, _ = self.run_main(FakeService(canary=lambda n: (200, 2600) if n % 10 < 2 else (200, 130)))
        self.assertEqual(code, 1)
        self.assertIn("p95 latency 2600.0 ms, over 2000.0 ms", out["canary_why"])

    def test_one_cold_start_doesnt_fail_it(self):
        code, out, text = self.run_main(FakeService(canary=lambda n: (200, 14400) if n == 2 else (200, 130)))
        self.assertEqual(code, 0, text)

    def test_a_split_that_never_reaches_the_new_revision_fails(self):
        code, out, _ = self.run_main(FakeService(share=0))
        self.assertEqual(code, 1)
        self.assertIn("none of", out["canary_why"])

    def test_the_same_model_on_both_revisions_skips_the_split_check(self):
        code, out, text = self.run_main(FakeService(share=0, old_version=NEW))
        self.assertEqual(code, 0, text)
        self.assertIn("can't be told apart", text)

    def test_a_reply_from_another_model_fails(self):
        fake = FakeService()
        real = fake.__call__

        def other(url, path, body=None, timeout=60):
            status, reply, s = real(url, path, body, timeout)
            if url == TAG and fake.n == 6:
                reply = {"risk": 0.1, "model": {"version": "f" * 40}}
            return status, reply, s
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            code = C.main(["--url", SERVICE, "--tag-url", TAG, "--version", NEW, "--seconds", "20"],
                          caller=other, clock=fake.clock, sleep=fake.sleep)
        self.assertEqual(code, 1)
        self.assertIn("neither the new one nor", err.getvalue())

    def test_p95_is_nearest_rank(self):
        self.assertEqual(C.p95(list(range(1, 101))), 95)
        self.assertEqual(C.p95([7]), 7)
        self.assertIsNone(C.p95([]))

    def test_deploy_toml_holds_plans_bars(self):
        with open(DEPLOY / "deploy.toml", "rb") as f:
            cfg = tomllib.load(f)["canary"]
        self.assertEqual(cfg["percent"], 10)
        self.assertLessEqual(cfg["max_error_rate"], 0.01)
        self.assertLessEqual(cfg["max_p95_ms"], 2000)
        self.assertGreaterEqual(cfg["watch_s"], 60)


class DeployYml(unittest.TestCase):
    text = (REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    def step(self, name, until):
        return self.text[self.text.index(f"- name: {name}"):self.text.index(f"- name: {until}")]

    def test_the_canary_comes_after_the_revisions_smoke_test_and_before_100_percent(self):
        order = [self.text.index(f"- name: {n}") for n in
                 ("Smoke test, the revision (main)", "Canary, a share", "Move traffic to the new revision (main)")]
        self.assertEqual(order, sorted(order))

    def test_the_canary_splits_by_name_and_marks_the_move_before_it(self):
        step = self.step("Canary, a share", "Move traffic to the new revision (main)")
        self.assertIn('--to-revisions "$REVISION=$PERCENT,$PREVIOUS=$(( 100 - PERCENT ))"', step)
        self.assertLess(step.index('echo "moved=true"'), step.index("update-traffic"))
        self.assertIn("Model-Deployment/canary.py", step)
        self.assertIn("history.outputs.present", step)

    def test_a_failed_canary_rolls_back_and_says_so_in_slack(self):
        rollback = self.step("Roll back to the revision that served before (main)", "Slack (main)")
        self.assertIn("steps.canary.outputs.moved == 'true'", rollback)
        self.assertIn("steps.canary.outcome == 'failure'", rollback)
        self.assertIn("notify.py canary_failed", self.step("Slack (main)", "Summary"))

    def test_an_automatic_run_on_a_champion_that_serves_says_so(self):
        self.assertIn("no message: the champion already serves", self.step("Slack (main)", "Summary"))


if __name__ == "__main__":
    unittest.main()
