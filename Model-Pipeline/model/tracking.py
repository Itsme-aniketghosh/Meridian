"""One way to log every run to MLflow (Contract 3 in Model-Pipeline/packets/README.md).

    import mlflow
    from model import tracking

    with tracking.start_run("lgbm-untuned", owner="rachna", decision="rachna-1"):
        mlflow.log_metric("val_pr_auc", 0.41)

    with tracking.start_run("writer", owner="vishwa", experiment="meridian-agents",
                            block_run_id="c5118b019d642a9b"):
        tracking.log_llm_call(agent="writer", model="small", prompt_version="v1", tokens_in=812,
                              tokens_out=96, cost_usd=0.0004, cache_hit=False, gate_pass=True)

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
import atexit
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
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
OWNERS = ("aniket", "chaitali", "rachna", "rochan", "utkarsh", "vishwa")  # one per packet folder
DECISION = re.compile(r"^([a-z]+)-[0-9]+$")  # <owner>-<n>: rochan-4

# log_llm_call
CALLS_FILE = "llm_calls.json"
CALL_COLUMNS = ("call", "at", "agent", "model", "prompt_version", "tokens_in", "tokens_out", "cost_usd",
                "cache_hit", "gate_pass", "route", "source", "task", "repo", "team", "latency_ms", "facts_hash")
SOURCES = {"codemod", "llm", "template", "rules"}  # Contract 5, suggestion.source
ROUTES = {"jev", "rules"}
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
FLUSH_EVERY = 500  # rows kept in memory before they're appended to the run's table

_lock = threading.RLock()
_experiment_ids = {}  # (tracking uri, experiment name) -> id
_agent_runs = set()  # meridian-agents runs started here and not ended yet
_pending = {}  # run id -> call rows not yet written
_totals = {}  # run id -> running totals, logged as metrics


# ---------- the tags ----------
@lru_cache(maxsize=1)
def git_state():
    """(sha, dirty) of the checkout, read once per process. Outside git (a Docker image): $MERIDIAN_GIT_SHA."""
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True,
                              timeout=30).stdout.strip()
    try:
        sha = git("rev-parse", "HEAD")
        # a change to a tracked file anywhere, or new code not yet in git: either way the sha doesn't hold it
        dirty = git("status", "--porcelain", "--untracked-files=no") or git(
            "status", "--porcelain", "--untracked-files=all", "--",
            ":(glob)Model-Pipeline/**/*.py", ":(glob)Data-Pipeline/meridian/**/*.py")
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
    if owner not in OWNERS:
        raise ValueError(f"owner {owner!r}: one of {', '.join(OWNERS)}")
    m = DECISION.match(decision) if isinstance(decision, str) else None
    if decision is not None and not (m and m.group(1) in OWNERS):
        raise ValueError(f"decision {decision!r}: <owner>-<n>, such as 'rochan-4'")
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
class Run:
    """What start_run returns. Use it in a `with`: on the way out it writes the run's LLM calls, then ends
    the run, FAILED if an exception is leaving. Anything else is passed to mlflow's ActiveRun."""

    def __init__(self, active):
        self._active = active

    def __getattr__(self, name):
        return getattr(self._active, name)

    def __enter__(self):
        return self._active

    def __exit__(self, exc_type, exc, tb):
        try:
            _close(self._active.info.run_id)
        except Exception:
            import mlflow
            mlflow.end_run("FAILED")  # its calls weren't written: the run must not read FINISHED
            raise
        self._active.__exit__(exc_type, exc, tb)
        return False


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
    active = mlflow.start_run(experiment_id=eid, run_name=name, nested=nested, tags=all_tags)
    if experiment == AGENTS:
        with _lock:
            _agent_runs.add(active.info.run_id)
    return Run(active)


def end_run(status="FINISHED"):
    """For a run started without `with`: write its LLM calls, then end it."""
    import mlflow
    active = mlflow.active_run()
    if active is not None:
        try:
            _close(active.info.run_id)
        except Exception:
            mlflow.end_run("FAILED")
            raise
    mlflow.end_run(status)


# ---------- LLM calls ----------
def _check_call(row):
    for k in ("agent", "model", "prompt_version"):
        if not isinstance(row[k], str) or not row[k].strip():
            raise ValueError(f"log_llm_call: {k} must be a non-empty string")
    for k in ("tokens_in", "tokens_out"):
        if isinstance(row[k], bool) or not isinstance(row[k], int) or row[k] < 0:
            raise ValueError(f"log_llm_call: {k} must be an int >= 0, got {row[k]!r}")
    for k in ("cost_usd", "latency_ms"):
        v = row[k]
        if k == "latency_ms" and v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ValueError(f"log_llm_call: {k} must be a number >= 0, got {v!r}")
    if not isinstance(row["cache_hit"], bool):
        raise ValueError(f"log_llm_call: cache_hit must be True or False, got {row['cache_hit']!r}")
    if row["gate_pass"] is not None and not isinstance(row["gate_pass"], bool):
        raise ValueError(f"log_llm_call: gate_pass must be True, False or None, got {row['gate_pass']!r}")
    if row["source"] is not None and row["source"] not in SOURCES:
        raise ValueError(f"log_llm_call: source {row['source']!r}: one of {sorted(SOURCES)} (Contract 5)")
    if row["route"] is not None and row["route"] not in ROUTES:
        raise ValueError(f"log_llm_call: route {row['route']!r}: one of {sorted(ROUTES)}")
    for k in ("task", "repo", "team", "facts_hash"):
        v = row[k]
        if v is not None and not isinstance(v, str):
            raise ValueError(f"log_llm_call: {k} must be a string, got {v!r}")
    for k in ("agent", "prompt_version", "task", "repo", "team"):  # not model: some model ids carry an @
        v = row[k]
        if v is not None and EMAIL.search(v):
            raise ValueError(f"log_llm_call: {k} looks like it holds an email. No names or emails leave the block")


def _agents_run(run_id):
    """The meridian-agents run a call belongs to: run_id if given, else this thread's active run, else the
    only one open in this process (worker threads don't see the main thread's active run)."""
    import mlflow
    with _lock:
        if run_id is not None:
            if run_id not in _agent_runs:
                raise ValueError(f"run {run_id} isn't an open meridian-agents run started with tracking.start_run")
            return run_id
        active = mlflow.active_run()
        if active is not None:
            if active.info.run_id in _agent_runs:
                return active.info.run_id
            raise ValueError("the active run isn't in meridian-agents: start one with "
                             "tracking.start_run(name, owner, experiment='meridian-agents', block_run_id=...)")
        if len(_agent_runs) == 1:
            return next(iter(_agent_runs))
        if not _agent_runs:
            raise RuntimeError("no meridian-agents run is open: start one with "
                               "tracking.start_run(name, owner, experiment='meridian-agents', block_run_id=...)")
        raise RuntimeError(f"{len(_agent_runs)} meridian-agents runs are open: pass run_id=")


def log_llm_call(*, agent, model, prompt_version, tokens_in, tokens_out, cost_usd, cache_hit, gate_pass,
                 route=None, source=None, task=None, repo=None, team=None, latency_ms=None, facts_hash=None,
                 run_id=None):
    """One LLM call, into the open meridian-agents run.

    agent           writer, jev, diff_drafter, ...
    model           the model id the call used (meridian.toml [llm])
    prompt_version  the prompt's version, the same one the cache key uses
    tokens_in, tokens_out, cost_usd   what the call cost. 0 for a cache hit
    cache_hit       True if the answer came from llm_cache/ and no call was made
    gate_pass       the fact gate's verdict; None if the output isn't gated (a Jev pick)
    route           jev or rules (Rachna's Jev); source: codemod, llm, template or rules (Contract 5)
    task, repo, team  what the call was for, for slicing. Never a person
    latency_ms      wall time of the call
    facts_hash      the suggestion row's facts_hash (Contract 5), so a call joins to what it produced
    run_id          the run, when calling from a worker thread with more than one agents run open

    Rows stay in memory and are written to the run's llm_calls.json table when the run ends (or every
    FLUSH_EVERY calls), with the totals as metrics. A call costs about 0.06 ms; one MLflow run per call
    cost 7.4 ms (packets/rochan-tracking/decisions.md). No prompt or response text is logged, by design:
    only counts and labels, so nothing a person wrote can leave the block through MLflow.
    """
    row = {"agent": agent, "model": model, "prompt_version": prompt_version, "tokens_in": tokens_in,
           "tokens_out": tokens_out, "cost_usd": cost_usd, "cache_hit": cache_hit, "gate_pass": gate_pass,
           "route": route, "source": source, "task": task, "repo": repo, "team": team, "latency_ms": latency_ms,
           "facts_hash": facts_hash}
    _check_call(row)
    with _lock:
        rid = _agents_run(run_id)
        t = _totals.setdefault(rid, {"calls": 0, "api_calls": 0, "gate_checked": 0, "gate_passed": 0,
                                     "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0})
        t["calls"] += 1
        t["api_calls"] += not cache_hit
        t["gate_checked"] += gate_pass is not None
        t["gate_passed"] += gate_pass is True
        t["tokens_in"] += tokens_in
        t["tokens_out"] += tokens_out
        t["cost_usd"] += float(cost_usd)
        row["call"] = t["calls"]
        row["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rows = _pending.setdefault(rid, [])
        rows.append(row)
        if len(rows) >= FLUSH_EVERY:
            _flush(rid)


def _flush(run_id):
    """Append the run's pending rows to llm_calls.json and log its totals. Called with _lock held."""
    rows = _pending.get(run_id)
    if not rows:
        return
    from mlflow.entities import Metric
    _, client = _client()
    client.log_table(run_id, {c: [r[c] for r in rows] for c in CALL_COLUMNS}, CALLS_FILE)
    del _pending[run_id]  # only once written: a failed write keeps them for the next try
    t = _totals[run_id]
    values = {"llm_calls": t["calls"], "llm_api_calls": t["api_calls"],
              "cache_hit_rate": (t["calls"] - t["api_calls"]) / t["calls"],
              "gate_checked": t["gate_checked"], "tokens_in": t["tokens_in"], "tokens_out": t["tokens_out"],
              "cost_usd": t["cost_usd"]}
    if t["gate_checked"]:
        values["gate_pass_rate"] = t["gate_passed"] / t["gate_checked"]
    now = int(time.time() * 1000)
    client.log_batch(run_id, metrics=[Metric(k, float(v), now, t["calls"]) for k, v in values.items()])


def _close(run_id):
    with _lock:
        _agent_runs.discard(run_id)  # no more calls go to it
        _flush(run_id)  # if this raises, the rows and totals stay for _flush_all's retry at exit
        _totals.pop(run_id, None)


@atexit.register
def _flush_all():
    """A run ended with mlflow.end_run() instead of `with` still keeps its calls."""
    with _lock:
        for rid in list(_pending):
            try:
                _flush(rid)
            except Exception as e:  # the interpreter is going away: say so, don't raise
                print(f"tracking: couldn't write the LLM calls of run {rid}: {e}", file=sys.stderr)


def load_llm_calls(run_ids=None):
    """Every logged LLM call as a DataFrame with a run_id column, for slicing by agent, repo or team."""
    _, client = _client()
    return client.load_table(experiment_id(AGENTS), CALLS_FILE, run_ids=run_ids, extra_columns=["run_id"])


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
