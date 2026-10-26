"""labels/build_gitlab_gold.py: the scrub, the merge of the two lists, and the raw dump it needs.

    python -m unittest tests.test_gold -v      # from Data-Pipeline/
"""
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

    def test_no_raw_dump_stops(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "out" / "dev").mkdir(parents=True)
        with self.assertRaises(SystemExit):
            G.main(tmp, tmp / "out" / "dev")


if __name__ == "__main__":
    unittest.main()
