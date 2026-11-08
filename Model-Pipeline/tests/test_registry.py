"""registry.py (Rochan, step G). Nov 8: the champion record and the readers. Registering, promotion, the push and
rollback get their tests on Nov 9 (packets/rochan-tracking, section G)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from model import registry as reg
from tests.contracts import make_rochan

CONTRACTS = Path(__file__).resolve().parent / "contracts"
GOOD = {"run_id": "b" * 32, "version": "c" * 40, "model_file": "model.joblib", "model_md5": "d" * 32}
BIAS = {"split": "val", "candidate": "lgbm", "n": 100, "pass": True}
NOISE = {"kind": "lgbm", "split": "val", "n": 100, "mean": 0.4, "sd": 0.01}


def metrics_like(column="lgbm", threshold=0.42):
    """The parts of a metrics.json the registry reads, made up."""
    def block(role, value):
        return {"role": role, "pr_auc": {"value": value, "interval": [value - 0.1, value + 0.1], "n": 100,
                                         "positive_rate": 0.2},
                "at_threshold": {"threshold": threshold, "rule": "precision", "alerts": 30, "chosen_on": "val",
                                 "n": 100, "positive_rate": 0.2}}
    return {"split": "val", "n": 100, "positive_rate": 0.2, "candidate": {"column": column, "kind": "lgbm"},
            "features_md5": "a" * 32,
            "models": {column: block("candidate", 0.4), "lines_x_files": block("baseline", 0.3)}}


def without(data, *path):
    """A copy of data with the key at path removed."""
    data = copy.deepcopy(data)
    *parents, last = path
    target = data
    for key in parents:
        target = target[key]
    del target[last]
    return data


def write(folder, name, data):
    path = Path(folder) / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class ChampionRecord(unittest.TestCase):
    def test_holds_the_candidates_block_as_is(self):
        m = metrics_like()
        c = reg.champion_record(m, **GOOD)
        self.assertEqual(list(c), list(reg.CHAMPION_KEYS))
        self.assertEqual({k: v for k, v in c["metrics"].items() if k not in ("split", "features_md5")},
                         m["models"]["lgbm"])
        self.assertEqual((c["metrics"]["split"], c["metrics"]["features_md5"]), ("val", "a" * 32))

    def test_p9_reads_the_threshold_chosen_on_val(self):
        c = reg.champion_record(metrics_like(threshold=0.37), **GOOD)
        self.assertEqual(c["metrics"]["at_threshold"]["threshold"], 0.37)

    def test_the_first_model_has_no_previous(self):
        self.assertIsNone(reg.champion_record(metrics_like(), **GOOD)["previous"])

    def test_previous_is_the_champion_it_replaces(self):
        old = reg.champion_record(metrics_like(), **GOOD)
        new = reg.champion_record(metrics_like(), **{**GOOD, "version": "e" * 40, "run_id": "f" * 32}, previous=old)
        self.assertEqual(new["previous"], {"version": "c" * 40, "run_id": "b" * 32})

    def test_the_candidate_must_be_in_the_models(self):
        m = metrics_like()
        m["candidate"]["column"] = "forest"
        with self.assertRaisesRegex(reg.ContractError, "'forest' isn't in models"):
            reg.champion_record(m, **GOOD)

    def test_bad_values_are_refused(self):
        cases = {"version": "abc", "run_id": "B" * 32, "model_md5": "d" * 31, "model_file": "registry/model.joblib"}
        for key, value in cases.items():
            with self.subTest(key), self.assertRaisesRegex(reg.ContractError, key):
                reg.champion_record(metrics_like(), **{**GOOD, key: value})

    def test_every_key_consumers_read_is_required(self):
        c = reg.champion_record(metrics_like(), **GOOD)
        paths = [(k,) for k in reg.CHAMPION_KEYS] + [("metrics", "split"), ("metrics", "features_md5"),
                                                     ("metrics", "pr_auc"), ("metrics", "pr_auc", "value"),
                                                     ("metrics", "at_threshold", "threshold")]
        for path in paths:
            with self.subTest(path), self.assertRaisesRegex(reg.ContractError, f"no {path[-1]}"):
                reg.check_champion(without(c, *path))

    def test_read_champion_reads_what_was_written(self):
        c = reg.champion_record(metrics_like(), **GOOD)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(reg.read_champion(write(d, "champion.json", c)), c)

    def test_a_missing_or_broken_file_is_a_contract_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(reg.ContractError, "not found"):
                reg.read_champion(Path(d) / "champion.json")
            (Path(d) / "bad.json").write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(reg.ContractError, "not JSON"):
                reg.read_champion(Path(d) / "bad.json")


class Readers(unittest.TestCase):
    """What registry.py reads from the files other steps write, on made-up data."""

    CASES = ((reg.read_metrics, metrics_like(), reg.METRICS_KEYS), (reg.read_bias, BIAS, reg.BIAS_KEYS),
             (reg.read_seed_noise, NOISE, reg.SEED_NOISE_KEYS))

    def test_good_files_read(self):
        with tempfile.TemporaryDirectory() as d:
            for reader, data, _ in self.CASES:
                self.assertEqual(reader(write(d, "x.json", data)), data)

    def test_every_key_the_registry_reads_is_required(self):
        with tempfile.TemporaryDirectory() as d:
            for reader, data, keys in self.CASES:
                for key in keys:
                    with self.subTest(reader=reader.__name__, key=key), \
                            self.assertRaisesRegex(reg.ContractError, f"no {key}"):
                        reader(write(d, "x.json", without(data, key)))

    def test_metrics_must_hold_the_candidates_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            for path in [("models", "lgbm"), ("models", "lgbm", "pr_auc"), ("models", "lgbm", "at_threshold"),
                         ("models", "lgbm", "at_threshold", "threshold"), ("candidate", "column")]:
                with self.subTest(path), self.assertRaises(reg.ContractError):
                    reg.read_metrics(write(d, "m.json", without(metrics_like(), *path)))

    def test_pass_must_be_true_or_false(self):
        with tempfile.TemporaryDirectory() as d:
            for value in ("yes", 1, None):
                with self.subTest(value), self.assertRaisesRegex(reg.ContractError, "pass is"):
                    reg.read_bias(write(d, "b.json", {**BIAS, "pass": value}))

    def test_sd_must_be_a_number_at_or_above_0(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(reg.read_seed_noise(write(d, "s.json", {**NOISE, "sd": 0}))["sd"], 0)
            for value in (-0.1, "0.1", True, None):
                with self.subTest(value), self.assertRaisesRegex(reg.ContractError, "sd is"):
                    reg.read_seed_noise(write(d, "s.json", {**NOISE, "sd": value}))


class ChampionExample(unittest.TestCase):
    """tests/contracts/champion.json, the example the gate and P9 test against (packets/README.md, Integration
    tests). Built by champion_record from a run on the fake table: python -m tests.contracts.make_rochan."""

    def test_the_example_reads(self):
        reg.read_champion(CONTRACTS / "champion.json")

    def test_it_has_the_shape_registry_writes(self):
        committed = json.loads((CONTRACTS / "champion.json").read_text(encoding="utf-8"))
        self.assertEqual(make_rochan.shape(committed), make_rochan.shape(make_rochan.build()[1]),
                         "champion.json's shape changed: run python -m tests.contracts.make_rochan and announce it "
                         "in the team chat (S10)")

    def test_it_comes_from_the_metrics_example(self):
        champion = json.loads((CONTRACTS / "champion.json").read_text(encoding="utf-8"))
        metrics = json.loads((CONTRACTS / "metrics.json").read_text(encoding="utf-8"))
        block = metrics["models"][metrics["candidate"]["column"]]
        self.assertEqual(champion["metrics"],
                         {"split": metrics["split"], "features_md5": metrics["features_md5"], **block})


class ExampleConsumer:
    """registry.py as a consumer of one example in tests/contracts/ (packets/README.md, Integration tests): the
    example reads, and a copy without any key registry.py reads fails. One class per example below."""

    def test_the_example_reads(self):
        self.READ(CONTRACTS / self.NAME)

    def test_a_missing_key_fails(self):
        data = json.loads((CONTRACTS / self.NAME).read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as d:
            for key in self.KEYS:
                with self.subTest(key), self.assertRaisesRegex(reg.ContractError, f"no {key}"):
                    self.READ(write(d, self.NAME, without(data, key)))


class MetricsExampleConsumer(ExampleConsumer, unittest.TestCase):
    READ, NAME, KEYS = staticmethod(reg.read_metrics), "metrics.json", reg.METRICS_KEYS  # Rochan's


@unittest.skipUnless((CONTRACTS / "bias_report.json").exists(),
                     "waiting for Chaitali's tests/contracts/bias_report.json (packets/README.md, Nov 8)")
class BiasExampleConsumer(ExampleConsumer, unittest.TestCase):
    READ, NAME, KEYS = staticmethod(reg.read_bias), "bias_report.json", reg.BIAS_KEYS


@unittest.skipUnless((CONTRACTS / "seed_noise.json").exists(),
                     "waiting for Rachna's tests/contracts/seed_noise.json (packets/README.md, Nov 8)")
class SeedNoiseExampleConsumer(ExampleConsumer, unittest.TestCase):
    READ, NAME, KEYS = staticmethod(reg.read_seed_noise), "seed_noise.json", reg.SEED_NOISE_KEYS


class Command(unittest.TestCase):
    def test_says_what_isnt_built_yet_and_fails(self):
        with self.assertRaises(SystemExit) as e:
            reg.main([])
        self.assertIn("Nov 9", str(e.exception))


if __name__ == "__main__":
    unittest.main()
