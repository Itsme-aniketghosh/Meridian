"""meridian/context.py (GAPS.md gap 16, decision 12): the riskiest files kept to 90% of bug fixes, the rest counted."""
import copy
import unittest

import tests.test_kb as K
from meridian import context as C
from meridian.common import CONFIG, load_config


def f(path, sites, bug_fixes):
    return {"path": path, "sites": sites, "bug_fixes": bug_fixes, "fix_keys": []}


def facts(*files):
    return {"team": "t", "left": sum(x["sites"] for x in files), "symbol": "s", "files": list(files),
            "blocked_by": [], "blocking": []}


class Cut(unittest.TestCase):
    def test_keeps_the_riskiest_until_90_percent(self):
        out = C.cut(facts(f("a", 1, 1), f("b", 5, 8), f("c", 2, 1)))
        # 8 of 10, then 9 of 10 = 90%; c before a on the tie, by sites (2 against 1)
        self.assertEqual([x["path"] for x in out["files"]], ["b", "c"])
        self.assertEqual((out["files_cut"], out["sites_cut"]), (1, 1))

    def test_no_bug_fixes_keeps_the_file_with_most_sites(self):
        out = C.cut(facts(f("a", 1, 0), f("b", 4, 0), f("c", 4, 0)))
        self.assertEqual([x["path"] for x in out["files"]], ["b"])  # 4 sites, then path order
        self.assertEqual((out["files_cut"], out["sites_cut"]), (2, 5))

    def test_left_stays_true_and_the_input_is_unchanged(self):
        before = facts(f("a", 3, 2), f("b", 1, 0))
        snapshot = copy.deepcopy(before)
        out = C.cut(before)
        self.assertEqual(before, snapshot)
        self.assertEqual(out["left"], 4)
        self.assertEqual(sum(x["sites"] for x in out["files"]) + out.get("sites_cut", 0), out["left"])

    def test_nothing_to_cut_adds_no_counts(self):
        out = C.cut(facts(f("a", 3, 2)))
        self.assertNotIn("files_cut", out)


class Measure(unittest.TestCase):
    def test_on_the_fixture_every_bug_fix_is_kept_with_fewer_files_than_random(self):
        db = K.build()
        r = C.measure(C.consumer_facts(db, load_config(CONFIG), "R2", K.R2[1]))
        self.assertEqual(r["teams"], 5)
        self.assertEqual(r["bug_fix_coverage"], 1.0)
        self.assertLess(r["random_order_coverage"], r["bug_fix_coverage"])
        self.assertLess(r["total"]["files"]["kept"], r["total"]["files"]["all"])

    def test_no_bug_fixes_anywhere_says_so(self):
        r = C.measure([facts(f("a", 1, 0), f("b", 2, 0))])
        self.assertIsNone(r["bug_fix_coverage"])
        self.assertEqual(r["teams_with_bug_fixes"], 0)


if __name__ == "__main__":
    unittest.main()
