"""Fault 21: A broken champion file in the push check.
Expected: P9 falls back to lines × files and says so.
Why: a push check must never break on our model."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.contracts.make_vishwa import fixture


class BrokenChampion(unittest.TestCase):
    def test_p9_falls_back_to_lines_x_files_and_says_so(self):
        fixture()  # Data-Pipeline on the path
        from meridian import p9
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        unreadable = Path(tmp.name) / "a" / "champion.json"
        unreadable.parent.mkdir()
        unreadable.write_text("{not json", encoding="utf-8")
        corrupt = Path(tmp.name) / "b" / "champion.json"
        corrupt.parent.mkdir()
        (corrupt.parent / "model.joblib").write_bytes(b"not a model")
        corrupt.write_text(json.dumps({"model_file": "model.joblib",
                                       "model_md5": hashlib.md5(b"not a model").hexdigest()}), encoding="utf-8")
        for why, champion in (("champion.json doesn't read", unreadable), ("the model didn't load", corrupt)):
            with self.subTest(why):
                # db is never read: the champion fails before P9 reaches the push's features
                v = p9.verdict(None, "apache/spark", "R1", "2024-06-01T00:00:00Z", [], [], "a", r"\bSPARK-\d+\b",
                               (9, 3, 27), 10, champion)
                self.assertEqual((v.rule, v.alert), ("lines_x_files", True))
                self.assertTrue(v.text.startswith("9 lines × 3 files = 27, over 10 (lines × files: "), v.text)
                self.assertIn(why, v.text)


if __name__ == "__main__":
    unittest.main()
