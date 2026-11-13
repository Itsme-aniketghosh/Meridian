"""registry.py (Rochan, step G): the champion record and the readers, then registering, promotion (decision 6), what
it compares (decision 7), the push and rollback, against PASS-BARS.md §3.3, in a temp MLflow store."""
import copy
import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from model import registry as reg
from tests import mlflow_uri
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


HERE = Path(__file__).resolve().parents[1]
CONFIG, FEATURES = HERE / "model.toml", HERE / "data" / "features.parquet"
SEEDS = json.loads((HERE / "reports" / "seed_noise.json").read_text(encoding="utf-8"))  # Rachna's tuned forest


class Settings(unittest.TestCase):
    def test_defaults_and_overrides(self):
        self.assertEqual(reg.settings({})["rule"], "noise")
        self.assertEqual(reg.settings({"registry": {"project": "p"}})["project"], "p")

    def test_an_unknown_key_or_choice_stops(self):
        for cfg in ({"registry": {"rules": "any"}}, {"registry": {"rule": "maybe"}}, {"registry": {"compare": "x"}}):
            with self.assertRaises(SystemExit, msg=cfg):
                reg.settings(cfg)


class Verdict(unittest.TestCase):
    """Decision 6's rule as code: the margin, then the bias report."""

    def test_the_first_model_needs_only_its_bias_report(self):
        self.assertTrue(reg.verdict(0.2, None, True, 0.01)[0])
        self.assertEqual(reg.verdict(0.9, None, False, 0.01), (False, "its bias report didn't pass"))

    def test_a_gain_must_be_over_the_margin(self):
        self.assertTrue(reg.verdict(0.43, 0.40, True, 0.02)[0])
        self.assertIn("within the margin", reg.verdict(0.41, 0.40, True, 0.02)[1])
        self.assertIn("no better", reg.verdict(0.39, 0.40, True, 0.02)[1])
        self.assertFalse(reg.verdict(0.50, 0.40, False, 0.02)[0])

    def test_the_margin_is_noise_sds_sds_of_seed_noise_or_nothing(self):
        s = reg.settings({})
        self.assertAlmostEqual(reg.margin(s, 0.0021), 0.0042)
        self.assertEqual(reg.margin({**s, "rule": "any"}, 0.0021), 0.0)


class Store(unittest.TestCase):
    """A temp MLflow store and reports folder. Each registration writes a new model file and its files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: SQLAlchemy keeps mlflow.db open
        self.dir = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": mlflow_uri(self.dir)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        sha = mock.patch("model.tracking.git_state", return_value=("e" * 40, "false"))
        sha.start()
        self.addCleanup(sha.stop)
        self.s, self.said, self.n = reg.settings({}), [], 0
        self.champion = self.dir / "registry" / "champion.json"

    def files(self, pr_auc, bias_pass=True, sd=0.01, features_md5="a" * 32, model_bytes=None):
        self.n += 1
        folder = self.dir / f"r{self.n}"
        folder.mkdir()
        model = folder / "model.joblib"
        model.write_bytes(model_bytes if model_bytes is not None else f"model {self.n}".encode())
        (folder / "val_scores.csv").write_text("sha,label,forest\n", encoding="utf-8")
        cand = {"kind": "forest", "params": {}, "scores_file": "val_scores.csv", "column": "forest", "seed": 1,
                "model_file": "model.joblib", "model_md5": hashlib.md5(model.read_bytes()).hexdigest()}
        metrics = metrics_like("forest")
        metrics["models"]["forest"]["pr_auc"]["value"] = pr_auc
        metrics["features_md5"] = features_md5
        return {"candidate": write(folder, "candidate.json", cand), "metrics": write(folder, "metrics.json", metrics),
                "bias": write(folder, "bias_report.json", {**BIAS, "candidate": "forest", "pass": bias_pass}),
                "noise": write(folder, "seed_noise.json", {**NOISE, "sd": sd})}

    def register(self, paths, sha=None, **kw):
        """sha: the commit to file it under, for tests with more than one version on Artifact Registry."""
        if sha is None:
            return reg.register(paths, self.s, CONFIG, FEATURES, self.champion, say=self.said.append, **kw)
        with mock.patch("model.tracking.git_state", return_value=(sha, "false")):
            return reg.register(paths, self.s, CONFIG, FEATURES, self.champion, say=self.said.append, **kw)

    def new_store(self):
        """A second, empty MLflow store, as on CI: champion.json is all that's left of the first."""
        env = mock.patch.dict(os.environ, {"MLFLOW_TRACKING_URI": mlflow_uri(self.dir / "ci")})
        env.start()
        self.addCleanup(env.stop)

    def aliases(self):
        _, client = reg._client()
        return {a: getattr(reg.alias(client, "defect-risk", a), "version", None) for a in ("champion", "previous")}


class Promotion(Store):
    """PASS-BARS §3.3: first model, real gains, what blocks, noise never promotes."""

    def test_the_first_model_is_promoted_and_champion_json_written(self):
        tags = self.register(self.files(0.40))
        self.assertEqual((tags["promoted"], self.aliases()), ("true", {"champion": 1, "previous": None}))
        record = reg.read_champion(self.champion)
        self.assertEqual((record["version"], record["previous"], record["metrics"]["pr_auc"]["value"]),
                         ("e" * 40, None, 0.40))

    def test_a_real_gain_takes_champion_and_previous_points_at_the_old_one(self):
        self.register(self.files(0.40))
        first = reg.read_champion(self.champion)
        tags = self.register(self.files(0.45))  # margin 2 x 0.01
        self.assertEqual((tags["promoted"], self.aliases()), ("true", {"champion": 2, "previous": 1}))
        self.assertEqual(reg.read_champion(self.champion)["previous"],
                         {"version": first["version"], "run_id": first["run_id"]})

    def test_a_gain_within_seed_noise_is_registered_not_promoted(self):
        self.register(self.files(0.40))
        tags = self.register(self.files(0.41))
        self.assertEqual((tags["version"], tags["promoted"], self.aliases()["champion"]), (2, "false", 1))
        self.assertIn("within the margin 0.0200: seed noise", tags["reason"])

    def test_a_worse_model_is_registered_with_the_reason(self):
        self.register(self.files(0.40))
        tags = self.register(self.files(0.30))
        self.assertEqual(tags["promoted"], "false")
        self.assertIn("no better than the champion", tags["reason"])
        _, client = reg._client()
        self.assertEqual(client.get_model_version("defect-risk", "2").tags["reason"], tags["reason"])

    def test_a_failed_bias_report_blocks_promotion_even_of_the_first_model(self):
        tags = self.register(self.files(0.40, bias_pass=False))
        self.assertEqual((tags["promoted"], tags["reason"]), ("false", "its bias report didn't pass"))
        self.assertFalse(self.champion.exists())

    def test_rachnas_five_seeds_in_a_row_never_move_champion(self):
        """N = 5: the tuned forest refitted with seeds 1-5 (reports/seed_noise.json)."""
        for value in SEEDS["pr_auc"]:
            self.register(self.files(value, sd=SEEDS["sd"]))
        self.assertEqual(self.aliases(), {"champion": 1, "previous": None})

    def test_the_same_model_on_the_same_table_isnt_registered_twice(self):
        first = self.register(self.files(0.40, bias_pass=False, model_bytes=b"one model"))
        again = self.register(self.files(0.40, bias_pass=False, model_bytes=b"one model"), push=True)
        self.assertEqual((first["version"], again["version"]), (1, 1))
        self.assertIn("isn't registered again: its bias report didn't pass", self.said[-2])
        self.assertEqual(self.said[-1], "--push: there's no champion yet, so nothing to upload")

    def test_the_champion_sent_again_is_left_alone(self):
        self.register(self.files(0.40, model_bytes=b"one model"))
        again = self.register(self.files(0.40, model_bytes=b"one model"))
        self.assertEqual((again["version"], again["reason"], self.aliases()),
                         (1, "it's the champion", {"champion": 1, "previous": None}))

    def test_once_its_bias_report_passes_the_same_model_is_promoted(self):
        self.register(self.files(0.40, bias_pass=False, model_bytes=b"one model"))
        tags = self.register(self.files(0.40, model_bytes=b"one model"))
        self.assertEqual((tags["promoted"], self.aliases()["champion"]), ("true", 2))

    def test_the_champions_model_file_is_next_to_champion_json(self):
        """What P9 loads (Rachna, step Q): registry/<model_file> with the md5 champion.json records. A model that isn't
        promoted leaves registry/ alone; the next champion replaces it."""
        self.register(self.files(0.40, model_bytes=b"first"))
        record, placed = reg.read_champion(self.champion), self.champion.parent / "model.joblib"
        self.assertEqual(placed.read_bytes(), b"first")
        self.assertEqual(hashlib.md5(placed.read_bytes()).hexdigest(), record["model_md5"])
        self.register(self.files(0.30, model_bytes=b"worse"))
        self.assertEqual(placed.read_bytes(), b"first")
        self.register(self.files(0.45, model_bytes=b"better"))
        self.assertEqual(placed.read_bytes(), b"better")
        self.assertEqual(sorted(p.name for p in self.champion.parent.iterdir()), ["champion.json", "model.joblib"])

    def test_the_champion_sent_again_puts_back_a_missing_model_file(self):
        """champion.json from git, the model from train.py: registering it again puts the model where P9 looks."""
        self.register(self.files(0.40, model_bytes=b"one model"))
        (self.champion.parent / "model.joblib").unlink()
        self.register(self.files(0.40, model_bytes=b"one model"))
        self.assertEqual((self.champion.parent / "model.joblib").read_bytes(), b"one model")

    def test_the_model_must_be_the_one_train_py_judged(self):
        paths = self.files(0.40)
        (Path(paths["candidate"]).parent / "model.joblib").write_bytes(b"something else")
        with self.assertRaisesRegex(reg.ContractError, "not the model train.py judged"):
            self.register(paths)

    def test_every_file_must_be_about_the_same_candidate(self):
        paths = self.files(0.40)
        write(Path(paths["bias"]).parent, "bias_report.json", {**BIAS, "candidate": "lgbm"})
        with self.assertRaisesRegex(reg.ContractError, "run evaluate and bias on this candidate"):
            self.register(paths)


class Rollback(Store):
    def test_rollback_puts_champion_back_and_rewrites_champion_json(self):
        self.register(self.files(0.40))
        first = reg.read_champion(self.champion)
        self.register(self.files(0.45))
        reg.rollback(self.s, self.champion, say=self.said.append)
        self.assertEqual(self.aliases(), {"champion": 1, "previous": None})
        self.assertEqual(reg.read_champion(self.champion), first)
        _, client = reg._client()
        self.assertIn("went back to version 1", client.get_model_version("defect-risk", "2").tags["rolled_back"])

    def test_rollback_puts_the_previous_model_file_back_next_to_champion_json(self):
        self.register(self.files(0.40, model_bytes=b"first"))
        self.register(self.files(0.45, model_bytes=b"second"))
        reg.rollback(self.s, self.champion, say=self.said.append)
        record = reg.read_champion(self.champion)
        placed = self.champion.parent / record["model_file"]
        self.assertEqual(placed.read_bytes(), b"first")
        self.assertEqual(hashlib.md5(placed.read_bytes()).hexdigest(), record["model_md5"])

    def test_previous_moves_back_a_step_too(self):
        for value in (0.40, 0.45, 0.50):
            self.register(self.files(value))
        reg.rollback(self.s, self.champion, say=self.said.append)
        self.assertEqual(self.aliases(), {"champion": 2, "previous": 1})

    def test_with_nothing_before_it_stops(self):
        self.register(self.files(0.40))
        with self.assertRaisesRegex(SystemExit, "no previous version"):
            reg.rollback(self.s, self.champion, say=self.said.append)


class Rescore(Store):
    """Decision 7: when the champion was judged on another table, its own model is scored on today's val."""

    def test_a_champion_from_another_table_is_rescored_on_todays_val(self):
        import joblib
        from model import train
        df = train.load_features(FEATURES)
        model, _, _ = train.fit_and_score(df, "logreg", 1)
        path = self.dir / "logreg.joblib"
        joblib.dump(model, path)
        today = hashlib.md5(FEATURES.read_bytes()).hexdigest()
        self.register(self.files(0.99, features_md5="f" * 32, model_bytes=path.read_bytes()))
        tags = self.register(self.files(0.30, features_md5=today))
        val = train.rows(df, "val")
        expect = train.pr_auc(val["label"].to_numpy(), train.score_with(model, val))
        self.assertIn("re-scored on today's val (N = 4,113)", tags["compared"])
        self.assertAlmostEqual(float(tags["champion_pr_auc"]), expect, places=12)

    def test_on_the_same_table_the_stored_number_is_used(self):
        self.register(self.files(0.40))
        tags = self.register(self.files(0.41))
        self.assertEqual((tags["compared"], float(tags["champion_pr_auc"])), ("stored: same table", 0.40))


class FakeGCP:
    """Artifact Registry as gcloud shows it, in memory: generic upload, files list, versions list, generic download
    and auth list. Like the real one, a file can't be uploaded to a version twice."""

    def __init__(self, signed_in="me@example.com"):
        self.held, self.calls, self.signed_in = {}, [], signed_in  # held: {(package, version): {file name: bytes}}

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        args = dict(a[2:].split("=", 1) for a in cmd if a.startswith("--") and "=" in a)
        what, out = cmd[1:4], ""
        files = self.held.get((args.get("package"), args.get("version")), {})
        if what[:2] == ["auth", "list"]:
            out = self.signed_in
        elif what == ["artifacts", "generic", "upload"]:
            source = Path(args["source"])
            if source.name in files:
                return mock.Mock(returncode=1, stdout="", stderr=f"ALREADY_EXISTS: {source.name}")
            self.held.setdefault((args["package"], args["version"]), {})[source.name] = source.read_bytes()
        elif what == ["artifacts", "files", "list"]:
            out = json.dumps([{"name": f"projects/p/locations/l/repositories/r/files/{args['package']}:"
                                       f"{args['version']}:{n}",
                               "hashes": [{"type": "SHA256", "value": hashlib.sha256(b).hexdigest()},
                                          {"type": "MD5", "value": hashlib.md5(b).hexdigest()}]}
                              for n, b in files.items()])
        elif what == ["artifacts", "versions", "list"]:
            versions = sorted(v for p, v in self.held if p == args["package"])
            if not versions:
                return mock.Mock(returncode=1, stdout="", stderr='Requested package does not exist: "status": '
                                                                 '"NOT_FOUND"')
            out = "\n".join(versions) + "\n"
        elif what == ["artifacts", "generic", "download"]:
            if args["name"] not in files:
                return mock.Mock(returncode=1, stdout="", stderr="NOT_FOUND")
            (Path(args["destination"]) / args["name"]).write_bytes(files[args["name"]])
        return mock.Mock(returncode=0, stdout=out, stderr="")

    def uploads(self):
        """(package, version, file name) of each upload, in order."""
        out = []
        for c in self.calls:
            if c[1:4] == ["artifacts", "generic", "upload"]:
                args = dict(a[2:].split("=", 1) for a in c if a.startswith("--"))
                out.append((args["package"], args["version"], Path(args["source"]).name))
        return out

    def pointer(self):
        """The champion.json the pointer package's newest version holds, or None."""
        versions = sorted(v for p, v in self.held if p == "champion")
        return json.loads(self.held["champion", versions[-1]]["champion.json"]) if versions else None


class Push(Store):
    """PASS-BARS §3.3 'No credentials', and what --push sends."""

    def setUp(self):
        super().setUp()
        self.gcp = FakeGCP()
        which = mock.patch("model.registry.shutil.which", return_value="/bin/gcloud")
        which.start()
        self.addCleanup(which.stop)

    def on_gcp(self):
        self.s = {**self.s, "project": "p"}

    def test_without_a_project_it_skips_and_says_so(self):
        self.register(self.files(0.40), push=True, runner=self.gcp)
        self.assertEqual(self.gcp.calls, [])
        self.assertTrue(self.champion.exists())
        self.assertIn("--push skipped: model.toml [registry] project isn't set", self.said[-1])

    def test_without_gcloud_or_a_signed_in_account_it_skips(self):
        self.on_gcp()
        with mock.patch("model.registry.shutil.which", return_value=None):
            self.register(self.files(0.40), push=True, runner=self.gcp)
        self.assertIn("gcloud isn't installed", self.said[-1])
        self.register(self.files(0.50), push=True, runner=FakeGCP(signed_in=""))
        self.assertIn("no gcloud account is signed in", self.said[-1])

    def test_promotion_uploads_the_model_and_champion_json_then_the_pointer(self):
        self.on_gcp()
        self.register(self.files(0.40), push=True, runner=self.gcp)
        uploads = [c for c in self.gcp.calls if c[1:4] == ["artifacts", "generic", "upload"]]

        def name(arg):  # --source=<path>: the file's name, whichever slash the path has (Windows: \)
            return Path(arg.split("=", 1)[1]).name

        self.assertEqual([(a[8], name(a[9])) for a in uploads[:2]],
                         [("--version=" + "e" * 40, "model.joblib"), ("--version=" + "e" * 40, "champion.json")])
        self.assertEqual(uploads[0][4:8], ["--project=p", "--location=us-east1", "--repository=meridian-models",
                                           "--package=defect-risk"])
        self.assertEqual((uploads[2][7], name(uploads[2][9])), ("--package=champion", "champion.json"))
        self.assertRegex(uploads[2][8], r"^--version=\d{8}t\d{6}z$")
        self.assertEqual(self.gcp.pointer(), reg.read_champion(self.champion))

    def test_champion_json_is_the_same_bytes_on_every_os(self):
        """'\n' line ends, never Windows' '\r\n': else the md5 of one record differs between machines, and a push
        from Windows would find "another champion.json" where a Mac or CI pushed the same one."""
        self.on_gcp()
        self.register(self.files(0.40), push=True, runner=self.gcp)
        record = reg.read_champion(self.champion)
        expect = (json.dumps(record, indent=2) + "\n").encode("utf-8")
        self.assertEqual((self.champion.read_bytes(), self.gcp.held["defect-risk", "e" * 40]["champion.json"]),
                         (expect, expect))

    def test_gcloud_is_started_by_its_full_path(self):
        """Windows: gcloud is gcloud.cmd, which subprocess can't find by its bare name."""
        self.on_gcp()
        with mock.patch("model.registry.shutil.which", return_value=r"C:\gcloud\bin\gcloud.cmd"):
            self.register(self.files(0.40), push=True, runner=self.gcp)
        self.assertEqual({c[0] for c in self.gcp.calls}, {r"C:\gcloud\bin\gcloud.cmd"})

    def test_uncommitted_code_isnt_pushed(self):
        self.on_gcp()
        with mock.patch("model.tracking.git_state", return_value=("e" * 40, "true")):
            with self.assertRaisesRegex(SystemExit, "needs committed code"):
                self.register(self.files(0.40), push=True, runner=self.gcp)
        self.assertEqual(self.gcp.uploads(), [])

    def test_a_model_registered_in_a_docker_image_isnt_pushed(self):
        """No .git in the image: the sha is the GIT_SHA build arg, and git can't say the code was committed."""
        self.on_gcp()
        with mock.patch("model.tracking.git_state", return_value=("e" * 40, "unknown")):
            with self.assertRaisesRegex(SystemExit, "a Docker image has no .git"):
                self.register(self.files(0.40), push=True, runner=self.gcp)
        self.assertEqual(self.gcp.uploads(), [])

    def test_a_push_skipped_before_is_done_by_the_next_push(self):
        """run_all registers without --push. The champion still reaches Artifact Registry on the next --push."""
        self.on_gcp()
        self.register(self.files(0.40, model_bytes=b"one model"))
        self.assertEqual(self.gcp.calls, [])
        again = self.register(self.files(0.40, model_bytes=b"one model"), push=True, runner=self.gcp)
        self.assertEqual(again["reason"], "it's the champion")
        self.assertEqual([u[2] for u in self.gcp.uploads()], ["model.joblib", "champion.json", "champion.json"])
        self.assertEqual(self.gcp.held["defect-risk", "e" * 40]["model.joblib"], b"one model")
        self.assertEqual(self.gcp.pointer(), reg.read_champion(self.champion))

    def test_a_push_that_failed_halfway_sends_only_the_rest(self):
        self.on_gcp()
        self.register(self.files(0.40, model_bytes=b"one model"), push=True, runner=self.gcp)
        del self.gcp.held["defect-risk", "e" * 40]["champion.json"]
        for key in [k for k in self.gcp.held if k[0] == "champion"]:
            del self.gcp.held[key]
        self.gcp.calls.clear()
        self.register(self.files(0.40, model_bytes=b"one model"), push=True, runner=self.gcp)
        self.assertEqual(self.gcp.uploads(), [("defect-risk", "e" * 40, "champion.json"),
                                              ("champion", self.gcp.uploads()[1][1], "champion.json")])

    def test_pushing_again_sends_nothing(self):
        """A rerun on the same commit: the model, its record and the pointer are all there already."""
        self.on_gcp()
        self.register(self.files(0.40, model_bytes=b"one model"), push=True, runner=self.gcp)
        self.gcp.calls.clear()
        self.register(self.files(0.40, model_bytes=b"one model"), push=True, runner=self.gcp)
        self.assertEqual(self.gcp.uploads(), [])
        self.assertIn("names it already: nothing new to send", self.said[-1])

    def test_another_model_under_the_same_version_stops_before_any_upload(self):
        self.on_gcp()
        self.register(self.files(0.40), push=True, runner=self.gcp)
        self.gcp.calls.clear()
        with self.assertRaisesRegex(SystemExit, "has another model.joblib"):
            self.register(self.files(0.50), push=True, runner=self.gcp)  # the same commit, e * 40
        self.assertEqual(self.gcp.uploads(), [])

    def test_a_model_not_promoted_still_leaves_the_champion_on_gcp(self):
        self.on_gcp()
        self.register(self.files(0.40, model_bytes=b"the champion"))
        tags = self.register(self.files(0.30), push=True, runner=self.gcp, sha="1" * 40)
        self.assertEqual(tags["promoted"], "false")
        self.assertEqual(self.gcp.held["defect-risk", "e" * 40]["model.joblib"], b"the champion")
        self.assertNotIn(("defect-risk", "1" * 40), self.gcp.held)
        self.assertEqual(self.gcp.pointer()["version"], "e" * 40)

    def test_rollback_pushes_the_restored_record_as_the_pointer(self):
        self.on_gcp()
        self.register(self.files(0.40), push=True, runner=self.gcp, sha="1" * 40)
        self.register(self.files(0.45), push=True, runner=self.gcp, sha="2" * 40)
        self.gcp.calls.clear()
        reg.rollback(self.s, self.champion, push=True, runner=self.gcp, say=self.said.append)
        self.assertEqual([u[0] for u in self.gcp.uploads()], ["champion"])
        self.assertEqual(self.gcp.pointer()["version"], "1" * 40)

    def test_rollback_to_a_version_never_pushed_uploads_it_first(self):
        """Else the pointer would name a model Artifact Registry doesn't have."""
        self.on_gcp()
        self.register(self.files(0.40, model_bytes=b"first"), sha="1" * 40)
        self.register(self.files(0.45), push=True, runner=self.gcp, sha="2" * 40)
        reg.rollback(self.s, self.champion, push=True, runner=self.gcp, say=self.said.append)
        self.assertEqual(self.gcp.held["defect-risk", "1" * 40]["model.joblib"], b"first")
        self.assertEqual(self.gcp.pointer()["version"], "1" * 40)

    def test_pointer_versions_always_sort_after_the_newest(self):
        real = time.gmtime
        with mock.patch("model.registry.time.gmtime", side_effect=lambda *t: real(*t) if t else real(0)):  # now: 1970
            self.assertEqual(reg.pointer_version(), "19700101t000000z")
            self.assertEqual(reg.pointer_version("19700101t000000z"), "19700101t000001z")
            self.assertEqual(reg.pointer_version("20261110t120000z"), "20261110t120001z")  # a clock behind


class NewStore(Store):
    """CI's MLflow store is new every run: champion.json (in git, PLAN §8) is what knows the champion."""

    def test_the_champion_comes_from_champion_json(self):
        self.register(self.files(0.40))
        first = reg.read_champion(self.champion)
        self.new_store()
        tags = self.register(self.files(0.41))
        self.assertEqual((tags["promoted"], tags["compared"], float(tags["champion_pr_auc"])),
                         ("false", "stored: same table", 0.40))
        self.assertIn("within the margin", tags["reason"])
        tags = self.register(self.files(0.45))
        self.assertEqual(tags["promoted"], "true")
        self.assertEqual(reg.read_champion(self.champion)["previous"],
                         {"version": first["version"], "run_id": first["run_id"]})
        self.assertEqual(self.aliases(), {"champion": 2, "previous": None})

    def test_the_champion_sent_again_isnt_registered(self):
        self.register(self.files(0.40, model_bytes=b"one model"))
        self.new_store()
        tags = self.register(self.files(0.40, model_bytes=b"one model"))
        self.assertEqual((tags["version"], tags["reason"]), (None, "it's the champion"))
        _, client = reg._client()
        self.assertEqual(client.search_model_versions("name = 'defect-risk'"), [])

    def test_a_champion_from_another_table_needs_its_model_to_be_rescored(self):
        self.register(self.files(0.99, features_md5="f" * 32))
        (self.champion.parent / "model.joblib").unlink()  # only champion.json came along
        self.new_store()
        with self.assertRaisesRegex(SystemExit, "re-scores its model on today's val, and its model.joblib isn't"):
            self.register(self.files(0.30, features_md5=hashlib.md5(FEATURES.read_bytes()).hexdigest()))

    def test_a_champion_registered_elsewhere_is_pushed_from_there(self):
        """CI knows the champion from champion.json only: it can't say the champion's code was committed."""
        self.register(self.files(0.40, model_bytes=b"one model"))
        self.new_store()
        gcp = FakeGCP()
        with mock.patch("model.registry.shutil.which", return_value="/bin/gcloud"):
            self.s = {**self.s, "project": "p"}
            with self.assertRaisesRegex(SystemExit, "another MLflow store registered this model"):
                self.register(self.files(0.40, model_bytes=b"one model"), push=True, runner=gcp)
            self.assertEqual(gcp.uploads(), [])
            first = FakeGCP()  # pushed from where it was registered: then CI finds it there
            first.held["defect-risk", "e" * 40] = {"model.joblib": b"one model",
                                                   "champion.json": (json.dumps(reg.read_champion(self.champion),
                                                                                indent=2) + "\n").encode()}
            self.register(self.files(0.40, model_bytes=b"one model"), push=True, runner=first)
        self.assertEqual(first.uploads(), [("champion", first.uploads()[0][1], "champion.json")])

    def test_a_champion_from_another_table_is_rescored_from_the_model_next_to_champion_json(self):
        """CI with registry/ from git: the store is new, and the model file next to champion.json is the champion's."""
        import joblib
        from model import train
        df = train.load_features(FEATURES)
        model, _, _ = train.fit_and_score(df, "logreg", 1)
        path = self.dir / "logreg.joblib"
        joblib.dump(model, path)
        self.register(self.files(0.99, features_md5="f" * 32, model_bytes=path.read_bytes()))
        self.new_store()
        tags = self.register(self.files(0.30, features_md5=hashlib.md5(FEATURES.read_bytes()).hexdigest()))
        val = train.rows(df, "val")
        self.assertIn("re-scored on today's val (N = 4,113)", tags["compared"])
        self.assertAlmostEqual(float(tags["champion_pr_auc"]),
                               train.pr_auc(val["label"].to_numpy(), train.score_with(model, val)), places=12)

    def test_rollback_needs_the_store_that_holds_the_previous_version(self):
        self.register(self.files(0.40))
        self.register(self.files(0.45))
        self.new_store()
        with self.assertRaisesRegex(SystemExit, "champion.json's previous is eeeeeeeeeeee, and this MLflow store "
                                                "doesn't hold it"):
            reg.rollback(self.s, self.champion, say=self.said.append)


class Provenance(Store):
    def test_outside_git_with_no_sha_nothing_is_registered(self):
        with mock.patch("model.tracking.git_state", return_value=("unknown", "unknown")):
            with self.assertRaisesRegex(SystemExit, "a version is the git sha .* here it's 'unknown'"):
                self.register(self.files(0.40))
        self.assertFalse(self.champion.exists())

    def test_a_store_that_cant_be_read_isnt_taken_for_no_champion(self):
        from mlflow.exceptions import MlflowException
        from mlflow.protos.databricks_pb2 import INTERNAL_ERROR, INVALID_PARAMETER_VALUE
        client = mock.Mock()
        client.get_model_version_by_alias.side_effect = MlflowException("Registered model alias champion not found.",
                                                                        error_code=INVALID_PARAMETER_VALUE)
        self.assertIsNone(reg.alias(client, "defect-risk"))
        client.get_model_version_by_alias.side_effect = MlflowException("database is locked",
                                                                        error_code=INTERNAL_ERROR)
        with self.assertRaisesRegex(MlflowException, "database is locked"):
            reg.alias(client, "defect-risk")


class Decisions(unittest.TestCase):
    def test_moves_counts_promotions_in_arrival_order(self):
        self.assertEqual(reg.moves([0.40, 0.41, 0.39, 0.42], 0.0), 2)
        self.assertEqual(reg.moves([0.40, 0.41, 0.39, 0.42], 0.015), 1)

    def test_decision_6_on_rachnas_seeds(self):
        res = reg.decide_6(SEEDS, reg.settings({}))
        self.assertEqual(res["noise"]["seed_order"], 0)
        self.assertEqual(res["any"]["orders"], 120)
        self.assertEqual(reg.pick_6(res), "noise")

    def test_decision_7_picks_rescore_only_over_the_margin(self):
        self.assertEqual(reg.pick_7({"gap": 0.01, "margin": 0.004}), "rescore")
        self.assertEqual(reg.pick_7({"gap": 0.001, "margin": 0.004}), "stored")


class Command(Store):
    def test_registers_from_the_command_line(self):
        paths = self.files(0.40)
        args = ["--candidate", str(paths["candidate"]), "--metrics", str(paths["metrics"]), "--bias",
                str(paths["bias"]), "--seed-noise", str(paths["noise"]), "--champion", str(self.champion)]
        self.assertEqual(reg.main(args), 0)
        self.assertEqual(self.aliases()["champion"], 1)

    def test_a_contract_problem_exits_with_the_message(self):
        with self.assertRaisesRegex(SystemExit, "model.registry: .*not found"):
            reg.main(["--candidate", str(self.dir / "nope.json")])


if __name__ == "__main__":
    unittest.main()
