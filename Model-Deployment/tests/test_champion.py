"""champion.py (S3): deploy.yml ships only a champion whose model file is the record's, and the record it was asked for.

    python -m unittest discover -s Model-Deployment/tests -v
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
import champion as C  # noqa: E402
from model import registry as R  # noqa: E402

try:  # registry.download reads md5s through model.evaluate, which needs the model's packages (deploy.yml has them)
    import numpy  # noqa: F401
    HAS_MODEL_PACKAGES = True
except ModuleNotFoundError:
    HAS_MODEL_PACKAGES = False


def record(model_bytes, version="a" * 40):
    return {"version": version, "run_id": "b" * 32, "model_file": "model.joblib",
            "model_md5": hashlib.md5(model_bytes).hexdigest(),
            "metrics": {"split": "val", "features_md5": "c" * 32, "pr_auc": {"value": 0.4067, "n": 4113},
                        "at_threshold": {"threshold": None, "rule": "precision"}},
            "previous": None}


def write(folder, rec, model_bytes):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "champion.json").write_bytes(R.champion_bytes(rec))
    (folder / rec["model_file"]).write_bytes(model_bytes)
    return folder / "champion.json"


class FakeGcloud:
    """gcloud's answers for a pointer with versions and one model version on Artifact Registry."""

    def __init__(self, pointer_versions, files):
        self.pointer_versions, self.files, self.calls = pointer_versions, files, []

    def __call__(self, args, **_):
        self.calls.append(args[1:])
        if args[1:3] == ["auth", "list"]:
            return SimpleNamespace(returncode=0, stdout="meridian-deploy@x.iam.gserviceaccount.com\n", stderr="")
        if args[1:4] == ["artifacts", "versions", "list"]:
            return SimpleNamespace(returncode=0, stdout="\n".join(self.pointer_versions), stderr="")
        if args[1:4] == ["artifacts", "generic", "download"]:
            opt = dict(a.split("=", 1) for a in args if a.startswith("--"))
            data = self.files.get((opt["--package"], opt["--version"], opt["--name"]))
            if data is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="NOT_FOUND")
            Path(opt["--destination"]).mkdir(parents=True, exist_ok=True)
            (Path(opt["--destination"]) / opt["--name"]).write_bytes(data)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected gcloud {args}")


class Committed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name) / "registry"
        self.model = b"the model"

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_repos_own_champion_checks(self):
        rec, model = C.committed()
        self.assertEqual(rec["model_md5"], C.md5(model))

    def test_a_champion_and_its_model(self):
        path = write(self.dir, record(self.model), self.model)
        rec, model = C.committed(path)
        self.assertEqual(model, self.dir / "model.joblib")

    def test_a_model_file_that_isnt_the_records_is_refused(self):
        path = write(self.dir, record(self.model), b"another model")
        with self.assertRaisesRegex(C.ChampionError, "md5"):
            C.committed(path)

    def test_a_missing_model_file_is_refused(self):
        path = write(self.dir, record(self.model), self.model)
        (self.dir / "model.joblib").unlink()
        with self.assertRaisesRegex(C.ChampionError, "no model.joblib"):
            C.committed(path)

    def test_no_champion_at_all(self):
        with self.assertRaisesRegex(C.ChampionError, "nothing has been registered"):
            C.committed(self.dir / "champion.json")


@unittest.skipUnless(HAS_MODEL_PACKAGES, "needs Model-Pipeline/requirements.txt (deploy.yml installs it)")
class Pointer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dest = Path(self.tmp.name)
        self.model = b"the new model"
        self.rec = record(self.model, version="d" * 40)
        self.which = mock.patch("shutil.which", return_value="/usr/bin/gcloud")
        self.which.start()

    def tearDown(self):
        self.which.stop()
        self.tmp.cleanup()

    def gcloud(self, model=None, versions=("20261101t000000z", "20261120t030000z")):
        old = record(b"the old model", version="e" * 40)
        files = {("defect-risk", self.rec["version"], "model.joblib"): self.model if model is None else model}
        if versions:
            files[("champion", versions[0], "champion.json")] = R.champion_bytes(old)
            files[("champion", versions[-1], "champion.json")] = R.champion_bytes(self.rec)
        return FakeGcloud(list(versions), files)

    def test_the_newest_pointer_and_its_model(self):
        rec, model = C.pointer(self.dest, runner=self.gcloud())
        self.assertEqual(rec["version"], "d" * 40)
        self.assertEqual(C.md5(model), rec["model_md5"])

    def test_a_downloaded_model_that_isnt_the_records_is_refused(self):
        with self.assertRaisesRegex(C.ChampionError, "md5"):
            C.pointer(self.dest, runner=self.gcloud(model=b"tampered"))

    def test_no_pointer_version(self):
        with self.assertRaisesRegex(C.ChampionError, "no champion was ever pushed"):
            C.pointer(self.dest, runner=self.gcloud(versions=()))

    def test_not_signed_in(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(C.ChampionError, "gcloud isn't installed"):
                C.pointer(self.dest, runner=self.gcloud())


class Expected(unittest.TestCase):
    def setUp(self):
        self.rec = record(b"m", version="d" * 40)

    def test_the_dispatchs_version_and_md5(self):
        C.expected(self.rec, "d" * 40, self.rec["model_md5"])

    def test_another_version_than_the_dispatch_named(self):
        with self.assertRaisesRegex(C.ChampionError, "the dispatch"):
            C.expected(self.rec, "f" * 40, None)

    def test_without_a_dispatch_the_committed_champion_decides(self):
        with self.assertRaisesRegex(C.ChampionError, "registry/champion.json"):
            C.expected(self.rec)
        C.expected(R.read_champion())


class Main(unittest.TestCase):
    def test_outputs_and_stage(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            out, stage = Path(d) / "out", Path(d) / "stage"
            got = C.main(["--source", "committed", "--github-output", str(out), "--stage", str(stage)])
            lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
            self.assertEqual(lines["version"], got["version"])
            self.assertEqual(C.md5(stage / "model.joblib"), lines["md5"])
            self.assertEqual(json.loads((stage / "champion.json").read_text())["version"], got["version"])

    def test_a_refusal_exits_with_the_reason(self):
        with self.assertRaises(SystemExit) as e:
            C.main(["--source", "committed", "--expect-version", "f" * 40])
        self.assertIn("the dispatch", str(e.exception))


if __name__ == "__main__":
    unittest.main()
