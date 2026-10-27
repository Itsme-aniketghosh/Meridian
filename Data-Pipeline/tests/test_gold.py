"""labels/build_gitlab_gold.py: the scrub, the merge of the two lists, and the raw dump it needs.

    python -m unittest tests.test_gold -v      # from Data-Pipeline/
"""
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "labels"))
import build_gitlab_gold as G


class Gold(unittest.TestCase):
    def test_every_username_is_scrubbed_any_case_words_too(self):
        scrub = G.scrubber({"design", "jdoe99", "bob"})
        self.assertEqual(scrub("Design said JDOE99 and bob, cc @jdoe99 a@b.org"),
                         "user said user and bob, cc @user [email]")

    def test_put_keeps_one_source_unless_both_lists_have_the_pair(self):
        row = lambda source, verdict: {"blocked_ref": "g#1", "blocker_ref": "g#2", "verdict": verdict,
                                       "source": source, "q": ""}
        rows = {}
        G.put(rows, row("link_pass", "unclear"))
        G.put(rows, row("link_pass", "wait"))
        self.assertEqual((rows["g#1", "g#2"]["source"], rows["g#1", "g#2"]["verdict"]), ("link_pass", "wait"))
        G.put(rows, row("targeted_read", "no"))
        self.assertEqual((rows["g#1", "g#2"]["source"], rows["g#1", "g#2"]["verdict"]), ("both", "wait"))

    def test_spot_check_answers_are_verdicts_with_a_checker(self):
        rows = G.read_csv(G.HERE / "gitlab_cross_team_blocks.csv")
        pairs = [(r["blocked_ref"], r["blocker_ref"]) for r in rows]
        self.assertEqual(len(pairs), len(set(pairs)))  # a rebuild matches answers back on the pair
        checked = [r for r in rows if r["human_verdict"]]
        self.assertGreaterEqual(len(checked), 50)  # the team task's sample: random.seed(1), 50 rows
        for r in checked:
            self.assertIn(r["human_verdict"], G.RANK, r["blocked_ref"])
            self.assertNotIn(r["checked_by"], ("", "model"), r["blocked_ref"])

    def test_spot_check_is_the_task_lists_random_sample(self):
        # The team task's draw: random.seed(1), then random.sample(rows, 50) over the file in order. If rows are added
        # to the CSV the draw changes, and the 50 answered rows have to be drawn and checked again.
        rows = G.read_csv(G.HERE / "gitlab_cross_team_blocks.csv")
        sample = {(r["blocked_ref"], r["blocker_ref"]) for r in random.Random(1).sample(rows, 50)}
        checked = {(r["blocked_ref"], r["blocker_ref"]) for r in rows if r["human_verdict"]}
        self.assertEqual(checked, sample)

    def test_rebuild_keeps_human_verdict_and_checked_by(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "old.csv").write_text("blocked_ref,blocker_ref,checked_by,human_verdict\ng#1,g#2,ZQ,wait\n",
                                     encoding="utf-8")
        row = lambda blocked: {"blocked_ref": blocked, "blocker_ref": "g#2", "checked_by": "model", "human_verdict": ""}
        blocks = [row("g#1"), row("g#3")]
        G.keep_spot_check(blocks, tmp / "old.csv")
        self.assertEqual([(r["checked_by"], r["human_verdict"]) for r in blocks], [("ZQ", "wait"), ("model", "")])

    def test_no_raw_dump_stops(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "out" / "dev").mkdir(parents=True)
        with self.assertRaises(SystemExit):
            G.main(tmp, tmp / "out" / "dev")


if __name__ == "__main__":
    unittest.main()
