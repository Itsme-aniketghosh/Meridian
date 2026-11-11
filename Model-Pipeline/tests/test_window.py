"""Decision 5 (model/window.py): the short window only drops early train rows, and the rule picks by the interval."""
import json
import tempfile
import unittest
from pathlib import Path

from model import window as W
from model.train import evaluate, pick_candidate
from tests.fake_table import fake_table


class Window(unittest.TestCase):
    def test_only_train_rows_before_2019_leave_train(self):
        df = fake_table(n=2000, seed=4)
        short = W.short_window(df)
        moved = (df["split"] != short["split"])
        self.assertTrue((df.loc[moved, "split"] == "train").all())
        self.assertTrue((df.loc[moved, "year"] < 2019).all())
        self.assertTrue((short.loc[moved, "split"] == "none").all())
        self.assertEqual((df["split"] == "val").sum(), (short["split"] == "val").sum())
        self.assertEqual((df["split"] == "test").sum(), (short["split"] == "test").sum())

    def test_command_compares_on_the_judged_val_rows(self):
        df = fake_table(n=3000, seed=6)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            df.to_csv(d / "f.csv", index=False)
            evaluate(df, 1, d, {"forest": {"n_estimators": 40}}, "t", reps=50)
            pick_candidate(d, "t", 1, reps=50)
            r = W.main(["--candidate", str(d / "candidate.json"), "--config", str(d / "none.toml"),
                        "--features", str(d / "f.csv"), "--out", str(d / "window.json"), "--reps", "200"])
            saved = json.loads((d / "window.json").read_text())
        self.assertEqual(saved, json.loads(json.dumps(r)))
        self.assertLess(r["train_rows"]["2019-2022"], r["train_rows"]["2015-2022"])
        lo, hi = r["interval"]
        self.assertEqual(r["picked"], "2019-2022" if lo > 0 else "2015-2022")


if __name__ == "__main__":
    unittest.main()
