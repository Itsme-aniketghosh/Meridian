"""S2's no-names bar on real data: no email, name or person id in any /score response or D5 log line, over 1,000
scored commits. Runs where the real block is (Rachna's laptop); writes the evidence to reports/serve_no_names.json.

    # from Model-Deployment/:
    MERIDIAN_BLOCK=../out/<run_id>/meridian.sqlite python -m unittest serve.tests.test_no_names -v

The 1,000: 500 commits by SHA (the feature table's, seeded) and 500 as pushes (a block commit's real numstat,
subject and hashed author), all over real HTTP through the endpoint's own handler, the way Cloud Run serves it. Every
response body and log line is checked against every identifier the block holds: authors' and committers' emails,
person ids, names (4 letters or more, as whole words), and the hashes the requests sent.
"""
import contextlib
import io
import json
import os
import random
import re
import sqlite3
import threading
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from serve import app as A
from serve import scorer as S

BLOCK = os.environ.get("MERIDIAN_BLOCK")
REPORT = Path(__file__).resolve().parents[2] / "reports" / "serve_no_names.json"
N_SHA, N_PUSH = 500, 500
MIN_ID = 6       # an identifier shorter than this, without an @, can't name anyone and would match any line


class Capture(io.TextIOBase):
    """stdout for the server's thread: every D5 line, kept."""

    def __init__(self):
        self.lines, self.lock = [], threading.Lock()

    def write(self, s):
        with self.lock:
            self.lines.append(s)
        return len(s)


@unittest.skipUnless(BLOCK and Path(BLOCK).is_file(), "set MERIDIAN_BLOCK to the real block")
class NoNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pandas as pd
        env = os.environ.get
        cls.s = S.Scorer(champion=env("MERIDIAN_CHAMPION"), features=env("MERIDIAN_FEATURES"),
                         val_scores=env("MERIDIAN_VAL_SCORES"), block=BLOCK, run_id=env("MERIDIAN_RUN_ID"))
        run_id = cls.s.block["run_id"]
        db = sqlite3.connect(Path(BLOCK).resolve().as_uri() + "?mode=ro", uri=True)
        q = lambda sql, *p: [r[0] for r in db.execute(sql, (run_id, *p)) if r[0]]
        emails = set(q("SELECT author_email FROM git_commit WHERE run_id = ?")) | \
            set(q("SELECT committer_email FROM git_commit WHERE run_id = ?")) | \
            set(q("SELECT email FROM person_alias WHERE run_id = ?"))
        # A value like "x" (a junk author email in git history) would match every line: checked only if it could
        # name someone, i.e. has an @ or is 6+ characters. The junk ones are counted in the report, not hidden.
        cls.emails = {e for e in emails if "@" in e or len(e) >= MIN_ID}
        cls.junk_emails = sorted(emails - cls.emails)
        cls.person_ids = {p for p in q("SELECT person_id FROM person WHERE run_id = ?") if len(p) >= MIN_ID}
        names = set(q("SELECT name FROM person WHERE run_id = ?")) | \
            set(q("SELECT name FROM person_alias WHERE run_id = ?"))
        cls.name_words = {w.lower() for n in names for w in re.findall(r"[^\W\d_]{4,}", n)}
        rng = random.Random(1)
        f = Path(os.environ.get("MERIDIAN_FEATURES") or S.DEFAULTS["features"])
        table = pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f)
        cls.shas = rng.sample(sorted(table["sha"]), N_SHA)
        commits = list(db.execute("SELECT sha, author_email, subject FROM git_commit "
                                  "WHERE run_id = ? AND repo = ? AND first_parent = 1", (run_id, cls.s.repo)))
        cls.pushes = []
        for sha, email, subject in rng.sample(commits, min(len(commits), N_PUSH * 2)):
            numstat = [[a, d, p] for p, a, d in db.execute(
                "SELECT path, added, deleted FROM commit_file WHERE run_id = ? AND repo = ? AND sha = ?",
                (run_id, cls.s.repo, sha))]
            if numstat:
                cls.pushes.append({"repo": cls.s.repo, "numstat": numstat, "subject": subject or "",
                                   "author_email_hash": S.email_hash(email)})
            if len(cls.pushes) == N_PUSH:
                break
        db.close()
        cls.server = A.ThreadingHTTPServer(("127.0.0.1", 0), A.make_handler(cls.s))
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/score"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, body):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")

    def hits(self, text, hashes):
        low = text.lower()
        found = []
        if "@" in text:
            found.append("an @")
        found += [f"email {e}" for e in self.emails if e.lower() in low]
        found += [f"person id {p}" for p in self.person_ids if p in text]
        words = set(re.findall(r"[^\W\d_]{4,}", low))
        found += [f"name word {w}" for w in self.name_words & words]
        found += [f"request hash {h[:12]}" for h in hashes if h in text]
        return found

    def test_1000_scored_commits_name_nobody(self):
        bodies = [{"repo": self.s.repo, "sha": sha} for sha in self.shas] + self.pushes
        self.assertEqual(len(bodies), N_SHA + N_PUSH)
        cap = Capture()
        responses = []
        with contextlib.redirect_stdout(cap):
            for b in bodies:
                status, text = self.post(b)
                self.assertEqual(status, 200, text[:200])
                responses.append(text)
        logs = [line for line in "".join(cap.lines).splitlines() if line.strip()]
        self.assertEqual(sum(json.loads(line).get("event") == "score" for line in logs), len(bodies))
        hashes = {b["author_email_hash"] for b in self.pushes}
        # names that are ordinary words in the response's own keys can't count as leaks: there are none of those
        fixed = set(re.findall(r"[^\W\d_]{4,}", " ".join(responses[:1] + logs[:1]).lower()))
        excluded = sorted(self.name_words & fixed)                # e.g. a person called "Rule" or "Push"
        self.name_words -= fixed
        found = []
        for text in responses + logs:
            found += self.hits(text, hashes)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({
            "bar": "no email, name or person id in any /score response or log line, over 1,000 scored commits",
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "block_run_id": self.s.block["run_id"], "model_version": self.s.version, "model_md5": self.s.md5,
            "scored": {"by_sha": N_SHA, "as_push": len(self.pushes)}, "responses": len(responses),
            "log_lines": len(logs),
            "identifiers_checked": {"emails": len(self.emails), "person_ids": len(self.person_ids),
                                    "name_words": len(self.name_words), "request_hashes": len(hashes)},
            "junk_emails_not_checked": {"count": len(self.junk_emails),
                                        "why": f"no @ and under {MIN_ID} characters: names nobody, matches any line",
                                        "values": self.junk_emails[:20]},
            "name_words_excluded_as_the_responses_own_keys": excluded,
            "hits": found[:50], "passed": not found}, indent=1) + "\n", encoding="utf-8")
        print(f"\n{len(responses)} responses and {len(logs)} log lines against {len(self.emails):,} emails, "
              f"{len(self.person_ids):,} person ids, {len(self.name_words):,} name words: {len(found)} hits; {REPORT}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
