"""The endpoint (Model-Deployment/serve): the same risk as p9.py, the hashed author, no names out, the HTTP contract D1
and its log line D5. On a small Spark block and a small forest; the real champion is checked on Rachna's laptop.

    python -m unittest serve.tests.test_serve -v        # from Model-Deployment/
"""
import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from serve import app as A
from serve import scorer as S

ROOT = S.ROOT


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TP9 = load("dp_test_p9", ROOT / "Data-Pipeline" / "tests" / "test_p9.py")      # the P9 tests' Spark block
FAKE = None


def fake():
    global FAKE
    if FAKE is None:
        S.p9._model_modules()
        FAKE = load("mp_fake_table", ROOT / "Model-Pipeline" / "tests" / "fake_table.py")
    return FAKE


class Fixture:
    """A champion (small forest), its feature table and val scores, and a Spark block, in a temp folder."""

    def __init__(self):
        import joblib
        import pandas as pd
        self.tmp = tempfile.TemporaryDirectory()
        d = self.d = Path(self.tmp.name)
        _, train = S.p9._model_modules()
        table = fake().fake_table(n=1500, seed=3)
        model = train.fit_and_score(table, "forest", 1, {"n_estimators": 30})[0]
        joblib.dump(model, d / "model.joblib")
        md5 = hashlib.md5((d / "model.joblib").read_bytes()).hexdigest()
        (d / "champion.json").write_text(json.dumps({"version": "abc1234def", "model_file": "model.joblib",
                                                     "model_md5": md5, "metrics": {}, "previous": None}))
        table.to_csv(d / "features.csv", index=False)
        val = table[table["split"] == "val"]
        self.val = val
        self.val_scores = train.score_with(model, val)
        pd.DataFrame({"sha": val["sha"], "label": val["label"], "forest": self.val_scores}).to_csv(
            d / "val_scores.csv", index=False)
        db = TP9.block(d / "meridian.sqlite")
        db.execute("INSERT INTO run (run_id, snapshot_at, repo_shas, source_hashes, config_hash, image_digest, status) "
                   "VALUES (?, ?, '{}', '{}', 'c', 'i', 'complete')", (TP9.RUN, TP9.AT))
        db.commit()
        db.close()
        self.kw = dict(champion=d / "champion.json", features=d / "features.csv", val_scores=d / "val_scores.csv")

    def scorer(self, block=True):
        return S.Scorer(**self.kw, block=self.d / "meridian.sqlite" if block else None)


PUSH = {"repo": "apache/spark", "numstat": [[4, 2, "core/a.py"], [3, 0, "sql/b.py"], [None, None, "img/logo.png"]],
        "subject": "SPARK-1 more", "author_email_hash": S.email_hash("ann@x.org")}


class Scoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()
        cls.s = cls.f.scorer()

    @classmethod
    def tearDownClass(cls):
        cls.f.tmp.cleanup()

    def test_a_known_commit_scores_as_judged(self):
        for sha, judged in list(zip(self.f.val["sha"], self.f.val_scores))[:20]:
            r, _ = self.s.score({"repo": "apache/spark", "sha": sha})
            self.assertLessEqual(abs(r["risk"] - judged), 1e-9)

    def test_a_push_scores_as_p9_does(self):
        """The bar: the same risk as p9.py on its own (re-reading the block), to 1e-9."""
        import sqlite3
        db = sqlite3.connect(self.f.d / "meridian.sqlite")
        try:
            row = S.p9.push_features(db, "apache/spark", TP9.RUN, TP9.AT, [("core/a.py", 4, 2), ("sql/b.py", 3, 0),
                                     ("img/logo.png", None, None)], [("push", "SPARK-1 more", "")], "ann@x.org",
                                     self.s.key_regex, self.s.prior_fix_days)
        finally:
            db.close()
        expected = float(self.s.train.score_with(self.s.model, row)[0])
        r, fields = self.s.score(PUSH)
        self.assertLessEqual(abs(r["risk"] - expected), 1e-9)
        self.assertEqual(fields["features"]["exp"], 2.0)           # the hashed author found: ann's 2 commits

    def test_an_unknown_author_is_new(self):
        r, fields = self.s.score({**PUSH, "author_email_hash": S.email_hash("nobody@x.org")})
        self.assertEqual((fields["features"]["exp"], fields["features"]["sexp"]), (0.0, 0.0))

    def test_response_fields_and_percentile(self):
        r, _ = self.s.score({"repo": "apache/spark", "sha": self.f.val["sha"].iloc[0]})
        self.assertEqual(set(r), {"risk", "percentile_val", "p9", "model", "features_version"})
        self.assertEqual(set(r["p9"]), {"rule", "alert"})
        self.assertEqual(r["p9"]["rule"], "lines_x_files")
        self.assertEqual(r["model"]["version"], "abc1234def")
        self.assertTrue(0 <= r["percentile_val"] <= 1)
        top = max(self.f.val_scores)
        self.assertEqual(self.s.percentile(top), 1.0)
        self.assertEqual(self.s.percentile(-1), 0.0)

    def test_p9_is_lines_x_files_over_the_threshold(self):
        big = {**PUSH, "numstat": [[400, 100, f"core/f{i}.py"] for i in range(5)]}      # 500 x 5 = 2,500
        self.assertTrue(self.s.score(big)[0]["p9"]["alert"])
        self.assertFalse(self.s.score(PUSH)[0]["p9"]["alert"])                          # 9 x 3 = 27

    def test_no_names_out(self):
        """No email, name or person id in any response or log field, on every commit and a push."""
        people = {"ann@x.org", "bob@x.org", "p1", "p2"}
        bodies = [{"repo": "apache/spark", "sha": s} for s in self.f.val["sha"]] + [PUSH]
        for b in bodies:
            r, fields = self.s.score(b)
            text = json.dumps(r) + json.dumps(fields)
            for p in people:
                self.assertNotIn(p, text)
            self.assertNotIn("@", text)
            self.assertNotIn(PUSH["author_email_hash"], text)

    def test_refusals(self):
        cases = {S.WrongRepo: [{"repo": "acme/shop", "sha": "x"}],
                 S.BadRequest: [[], {"sha": "x"}, {"repo": "apache/spark"}, {"repo": "apache/spark", "sha": "nope"},
                                {**PUSH, "numstat": []}, {**PUSH, "numstat": [[1, 2]]},
                                {**PUSH, "numstat": [[-1, 0, "a.py"]]}, {**PUSH, "author_email_hash": "ann@x.org"},
                                {**PUSH, "subject": 7}, {**PUSH, "sha": "x"},
                                {**PUSH, "numstat": [[S.MAX_LINES + 1, 0, "a.py"]]},
                                {**PUSH, "numstat": [[1, 0, "a" * (S.MAX_PATH + 1)]]}]}
        for exc, bodies in cases.items():
            for b in bodies:
                with self.subTest(body=b), self.assertRaises(exc):
                    self.s.score(b)

    def test_no_block_scores_known_shas_only(self):
        s = self.f.scorer(block=False)
        self.assertIn("risk", s.score({"repo": "apache/spark", "sha": self.f.val["sha"].iloc[0]})[0])
        with self.assertRaisesRegex(S.BadRequest, "known SHAs only"):
            s.score(PUSH)

    def test_a_changed_model_file_wont_start(self):
        with tempfile.TemporaryDirectory() as d:
            c = json.loads((self.f.d / "champion.json").read_text())
            (Path(d) / "model.joblib").write_bytes(b"not the model")
            (Path(d) / "champion.json").write_text(json.dumps(c))
            with self.assertRaisesRegex(SystemExit, "md5"):
                S.Scorer(**{**self.f.kw, "champion": Path(d) / "champion.json"})

    def test_a_feature_table_the_champion_wasnt_judged_on_wont_start(self):
        with tempfile.TemporaryDirectory() as d:
            c = json.loads((self.f.d / "champion.json").read_text())
            c["metrics"] = {"features_md5": "0" * 32}
            (Path(d) / "model.joblib").write_bytes((self.f.d / "model.joblib").read_bytes())
            (Path(d) / "champion.json").write_text(json.dumps(c))
            with self.assertRaisesRegex(SystemExit, "judged on"):
                S.Scorer(**{**self.f.kw, "champion": Path(d) / "champion.json"})


class Http(unittest.TestCase):
    """The contract over real HTTP, and one D5 line per call."""

    @classmethod
    def setUpClass(cls):
        cls.f = Fixture()
        cls.server = A.ThreadingHTTPServer(("127.0.0.1", 0), A.make_handler(cls.f.scorer()))
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.f.tmp.cleanup()

    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    status, payload = r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                status, payload = e.code, json.loads(e.read())
        return status, payload, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]

    def test_health(self):
        status, payload, logs = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual([(x["event"], x["status"]) for x in logs], [("health", 200)])
        self.assertEqual(payload, {"ok": True, "model": {"version": "abc1234def", "md5": self.f.scorer(False).md5}})

    def test_score_and_its_log_line(self):
        status, payload, logs = self.call("POST", "/score", PUSH)
        self.assertEqual(status, 200)
        self.assertIn("risk", payload)
        self.assertEqual(len(logs), 1)
        line = logs[0]
        self.assertEqual((line["event"], line["status"], line["model_version"], line["kind"]),
                         ("score", 200, "abc1234def", "push"))
        self.assertIsInstance(line["latency_ms"], int)
        self.assertEqual(line["risk"], payload["risk"])
        self.assertNotIn("@", json.dumps(line))
        self.assertNotIn(PUSH["author_email_hash"], json.dumps(line))

    def test_status_codes(self):
        self.assertEqual(self.call("POST", "/score", {"repo": "acme/shop", "sha": "x"})[0], 422)
        self.assertEqual(self.call("POST", "/score", raw=b"{not json")[0], 400)
        self.assertEqual(self.call("POST", "/score", {"repo": "apache/spark"})[0], 400)
        self.assertEqual(self.call("POST", "/score", raw=b" " * (A.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        status, payload, logs = self.call("POST", "/score", {"repo": "apache/spark"})
        self.assertEqual((logs[0]["status"], set(payload)), (400, {"error"}))

    def test_a_negative_content_length_is_refused_at_once(self):
        host, port = self.server.server_address
        conn = http.client.HTTPConnection(host, port, timeout=10)    # a hang would read to EOF: the timeout catches it
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                conn.putrequest("POST", "/score")
                conn.putheader("Content-Length", "-1")
                conn.endheaders()
                self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
