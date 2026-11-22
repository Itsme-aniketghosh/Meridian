"""Day 2's bar on real data (S2): /score matches p9.py to 1e-9 on 20 val commits, and a known commit scores as
judged. Runs only where the real block is: Rachna's laptop, or wherever MERIDIAN_BLOCK points.

    # from Model-Deployment/:
    MERIDIAN_BLOCK=../out/<run_id>/meridian.sqlite python -m unittest serve.tests.test_real -v

For 20 seeded val commits that are in the block: the commit as a push (its numstat, subject and hashed author from
the block) scored by the endpoint from the precomputed history state, against p9.push_features re-reading the block,
the push check's own path. And the commit by SHA, against the champion's judged val score.
"""
import os
import random
import sqlite3
import time
import unittest
from pathlib import Path

from serve import scorer as S

BLOCK = os.environ.get("MERIDIAN_BLOCK")
N = 20


@unittest.skipUnless(BLOCK and Path(BLOCK).is_file(), "set MERIDIAN_BLOCK to the real block")
class RealVal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pandas as pd
        t0 = time.perf_counter()
        cls.s = S.Scorer(block=BLOCK, run_id=os.environ.get("MERIDIAN_RUN_ID"))
        cls.start_s = time.perf_counter() - t0
        cls.db = sqlite3.connect(Path(BLOCK).resolve().as_uri() + "?mode=ro", uri=True)
        run_id = cls.s.block["run_id"]
        in_block = {r[0] for r in cls.db.execute(
            "SELECT sha FROM git_commit WHERE run_id = ? AND repo = ? AND first_parent = 1", (run_id, cls.s.repo))}
        scores = pd.read_csv(S.DEFAULTS["val_scores"])
        column = S.KIND_COLUMN.get(type(cls.s.model).__name__, "logreg")
        cls.judged = dict(zip(scores["sha"], scores[column]))
        val = [sha for sha in scores["sha"] if sha in in_block]
        cls.shas = random.Random(1).sample(val, min(N, len(val)))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def as_push(self, sha):
        run_id = self.s.block["run_id"]
        numstat = [[a, d, p] for p, a, d in self.db.execute(
            "SELECT path, added, deleted FROM commit_file WHERE run_id = ? AND repo = ? AND sha = ? ORDER BY path",
            (run_id, self.s.repo, sha))]
        email, subject = self.db.execute("SELECT author_email, subject FROM git_commit WHERE run_id = ? AND repo = ? "
                                         "AND sha = ?", (run_id, self.s.repo, sha)).fetchone()
        return numstat, subject or "", email or ""

    def test_20_val_commits_as_pushes_match_p9(self):
        self.assertEqual(len(self.shas), N)
        worst, times = 0.0, []
        for sha in self.shas:
            numstat, subject, email = self.as_push(sha)
            row = S.p9.push_features(self.db, self.s.repo, self.s.block["run_id"], self.s.block["at"],
                                     [(p, a, d) for a, d, p in numstat], [("push", subject, "")], email,
                                     self.s.key_regex, self.s.prior_fix_days)
            expected = float(self.s.train.score_with(self.s.model, row)[0])
            t0 = time.perf_counter()
            got, _ = self.s.score({"repo": self.s.repo, "numstat": numstat, "subject": subject,
                                   "author_email_hash": S.email_hash(email)})
            times.append(time.perf_counter() - t0)
            worst = max(worst, abs(got["risk"] - expected))
            with self.subTest(sha=sha[:12]):
                self.assertLessEqual(abs(got["risk"] - expected), 1e-9)
        times.sort()
        print(f"\n{len(self.shas)} val commits as pushes: max |endpoint - p9.py| = {worst:.1e}; push scoring "
              f"median {times[len(times) // 2] * 1000:.0f} ms, max {times[-1] * 1000:.0f} ms; "
              f"start {self.start_s:.1f} s")

    def test_20_val_commits_by_sha_score_as_judged(self):
        worst = 0.0
        for sha in self.shas:
            got, _ = self.s.score({"repo": self.s.repo, "sha": sha})
            worst = max(worst, abs(got["risk"] - self.judged[sha]))
            with self.subTest(sha=sha[:12]):
                self.assertLessEqual(abs(got["risk"] - self.judged[sha]), 1e-9)
        print(f"\n{len(self.shas)} val commits by SHA: max |endpoint - judged| = {worst:.1e}")


if __name__ == "__main__":
    unittest.main()
