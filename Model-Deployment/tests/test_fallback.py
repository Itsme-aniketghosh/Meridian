"""S10's fallback-rate slice report: the #6921 bug, replayed, posts to Slack; a clean refresh, a small slice and an
accepted slice don't; nothing posted carries an email; a broken run never fails the refresh.

    python -m unittest discover -s tests -t . -p "test_fallback.py"     # from Model-Deployment/
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from monitor import fallback

B, ACCEPTED = fallback.settings()


def calls(fell_back):
    """Writer rows: {task: (fell back, texts)}, one text per row, every attempt on gemini-flash."""
    rows = []
    for task, (k, n) in fell_back.items():
        for i in range(n):
            rows.append({"agent": "writer", "facts_hash": f"{task}{i}", "repo": "apache/spark",
                         "team": "SQL", "task": task, "model": "gemini-flash",
                         "source": "template" if i < k else "llm"})
    return pd.DataFrame(rows)


BUG = calls({"stall_reason": (41, 68), "consumer": (0, 68), "ticket_draft": (0, 68)})   # #6921: 41 of 68
CLEAN = calls({"stall_reason": (1, 68), "consumer": (0, 68), "ticket_draft": (2, 68)})


class Recorder:
    def __init__(self):
        self.sent = []

    def __call__(self, subject, lines):
        self.sent.append((subject, lines))
        return ["slack"]


class Fallback(unittest.TestCase):
    def test_the_6921_bug_posts(self):
        rep = fallback.build(BUG, B, ACCEPTED, "c5118b019d642a9b")
        self.assertEqual([x["slice"] for x in rep["open_flags"]], ["task=stall_reason"])
        self.assertEqual((rep["open_flags"][0]["k"], rep["open_flags"][0]["n"]), (41, 68))
        slack = Recorder()
        self.assertEqual(fallback.post(rep, slack), ["slack"])
        subject, lines = slack.sent[0]
        self.assertIn("1 slice", subject)
        self.assertTrue(any(x.startswith("task=stall_reason: 0.603 (41 of 68") for x in lines))

    def test_a_clean_refresh_does_not_post(self):
        rep = fallback.build(CLEAN, B, ACCEPTED)
        self.assertEqual(rep["flags"], [])
        slack = Recorder()
        self.assertEqual(fallback.post(rep, slack), [])
        self.assertEqual(slack.sent, [])

    def test_a_slice_under_min_n_is_listed_not_judged(self):
        """19 of 20 walk texts fell back on Nov 14's live calls: listed, but N < 30 isn't judged (phase 2's rule)."""
        rep = fallback.build(calls({"walk": (19, 20), "consumer": (0, 68), "ticket_draft": (0, 68)}), B)
        g = rep["by"]["task"]["walk"]
        self.assertEqual((g["k"], g["n"], g["judged"]), (19, 20, False))
        self.assertEqual(rep["flags"], [])

    def test_an_accepted_slice_is_reported_but_does_not_post(self):
        rep = fallback.build(BUG, B, ["fallback:task=stall_reason"])
        self.assertEqual(len(rep["flags"]), 1)
        self.assertEqual(rep["open_flags"], [])
        self.assertEqual(fallback.post(rep, Recorder()), [])

    def test_no_texts_judges_nothing(self):
        rep = fallback.build(None, B)
        self.assertEqual(rep["texts"], 0)
        self.assertEqual(rep["flags"], [])
        self.assertIn("no LLM texts", rep["note"])

    def test_a_template_with_no_llm_attempt_is_not_a_fallback(self):
        """A cache miss with the LLM off ships the template with no logged row: no text, not counted."""
        rep = fallback.build(calls({"consumer": (0, 40)}), B)
        self.assertEqual(rep["overall"]["k"], 0)

    def test_nothing_posted_carries_an_email(self):
        bug = BUG.copy()
        bug.loc[bug["task"] == "stall_reason", "task"] = "someone@example.com"
        subject, lines = fallback.message(fallback.build(bug, B))
        self.assertNotIn("@", subject + "".join(lines))
        self.assertIn("[removed]", "".join(lines))

    def test_the_rule_is_model_tomls(self):
        self.assertEqual((B["min_n"], B["max_gap"]), (30, 0.10))

    def test_the_cli_writes_the_report_and_always_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "calls.jsonl"
            BUG.to_json(p, orient="records", lines=True)
            out = Path(tmp) / "fallback.json"
            slack = Recorder()
            self.assertEqual(fallback.main(["--calls", str(p), "--out", str(out)], sender=slack), 0)
            rep = json.loads(out.read_text())
            self.assertEqual(rep["posted"], ["slack"])
            self.assertEqual(len(slack.sent), 1)
            with mock.patch.object(fallback, "write_texts", side_effect=OSError("no block")):
                self.assertEqual(fallback.main(["--db", "missing.sqlite", "--run-id", "x", "--out", str(out)]), 0)
            self.assertIn("no block", json.loads(out.read_text())["error"])


if __name__ == "__main__":
    unittest.main()
