"""smoke.py (S3): a revision passes only if it serves the champion deploy.yml checked, and scores like it.

    python -m unittest discover -s Model-Deployment/tests -p "test_smoke.py" -v
"""
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
import smoke as S  # noqa: E402

try:  # the expected risk needs the model's packages (deploy.yml has them)
    import pandas  # noqa: F401
    import sklearn  # noqa: F401
    HAS_MODEL_PACKAGES = True
except ModuleNotFoundError:
    HAS_MODEL_PACKAGES = False

VERSION, MD5, SHA, REPO, RISK = "a" * 40, "b" * 32, "c" * 40, "apache/spark", 0.4946872061692944


class Fake:
    """A server's answers: health and score replies, and how many /health calls fail before it's up."""

    def __init__(self, health=None, score=None, score_status=200, down_for=0):
        model = {"version": VERSION, "md5": MD5}
        self.health = health or {"ok": True, "model": model}
        self.score = score or {"risk": RISK, "model": model}
        self.score_status, self.down_for, self.bodies = score_status, down_for, []

    def handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if fake.down_for > 0:
                    fake.down_for -= 1
                    return self._send(503, {"error": "starting"})
                self._send(200, fake.health)

            def do_POST(self):
                fake.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self._send(fake.score_status, fake.score)

        return H


class Smoke(unittest.TestCase):
    def serve(self, fake):
        server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def check(self, fake, risk=RISK, **kw):
        return S.check(self.serve(fake), VERSION, MD5, SHA, REPO, risk, 1e-9, timeout=5, tries=3, wait=0.01, **kw)

    def test_the_champion_scoring_like_itself_passes(self):
        fake = Fake()
        problems, seen = self.check(fake)
        self.assertEqual(problems, [])
        self.assertEqual(seen["risk"], RISK)
        self.assertEqual(fake.bodies, [{"repo": REPO, "sha": SHA}])

    def test_a_server_still_starting_is_waited_for(self):
        problems, _ = self.check(Fake(down_for=2))
        self.assertEqual(problems, [])

    def test_a_server_that_never_comes_up_fails(self):
        problems, _ = self.check(Fake(down_for=10))
        self.assertTrue(any("/health answered 503" in p for p in problems), problems)

    def test_another_model_fails(self):
        fake = Fake(health={"ok": True, "model": {"version": "d" * 40, "md5": MD5}})
        problems, _ = self.check(fake)
        self.assertTrue(any("/health serves" in p for p in problems), problems)

    def test_another_risk_fails(self):
        problems, _ = self.check(Fake(), risk=RISK + 1e-6)
        self.assertTrue(any("gave risk" in p for p in problems), problems)

    def test_a_score_error_fails(self):
        problems, _ = self.check(Fake(score={"error": "scoring failed"}, score_status=500))
        self.assertTrue(any("answered 500" in p for p in problems), problems)

    def test_nothing_listening_fails_without_a_traceback(self):
        problems, _ = S.check("http://127.0.0.1:9", VERSION, MD5, SHA, REPO, RISK, 1e-9, timeout=1, tries=2,
                              wait=0.01)
        self.assertTrue(problems and "no answer" in problems[0], problems)

    def test_main_writes_the_outputs_and_exits_on_a_failure(self):
        url = self.serve(Fake())
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            out = Path(d) / "out"
            args = ["--url", url, "--version", VERSION, "--md5", MD5, "--sha", SHA, "--wait", "0.01",
                    "--github-output", str(out)]
            self.assertEqual(S.main(args + ["--expect-risk", str(RISK)]), 0)
            self.assertIn("smoke_passed=true", out.read_text())
            self.assertEqual(S.main(args + ["--expect-risk", "0.1"]), 1)
            self.assertIn("smoke_passed=false", out.read_text())


@unittest.skipUnless(HAS_MODEL_PACKAGES, "needs the model's packages (deploy.yml installs them)")
class ExpectedRisk(unittest.TestCase):
    def test_the_committed_champion_gives_the_score_it_was_judged_on(self):
        import pandas as pd
        from serve import scorer
        with open(S.DEPLOY_TOML, "rb") as f:
            sha = S.tomllib.load(f)["smoke"]["sha"]
        model, _, _ = scorer.p9.load_champion(scorer.DEFAULTS["champion"])
        scores = pd.read_csv(scorer.DEFAULTS["val_scores"]).set_index("sha")
        judged = float(scores.loc[sha, scorer.KIND_COLUMN.get(type(model).__name__, "logreg")])
        self.assertAlmostEqual(S.expected_risk(sha, REPO), judged, delta=1e-9)


if __name__ == "__main__":
    unittest.main()
