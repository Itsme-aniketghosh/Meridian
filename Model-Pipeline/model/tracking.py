"""One way to log every run to MLflow (Contract 3 in Model-Pipeline/packets/README.md).

    import mlflow
    from model import tracking

    with tracking.start_run("lgbm-untuned", owner="rachna", decision="rachna-1"):
        mlflow.log_metric("val_pr_auc", 0.41)

Every run gets the tags below, so any number in MLflow traces back to the block, the code, the
config and the feature table it came from:

    owner                    who ran it: your packet name
    block_run_id             the phase 1 block. Model runs: model.toml [data] run_id unless given
    git_sha, git_dirty       the commit, and whether tracked files had changes not yet committed
    config_sha256            sha256 of Model-Pipeline/model.toml
    meridian_config_sha256   sha256 of Data-Pipeline/meridian.toml (ground rules: both hashes on every run)
    features_md5             md5 of the feature table, model runs only, checked against features.schema.json
    decision                 <packet>-<n>, for decision tests

The store is local: $MLFLOW_TRACKING_URI if set, else sqlite:///<repo root>/mlflow.db from whatever
folder you run in. Artifacts go in mlruns/ next to it. Git ignores both. To look: `mlflow ui` at the
repo root.

`python -m model.tracking --owner <you>` prints the tags a run would get here, and logs nothing.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import tomllib
import warnings
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]  # Model-Pipeline/
ROOT = HERE.parent  # the repo
MODEL_TOML = HERE / "model.toml"
MERIDIAN_TOML = ROOT / "Data-Pipeline" / "meridian.toml"
FEATURES = HERE / "data" / "features.parquet"

MODEL = "meridian-defect-risk"
AGENTS = "meridian-agents"
AUDITOR = "meridian-auditor"
EXPERIMENTS = (MODEL, AGENTS, AUDITOR)

# Set here and nowhere else: a caller can't overwrite them through tags=.
OWN_TAGS = ("owner", "block_run_id", "git_sha", "git_dirty", "config_sha256", "meridian_config_sha256",
            "features_md5", "decision")
OWNER = re.compile(r"^[a-z][a-z0-9-]*$")  # your packet name, lower case: rochan, rachna
DECISION = re.compile(r"^[a-z][a-z0-9]*-[0-9]+$")  # <packet>-<n>: rochan-4

_lock = threading.RLock()
_experiment_ids = {}  # (tracking uri, experiment name) -> id


# ---------- the tags ----------
@lru_cache(maxsize=1)
def git_state():
    """(sha, dirty) of the checkout, read once per process. Outside git (a Docker image): $MERIDIAN_GIT_SHA."""
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True,
                              timeout=30).stdout.strip()
    try:
        sha = git("rev-parse", "HEAD")
        dirty = git("status", "--porcelain", "--untracked-files=no")
        return sha, "true" if dirty else "false"
    except (OSError, subprocess.SubprocessError):
        sha = os.environ.get("MERIDIAN_GIT_SHA")
        if sha:
            return sha, "unknown"
        warnings.warn("not a git checkout and $MERIDIAN_GIT_SHA isn't set: git_sha = unknown", stacklevel=2)
        return "unknown", "unknown"


def file_digest(path, algorithm):
    """Hex digest of a file's bytes, read in 1 MB chunks."""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_config(path, required, what):
    path = Path(path)
    if path.exists():
        return path.read_bytes()
    if required:
        raise FileNotFoundError(f"{path} not found: {what}")
    return None


def _check_features(features, md5, block_run_id):
    """The table must be the one its features.schema.json describes, built from this run's block."""
    schema_path = features.with_name(features.stem + ".schema.json")
    if not schema_path.exists():
        return
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema.get("md5") and schema["md5"] != md5:
        raise ValueError(f"{features.name} has md5 {md5}, but {schema_path.name} says {schema['md5']}: the "
                         f"table and its schema come from different builds. Rebuild: python -m model.data")
    if schema.get("block_run_id") and schema["block_run_id"] != block_run_id:
        raise ValueError(f"{features.name} was built from block {schema['block_run_id']}, but this run is "
                         f"tagged block {block_run_id}. Check model.toml [data] run_id")


def run_tags(owner, experiment=MODEL, *, block_run_id=None, decision=None, config=None, meridian_config=None,
             features=None, tags=None):
    """The tags start_run puts on a run. Raises before anything is logged if one can't be set."""
    if experiment not in EXPERIMENTS:
        raise ValueError(f"experiment {experiment!r}: Contract 3 has {', '.join(EXPERIMENTS)}")
    if not isinstance(owner, str) or not OWNER.match(owner):
        raise ValueError(f"owner {owner!r}: your packet name in lower case, such as 'rochan'")
    if decision is not None and not (isinstance(decision, str) and DECISION.match(decision)):
        raise ValueError(f"decision {decision!r}: <packet>-<n>, such as 'rochan-4'")
    extra = {str(k): str(v) for k, v in (tags or {}).items()}
    clash = sorted(set(extra) & set(OWN_TAGS))
    if clash:
        raise ValueError(f"tags {clash} are set by tracking.py; pass them as arguments, not in tags=")

    model_run = experiment == MODEL
    model_toml = _read_config(config or MODEL_TOML, model_run,
                              "model runs need Model-Pipeline/model.toml (Contract 2)")
    meridian_toml = _read_config(meridian_config or MERIDIAN_TOML, not model_run,
                                 f"{experiment} runs need Data-Pipeline/meridian.toml")
    if block_run_id is None:
        if not model_run:
            raise ValueError(f"{experiment} runs need block_run_id: the --run-id they read")
        block_run_id = tomllib.loads(model_toml.decode("utf-8")).get("data", {}).get("run_id")
        if not block_run_id:
            raise ValueError(f"{config or MODEL_TOML} has no [data] run_id (Contract 2)")
    sha, dirty = git_state()
    out = {
        "owner": owner,
        "block_run_id": str(block_run_id),
        "git_sha": sha,
        "git_dirty": dirty,
        "config_sha256": hashlib.sha256(model_toml).hexdigest() if model_toml is not None else "missing",
        "meridian_config_sha256": hashlib.sha256(meridian_toml).hexdigest() if meridian_toml is not None
        else "missing",
    }
    if model_run or features is not None:
        path = Path(features or FEATURES)
        if not path.exists():
            raise FileNotFoundError(f"{path} not found: model runs need the feature table (Contract 1). "
                                    f"Pass features=<path> if it's somewhere else")
        out["features_md5"] = file_digest(path, "md5")
        _check_features(path, out["features_md5"], out["block_run_id"])
    if decision is not None:
        out["decision"] = decision
    return {**extra, **out}


# ---------- the store ----------
def tracking_uri():
    """$MLFLOW_TRACKING_URI if set, else the repo root's mlflow.db, the same file from any folder."""
    return os.environ.get("MLFLOW_TRACKING_URI") or f"sqlite:///{ROOT / 'mlflow.db'}"


def _artifact_location(uri, experiment):
    """mlruns/<experiment> next to a local SQLite store, not wherever the first run was started."""
    if uri.startswith("sqlite:///"):
        return (Path(uri[len("sqlite:///"):]).resolve().parent / "mlruns" / experiment).as_uri()
    return None  # a server picks its own


def _client():
    """MLflow's client on our store. mlflow is imported here, so importing this module stays fast."""
    import mlflow
    os.environ.setdefault("MLFLOW_TRACKING_URI", tracking_uri())
    return mlflow, mlflow.MlflowClient()


def experiment_id(name):
    """The id of a Contract 3 experiment, made on first use."""
    if name not in EXPERIMENTS:
        raise ValueError(f"experiment {name!r}: Contract 3 has {', '.join(EXPERIMENTS)}")
    mlflow, client = _client()
    key = (mlflow.get_tracking_uri(), name)
    with _lock:
        if key not in _experiment_ids:
            exp = client.get_experiment_by_name(name)
            if exp is None:
                try:
                    client.create_experiment(name, artifact_location=_artifact_location(key[0], name))
                except mlflow.exceptions.MlflowException:
                    pass  # another process made it first
                exp = client.get_experiment_by_name(name)
            _experiment_ids[key] = exp.experiment_id
        return _experiment_ids[key]


# ---------- runs ----------
def start_run(name, owner, *, experiment=MODEL, block_run_id=None, decision=None, nested=False, config=None,
              meridian_config=None, features=None, tags=None):
    """Start an MLflow run with the Contract 3 tags. Use instead of mlflow.start_run.

    name        the run name you'll see in `mlflow ui`
    owner       your packet name: rochan, rachna, chaitali, vishwa, utkarsh, aniket
    experiment  meridian-defect-risk (default), meridian-agents or meridian-auditor
    block_run_id  the block it reads. Model runs default to model.toml [data] run_id; others must pass it
    decision    <packet>-<n> for a decision test
    nested      a child of the run that's open, such as one Optuna trial
    config, meridian_config, features  other paths than the repo's model.toml, meridian.toml and
                data/features.parquet. Pass the ones your command was given, so the hashes match
    tags        any other tags
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("start_run needs a run name")
    all_tags = run_tags(owner, experiment, block_run_id=block_run_id, decision=decision, config=config,
                        meridian_config=meridian_config, features=features, tags=tags)
    eid = experiment_id(experiment)
    mlflow, _ = _client()
    return mlflow.start_run(experiment_id=eid, run_name=name, nested=nested, tags=all_tags)


# ---------- the command ----------
def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m model.tracking",
                                description="Print the tags a run would get here. Logs nothing.")
    p.add_argument("--owner", required=True, help="your packet name")
    p.add_argument("--experiment", default=MODEL, choices=EXPERIMENTS)
    p.add_argument("--block-run-id")
    p.add_argument("--config", help=f"default {MODEL_TOML.relative_to(ROOT)}")
    p.add_argument("--meridian-config", help=f"default {MERIDIAN_TOML.relative_to(ROOT)}")
    p.add_argument("--features", help=f"default {FEATURES.relative_to(ROOT)}")
    a = p.parse_args(argv)
    try:
        tags = run_tags(a.owner, a.experiment, block_run_id=a.block_run_id, config=a.config,
                        meridian_config=a.meridian_config, features=a.features)
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f"tracking: {e}")
    print(json.dumps({"tracking_uri": tracking_uri(), "experiment": a.experiment, "tags": tags}, indent=1))
    return tags


if __name__ == "__main__":
    main()
