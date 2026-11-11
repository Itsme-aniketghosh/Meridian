"""model/run_all.py: order, stopping at the first failure, missing steps, --until, --fake."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from model.run_all import FAKE_LATER, main, run, steps


def fake_plan(n=4):
    return [(f"step{i}", "owner", f"mod{i}", []) for i in range(1, n + 1)]


class Runner:
    """Records which modules ran and fails the ones listed in `fail`."""

    def __init__(self, fail=()):
        self.ran, self.fail = [], set(fail)

    def __call__(self, cmd, cwd=None, env=None):
        module = cmd[2]
        self.ran.append(module)
        return SimpleNamespace(returncode=3 if module in self.fail else 0)


def quiet(f, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = f(*a, **k)
    return result, buf.getvalue()


class Run(unittest.TestCase):
    def test_all_pass_in_order(self):
        r = Runner()
        code, out = quiet(run, fake_plan(), runner=r, find=lambda m: True)
        self.assertEqual(code, 0)
        self.assertEqual(r.ran, ["mod1", "mod2", "mod3", "mod4"])
        self.assertIn("all 4 steps passed", out)

    def test_stops_at_the_first_failure_and_says_which(self):
        r = Runner(fail={"mod2"})
        code, out = quiet(run, fake_plan(), runner=r, find=lambda m: True)
        self.assertEqual(code, 3)
        self.assertEqual(r.ran, ["mod1", "mod2"])
        self.assertIn("FAILED at step2", out)

    def test_a_missing_module_fails_instead_of_skipping(self):
        r = Runner()
        code, out = quiet(run, fake_plan(), runner=r, find=lambda m: None if m == "mod3" else True)
        self.assertEqual(code, 1)
        self.assertEqual(r.ran, ["mod1", "mod2"])
        self.assertIn("mod3 isn't in the repo yet", out)

    def test_until_stops_after_that_step(self):
        r = Runner()
        code, _ = quiet(run, fake_plan(), until="step2", runner=r, find=lambda m: True)
        self.assertEqual(code, 0)
        self.assertEqual(r.ran, ["mod1", "mod2"])

    def test_until_must_name_a_step(self):
        with self.assertRaises(SystemExit):
            quiet(run, fake_plan(), until="nope", runner=Runner(), find=lambda m: True)


class Plan(unittest.TestCase):
    def test_order_follows_the_guideline(self):
        self.assertEqual([s[0] for s in steps()],
                         ["data", "train", "seed noise", "evaluate", "bias", "gate", "registry"])

    def test_refit_only_changes_the_train_step(self):
        full, refit = steps(), steps(refit=True)
        self.assertEqual([s[0] for s in full], [s[0] for s in refit])
        self.assertEqual(dict((s[0], s[3]) for s in refit)["train"], ["--save-model"])
        self.assertEqual([s for s in full if s[0] != "train"], [s for s in refit if s[0] != "train"])

    def test_seed_noise_reads_the_picked_candidate(self):
        args = dict((s[0], s[3]) for s in steps())["seed noise"]
        self.assertEqual(args, ["--seed-noise", "reports/candidate.json"])

    def test_dry_run_lists_every_step_and_runs_none(self):
        code, out = quiet(main, ["--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("python -m"), 7)


class Fake(unittest.TestCase):
    def test_every_step_reads_and_writes_only_the_temp_folder(self):
        with tempfile.TemporaryDirectory() as d:
            for name, _, _, args in steps(d):
                with self.subTest(step=name):
                    self.assertIn(str(Path(d) / "features.parquet"), args)
                    self.assertIn(str(Path(d) / "reports"), args)

    def test_order_and_what_waits(self):
        with tempfile.TemporaryDirectory() as d:
            names = [s[0] for s in steps(d)]
        self.assertEqual(names, ["data", "train", "seed noise", "evaluate", "bias", "gate"])
        self.assertEqual(set(FAKE_LATER), {"registry"})

    def test_evaluate_and_bias_log_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            for name, _, _, args in steps(d):
                if name in ("evaluate", "bias"):
                    self.assertIn("--no-mlflow", args)

    def test_fake_dry_run(self):
        code, out = quiet(main, ["--fake", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(out.count("python -m"), 6)
        self.assertIn("meridian-fake-", out)


class Check(unittest.TestCase):
    def test_check_prints_rows_per_split_and_writes_nothing(self):
        from model.train import main as train_main
        from tests.fake_table import fake_table
        with tempfile.TemporaryDirectory() as d:
            fake_table(n=500, seed=1).to_csv(Path(d) / "f.csv", index=False)
            _, out = quiet(train_main, ["--check", "--features", str(Path(d) / "f.csv"), "--config",
                                        str(Path(d) / "none.toml"), "--out", str(Path(d) / "r")])
            self.assertFalse((Path(d) / "r").exists())
        self.assertIn("feature table ok: 500 rows", out)

    def test_check_reports_negative_age(self):
        from model.train import main as train_main
        from tests.fake_table import fake_table
        with tempfile.TemporaryDirectory() as d:
            df = fake_table(n=500, seed=1)
            df["age"] = df["age"].astype(float)
            df.loc[[0, 1], "age"] = [-1.94, -0.2]
            df.to_csv(Path(d) / "f.csv", index=False)
            _, out = quiet(train_main, ["--check", "--features", str(Path(d) / "f.csv"), "--config",
                                        str(Path(d) / "none.toml")])
        self.assertIn("age is below 0 on 2 rows (min -1.94)", out)


if __name__ == "__main__":
    unittest.main()
