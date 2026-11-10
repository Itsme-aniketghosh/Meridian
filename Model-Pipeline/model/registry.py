"""Registry (step G, Rochan): where models live (packets/rochan-tracking, section G).

    python -m model.registry                       # register reports/'s candidate; promote it if it wins
    python -m model.registry --push                # and, on promotion, upload it to Artifact Registry
    python -m model.registry --rollback [--push]   # champion back to previous
    python -m model.registry --decide 6 7          # decisions.md 6 and 7

Register. The candidate's model file (candidate.json's model_file, next to it, with the md5 train.py recorded) becomes
a new version of [registry] name (`defect-risk`) in the local MLflow registry, filed under the git sha of the code
(outside git, a Docker image: its GIT_SHA build arg; with neither, nothing is registered). The same model on the same
table isn't registered twice, unless it would now be promoted. Its run holds the model, metrics.json,
the bias report, seed noise, candidate.json and the champion record the version would get, with Contract 3's tags.

The champion it's judged against is registry/champion.json's when that file is there, else the `champion` alias's.
The file is in git (PLAN §8), so a new MLflow store, as on CI, still knows the champion instead of judging every
candidate a first model.

Promote (decision 6). The version takes `champion` only if bias_report.json passes and its val PR-AUC beats the
champion's by more than the margin: [registry] noise_sds standard deviations of seed noise (rule "noise"), or any
gain (rule "any"). The first model only needs the bias report. A version that isn't promoted stays registered, with a
`reason` tag that says why. On promotion, `previous` moves to the old champion and registry/champion.json is
written. CI calls this only after gate.py passes.

What the champion's PR-AUC is (decision 7): [registry] compare "rescore" re-scores the champion's own model on today's
val rows when the table changed (its features_md5 isn't today's), since a stored number describes other rows. Its
model comes from its run in this store, else next to champion.json, else Artifact Registry. On the same table, and
always with "stored", it's the number stored when the champion was registered.

Push. --push makes Artifact Registry ([registry] project, location, repository) hold the champion in force, whether
this run promoted it or an earlier one did without --push: its model file and champion.json in package [registry]
package, version <git sha> (`gcloud artifacts generic upload`), then a version of package [registry] pointer holding
its champion.json. The pointer's newest version is the champion in force: every promotion and rollback adds one.
Only what isn't there yet is sent, so a retry, or a rerun on the same commit, finishes an earlier push or sends
nothing. A file there that differs from this one stops it before anything is sent: a version is one model. Without
[registry] project, gcloud or a signed-in account, --push skips and says so. A model registered from uncommitted code
isn't uploaded, because the version has to name the code, and neither is one registered in a Docker image, where git
can't tell. Local work never needs GCP.

Rollback. `champion` goes back to `previous`, `previous` to the champion before that, and registry/champion.json is
the restored version's record again. With --push, the restored version is uploaded too if it never was, then its
record as the new pointer. A store that doesn't hold the previous version (a new one, as on CI) can't roll back.

registry/champion.json (Contract 4) is written only by registry.py. Each key, and who reads it:

    version     the Artifact Registry version: the git sha of the commit that made the model (section G)
    run_id      the champion's MLflow run. Its Contract 3 tags say which code, config and table made the model
    model_file  the model's file name, next to champion.json here and in the Artifact Registry version: what P9 loads
    model_md5   P9 scores with the champion only when the file's md5 matches. Otherwise lines x files (rachna-models)
    metrics     the champion's block from its metrics.json, as is, plus `split` and `features_md5`. The gate compares
                a new candidate with these numbers, and decision 7 needs to know which table they're on. P9's alert
                threshold is `metrics.at_threshold.threshold`, chosen on val (decision 3)
    previous    the champion before this one, {version, run_id}, or null for the first model: what --rollback restores

tests/contracts/champion.json is an example built by champion_record from a run on tests/fake_table.py.
"""
import argparse
import calendar
import itertools
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]  # Model-Pipeline/
CHAMPION = HERE / "registry" / "champion.json"

CHAMPION_KEYS = ("version", "run_id", "model_file", "model_md5", "metrics", "previous")
HEX = {"version": 40, "run_id": 32, "model_md5": 32}  # a git sha, an MLflow run id, an md5
# What registry.py reads from each file the other steps write (Contract 4)
METRICS_KEYS = ("split", "n", "positive_rate", "candidate", "features_md5", "models")  # evaluate.py (Rochan)
BIAS_KEYS = ("split", "candidate", "n", "pass")  # bias.py (Chaitali)
SEED_NOISE_KEYS = ("kind", "split", "n", "mean", "sd")  # train.py --seed-noise (Rachna)


class ContractError(ValueError):
    """A file another step wrote is missing something the registry reads (Contract 4). The message says what."""


def _need(data, keys, where):
    if not isinstance(data, dict):
        raise ContractError(f"{where}: not a JSON object")
    missing = [k for k in keys if k not in data]
    if missing:
        raise ContractError(f"{where}: no {', '.join(missing)} (Contract 4)")
    return data


def _load(path):
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ContractError(f"{path}: not found") from None
    except json.JSONDecodeError as e:
        raise ContractError(f"{path}: not JSON ({e})") from None


def candidate_block(metrics, where="metrics.json"):
    """The candidate's entry in metrics.json's "models", with what the registry and P9 read from it."""
    column = _need(metrics["candidate"], ("column",), f"{where} candidate")["column"]
    if column not in metrics["models"]:
        raise ContractError(f"{where}: the candidate {column!r} isn't in models ({', '.join(metrics['models'])})")
    block = _need(metrics["models"][column], ("pr_auc", "at_threshold"), f"{where} models.{column}")
    _need(block["pr_auc"], ("value", "interval", "n"), f"{where} models.{column}.pr_auc")
    _need(block["at_threshold"], ("threshold", "rule"), f"{where} models.{column}.at_threshold")
    return block


def read_metrics(path):
    """evaluate.py's metrics.json (Rochan): the split, its N, the candidate, and every model's numbers."""
    data = _need(_load(path), METRICS_KEYS, str(path))
    candidate_block(data, str(path))
    return data


def read_bias(path):
    """bias.py's bias_report.json (Chaitali): which candidate, on which split, with N, and whether it passed."""
    data = _need(_load(path), BIAS_KEYS, str(path))
    if not isinstance(data["pass"], bool):
        raise ContractError(f"{path}: pass is {data['pass']!r}, not true or false")
    return data


def read_seed_noise(path):
    """train.py's seed_noise.json (Rachna): the chosen config's val PR-AUC over seeds, its mean and sd (decision 6)."""
    data = _need(_load(path), SEED_NOISE_KEYS, str(path))
    sd = data["sd"]
    if isinstance(sd, bool) or not isinstance(sd, (int, float)) or sd < 0:
        raise ContractError(f"{path}: sd is {sd!r}, not a number at or above 0")
    return data


def check_champion(record, where="champion.json"):
    """A champion record has every key, in the right form. Returns it unchanged."""
    _need(record, CHAMPION_KEYS, where)
    for key, n in HEX.items():
        if not (isinstance(record[key], str) and re.fullmatch(f"[0-9a-f]{{{n}}}", record[key])):
            raise ContractError(f"{where}: {key} is {record[key]!r}, not {n} hex characters")
    name = record["model_file"]
    if not (isinstance(name, str) and name and "/" not in name and "\\" not in name):
        raise ContractError(f"{where}: model_file is {name!r}, not a file name next to champion.json")
    metrics = _need(record["metrics"], ("split", "features_md5", "pr_auc", "at_threshold"), f"{where} metrics")
    _need(metrics["pr_auc"], ("value", "n"), f"{where} metrics.pr_auc")
    _need(metrics["at_threshold"], ("threshold", "rule"), f"{where} metrics.at_threshold")
    if record["previous"] is not None:
        _need(record["previous"], ("version", "run_id"), f"{where} previous")
    return record


def champion_record(metrics, run_id, version, model_file, model_md5, previous=None):
    """registry/champion.json for a promoted model, from its metrics.json (a dict). previous: the champion record it
    replaces, or None for the first model."""
    block = candidate_block(_need(metrics, METRICS_KEYS, "metrics"))
    return check_champion({
        "version": version, "run_id": run_id, "model_file": model_file, "model_md5": model_md5,
        "metrics": {"split": metrics["split"], "features_md5": metrics["features_md5"], **block},
        "previous": None if previous is None else {"version": previous["version"], "run_id": previous["run_id"]}})


def read_champion(path=CHAMPION):
    """registry/champion.json, checked."""
    return check_champion(_load(path), str(path))


REPORTS = HERE / "reports"
SETTINGS = {"name": "defect-risk", "rule": "noise", "noise_sds": 2.0, "compare": "rescore", "project": "",
            "location": "us-east1", "repository": "meridian-models", "package": "defect-risk", "pointer": "champion"}
CHOICES = {"rule": ("noise", "any"), "compare": ("rescore", "stored")}


def settings(cfg):
    """model.toml [registry] over SETTINGS. A key the registry doesn't read, or an unknown rule, stops the run."""
    given = cfg.get("registry", {})
    unknown = sorted(set(given) - set(SETTINGS))
    if unknown:
        raise SystemExit(f"model.toml [registry] has keys the registry doesn't read: {', '.join(unknown)}")
    s = {**SETTINGS, **given}
    for key, allowed in CHOICES.items():
        if s[key] not in allowed:
            raise SystemExit(f"model.toml [registry] {key} = {s[key]!r}: one of {', '.join(allowed)}")
    return s


def margin(s, sd):
    """Decision 6: how much better than the champion a candidate's val PR-AUC must be."""
    return 0.0 if s["rule"] == "any" else s["noise_sds"] * sd


def verdict(candidate, champion, bias_pass, need):
    """(promote, reason) for a candidate's val PR-AUC against the champion's. champion None: there's none yet."""
    if not bias_pass:
        return False, "its bias report didn't pass"
    if champion is None:
        return True, "the first model, and its bias report passes"
    gain = candidate - champion
    if gain > need:
        return True, f"val PR-AUC {gain:+.4f} over the champion, more than the margin {need:.4f}"
    if gain > 0:
        return False, f"val PR-AUC {gain:+.4f} over the champion, within the margin {need:.4f}: seed noise"
    return False, f"val PR-AUC {gain:+.4f}: no better than the champion"


def read_candidate(path):
    """candidate.json with the model file train.py saved next to it, checked against the md5 it recorded."""
    from model.evaluate import ScoresError, file_md5, read_candidate as evaluate_read
    if not Path(path).exists():
        raise ContractError(f"{path}: not found")
    try:
        cand = evaluate_read(path)
    except (ScoresError, json.JSONDecodeError) as e:
        raise ContractError(str(e)) from None
    _need(cand, ("kind", "column", "model_file", "model_md5"), str(path))
    model = Path(path).parent / cand["model_file"]
    if not model.exists():
        raise ContractError(f"{model}: no model file. Run python -m model.train --save-model first")
    if file_md5(model) != cand["model_md5"]:
        raise ContractError(f"{model}: md5 {file_md5(model)} isn't the {cand['model_md5']} candidate.json records: "
                            "not the model train.py judged")
    return cand, model


def read_inputs(paths):
    """Every file registering reads, checked against each other: the same candidate column, val, these scores."""
    from model.evaluate import file_md5
    cand, model = read_candidate(paths["candidate"])
    metrics, bias, noise = read_metrics(paths["metrics"]), read_bias(paths["bias"]), read_seed_noise(paths["noise"])
    column = cand["column"]
    if metrics["candidate"]["column"] != column or bias["candidate"] != column:
        raise ContractError(f"candidate.json is {column!r}, metrics.json {metrics['candidate']['column']!r}, "
                            f"bias_report.json {bias['candidate']!r}: run evaluate and bias on this candidate")
    if metrics["split"] != "val" or bias["split"] != "val":
        raise ContractError("the registry compares on val only: 2024 is scored once, on Nov 12")
    if "scores_md5" in metrics and metrics["scores_md5"] != file_md5(cand["scores_file"]):
        raise ContractError(f"metrics.json was made from other scores than {cand['scores_file']}: run evaluate again")
    return cand, model, metrics, bias, noise


# The MLflow side. mlflow is imported only here, so the readers stay fast


def _client():
    from model import tracking
    return tracking._client()


def alias(client, name, which="champion"):
    """The model version an alias points at, or None when the model or the alias doesn't exist. Any other error, a
    store that can't be read, stops the run: taken as "no champion", it would promote a first model."""
    from mlflow.exceptions import MlflowException
    try:
        return client.get_model_version_by_alias(name, which)
    except MlflowException as e:
        if e.error_code == "RESOURCE_DOES_NOT_EXIST" or (e.error_code == "INVALID_PARAMETER_VALUE"
                                                         and "not found" in str(e)):
            return None
        raise


def stored_record(client, version):
    """The champion record kept in a version's run."""
    import mlflow
    with tempfile.TemporaryDirectory() as d:
        path = mlflow.artifacts.download_artifacts(run_id=version.run_id, artifact_path="champion.json", dst_path=d)
        return json.loads(Path(path).read_text(encoding="utf-8"))


def local_version(client, name, run_id):
    """The version of `name` this store registered from MLflow run `run_id`, or None."""
    found = client.search_model_versions(f"name = '{name}' and run_id = '{run_id}'")
    return found[0] if found else None


def champion_now(client, name, champion_path):
    """The champion in force: (its champion.json record, its version in this store or None). registry/champion.json
    when it's there: it's in git (PLAN §8), so a new store, as on CI, still knows the champion. Else the `champion`
    alias's record. (None, None): there's no champion yet."""
    version = alias(client, name)
    if Path(champion_path).exists():
        record = read_champion(champion_path)
        if version is None or version.run_id != record["run_id"]:
            version = local_version(client, name, record["run_id"])
        return record, version
    if version is None:
        return None, None
    return stored_record(client, version), version


def champion_model(record, version, champion_path, dest):
    """The champion's model file, checked against its md5: from its run in this store (into `dest`), else the one
    next to champion.json. None when neither has it."""
    from model.evaluate import file_md5
    places = [Path(champion_path).parent / record["model_file"]]
    if version is not None:
        import mlflow
        places.insert(0, Path(mlflow.artifacts.download_artifacts(
            run_id=version.run_id, artifact_path=f"model/{record['model_file']}", dst_path=str(dest))))
    return next((p for p in places if p.exists() and file_md5(p) == record["model_md5"]), None)


def rescore(model_path, features):
    """Decision 7: a model file scored on today's val rows. (PR-AUC, N)."""
    import joblib
    from model import train
    model = joblib.load(model_path)
    val = train.rows(train.load_features(features), "val")
    return float(train.pr_auc(val["label"].to_numpy(), train.score_with(model, val))), len(val)


def champion_pr_auc(record, version, s, features_md5, features, champion_path, runner=subprocess.run):
    """The champion's val PR-AUC to beat, and how it was got (decision 7). Re-scoring needs the champion's model: from
    its run in this store, next to champion.json, or Artifact Registry. It stops when none has it."""
    stored = float(record["metrics"]["pr_auc"]["value"])
    if s["compare"] == "stored":
        return stored, "stored"
    if record["metrics"]["features_md5"] == features_md5:
        return stored, "stored: same table"
    with tempfile.TemporaryDirectory() as d:
        model = champion_model(record, version, champion_path, d)
        if model is None and gcp_missing(s, runner) is None:
            model = download(s, s["package"], record["version"], record["model_file"], Path(d) / "gcp", runner,
                             record["model_md5"])
        if model is None:
            raise SystemExit(f"model.registry: the champion ({record['version'][:12]}) was judged on another table, "
                             f"so decision 7 re-scores its model on today's val, and its {record['model_file']} "
                             f"isn't in this MLflow store, next to {champion_path} or on Artifact Registry")
        value, n = rescore(model, features)
    return value, f"re-scored on today's val (N = {n:,}): the table changed"


def git_version():
    """(sha, dirty) a version is filed under: Contract 4's version is the git sha of the code that made the model.
    Outside git (a Docker image) tracking reads $MERIDIAN_GIT_SHA, the image's GIT_SHA build arg. Without a sha it
    stops, before anything is registered."""
    from model import tracking
    sha, dirty = tracking.git_state()
    if not re.fullmatch("[0-9a-f]{40}", sha or ""):
        raise SystemExit(f"model.registry: a version is the git sha of the code that made the model, and here it's "
                         f"{sha!r}: register from a git checkout, or build the image with "
                         "--build-arg GIT_SHA=$(git rev-parse HEAD)")
    return sha, dirty


def champion_bytes(record):
    """champion.json as bytes, the same on every OS: write_text would end lines '\r\n' on Windows, so the md5 --push
    compares with Artifact Registry's would differ for the same record."""
    return (json.dumps(record, indent=2) + "\n").encode("utf-8")


def write_champion(record, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(champion_bytes(record))
    os.replace(tmp, path)


def register(paths, s, config, features, champion_path=CHAMPION, push=False, runner=subprocess.run, say=print):
    """Register reports/'s candidate and promote it if it wins. With push, Artifact Registry then holds the champion
    in force, whether or not this run promoted it. Returns the version's tags."""
    import mlflow
    from model import tracking
    cand, model, metrics, bias, noise = read_inputs(paths)
    block = candidate_block(metrics)
    need = margin(s, noise["sd"])
    sha, dirty = git_version()
    _, client = _client()
    name = s["name"]
    if not client.search_registered_models(filter_string=f"name = '{name}'"):
        client.create_registered_model(name)
    record, champion = champion_now(client, name, champion_path)
    table = metrics["features_md5"]
    if record is not None and (record["model_md5"], record["metrics"]["features_md5"]) == (cand["model_md5"], table):
        held = f"version {champion.version}" if champion else f"champion.json, {record['version'][:12]}"
        say(f"this model on this table is {name}'s champion already ({held}), so it isn't registered again")
        if push:
            pushed(record, s, runner, say, model, champion.tags.get("git_dirty", "unknown") if champion else None)
        return {**(champion.tags if champion else {}), "version": champion.version if champion else None,
                "promoted": "false", "reason": "it's the champion"}
    against, how = (None, "no champion yet") if record is None else champion_pr_auc(
        record, champion, s, table, features, champion_path, runner)
    promote, reason = verdict(block["pr_auc"]["value"], against, bias["pass"], need)
    same = next((v for v in client.search_model_versions(f"name = '{name}'")
                 if v.tags.get("model_md5") == cand["model_md5"] and v.tags.get("features_md5") == table), None)
    if same is not None and not promote:
        say(f"{name} version {same.version} is this model on this table already, so it isn't registered again: "
            f"{reason}")
        if push:
            keep(record, champion, s, champion_path, runner, say)
        return {**same.tags, "version": same.version, "promoted": "false", "reason": reason}

    with tracking.start_run(f"registry-{cand['column']}", owner="rochan", config=config, features=features) as run:
        mlflow.log_artifact(str(model), artifact_path="model")
        for key in ("metrics", "bias", "noise", "candidate"):
            mlflow.log_artifact(str(paths[key]))
        new = champion_record(metrics, run.info.run_id, sha, cand["model_file"], cand["model_md5"], record)
        mlflow.log_dict(new, "champion.json")
        mlflow.log_metrics({"val_pr_auc": block["pr_auc"]["value"], "margin": need})
        artifacts = run.info.artifact_uri
    tags = {"pr_auc": repr(block["pr_auc"]["value"]), "features_md5": table,
            "model_md5": cand["model_md5"], "model_file": cand["model_file"], "git_sha": sha, "git_dirty": dirty,
            "promoted": str(promote).lower(), "reason": reason, "compared": how,
            "champion_pr_auc": "" if against is None else repr(against)}
    version = client.create_model_version(name, source=f"{artifacts}/model", run_id=new["run_id"], tags=tags)
    say(f"registered {name} version {version.version} ({cand['kind']} {cand['column']}, val PR-AUC "
        f"{block['pr_auc']['value']:.4f}, N = {block['pr_auc']['n']:,}). Champion: {how}")
    if not promote:
        say(f"not promoted: {reason}")
        if push:
            keep(record, champion, s, champion_path, runner, say)
        return {**tags, "version": version.version}
    if champion is not None:
        client.set_registered_model_alias(name, "previous", champion.version)
    elif alias(client, name, "previous") is not None:
        client.delete_registered_model_alias(name, "previous")  # the champion it replaces isn't in this store
    client.set_registered_model_alias(name, "champion", version.version)
    write_champion(new, champion_path)
    before = (champion.version if champion else f"{record['version'][:12]} (champion.json's; not in this store)"
              if record else "none")
    say(f"promoted to champion: {reason}. previous = {before}; wrote {champion_path}")
    if push:
        pushed(new, s, runner, say, model, dirty)
    return {**tags, "version": version.version}


def keep(record, version, s, champion_path, runner, say):
    """--push when this run promoted nothing: Artifact Registry still gets the champion in force if it doesn't hold it
    yet, as when an earlier run promoted it without --push."""
    if record is None:
        say("--push: there's no champion yet, so nothing to upload")
        return False
    with tempfile.TemporaryDirectory() as d:
        model = champion_model(record, version, champion_path, d)
        return pushed(record, s, runner, say, model, version.tags.get("git_dirty", "unknown") if version else None)


def rollback(s, champion_path=CHAMPION, push=False, runner=subprocess.run, say=print):
    """`champion` back on `previous`, `previous` on the one before that, champion.json the restored record."""
    _, client = _client()
    name = s["name"]
    champion, previous = alias(client, name), alias(client, name, "previous")
    if previous is None:
        now = read_champion(champion_path) if Path(champion_path).exists() else None
        if now and now["previous"]:
            raise SystemExit(f"{name}: champion.json's previous is {now['previous']['version'][:12]}, and this MLflow "
                             "store doesn't hold it: roll back where it was registered")
        raise SystemExit(f"{name} has no previous version to roll back to")
    record = stored_record(client, previous)
    client.set_registered_model_alias(name, "champion", previous.version)
    before = record["previous"] and client.search_model_versions(
        f"name = '{name}' and run_id = '{record['previous']['run_id']}'")
    if before:
        client.set_registered_model_alias(name, "previous", before[0].version)
    else:
        client.delete_registered_model_alias(name, "previous")
    if champion is not None:
        client.set_model_version_tag(name, champion.version, "rolled_back",
                                     f"champion went back to version {previous.version}")
    write_champion(record, champion_path)
    say(f"rolled back: champion = version {previous.version} ({record['version'][:12]}), previous = "
        f"{before[0].version if before else 'none'}; wrote {champion_path}")
    if push:
        with tempfile.TemporaryDirectory() as d:
            pushed(record, s, runner, say, champion_model(record, previous, champion_path, d),
                   previous.tags.get("git_dirty", "unknown"))
    return record


# The Artifact Registry push


def gcloud(runner, *args):
    """gcloud by the path shutil.which finds: on Windows that's gcloud.cmd, which Python can't start by bare name."""
    return runner([shutil.which("gcloud") or "gcloud", *args], capture_output=True, text=True)


def repo_args(s):
    return [f"--project={s['project']}", f"--location={s['location']}", f"--repository={s['repository']}"]


def gcp_missing(s, runner=subprocess.run):
    """Why --push can't upload from here, or None."""
    if not s["project"]:
        return "model.toml [registry] project isn't set"
    if not shutil.which("gcloud"):
        return "gcloud isn't installed"
    r = gcloud(runner, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)")
    if r.returncode or not r.stdout.strip():
        return "no gcloud account is signed in"
    return None


def upload(s, package, version, files, runner=subprocess.run):
    """One `gcloud artifacts generic upload` per file. Stops on the first failure, with gcloud's message."""
    for f in files:
        args = ["artifacts", "generic", "upload", *repo_args(s), f"--package={package}", f"--version={version}",
                f"--source={f}"]
        r = gcloud(runner, *args)
        if r.returncode:
            raise SystemExit(f"upload failed: gcloud {' '.join(args)}\n{(r.stderr or r.stdout).strip()}")


def gcp_files(s, package, version, runner=subprocess.run):
    """{file name: md5} of one Artifact Registry version: empty when it isn't there (a package with no such version,
    or no such package, lists nothing)."""
    r = gcloud(runner, "artifacts", "files", "list", *repo_args(s), f"--package={package}", f"--version={version}",
               "--format=json")
    if r.returncode:
        raise SystemExit(f"--push stopped: listing {package} {version[:12]} failed\n{(r.stderr or r.stdout).strip()}")
    files = {}
    for f in json.loads(r.stdout or "[]"):  # name: projects/.../files/<package>:<version>:<file>
        md5 = [h["value"].lower() for h in f.get("hashes", []) if h.get("type") == "MD5"]
        files[f["name"].rsplit(":", 1)[-1]] = md5[0] if md5 else None
    return files


def download(s, package, version, name, dest, runner=subprocess.run, md5=None):
    """One file of an Artifact Registry version into `dest`: its path, or None when it isn't there (or, given md5,
    isn't that file)."""
    from model.evaluate import file_md5
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    r = gcloud(runner, "artifacts", "generic", "download", *repo_args(s), f"--package={package}",
               f"--version={version}", f"--destination={dest}", f"--name={name}")
    path = dest / name
    if r.returncode or not path.exists() or (md5 and file_md5(path) != md5):
        return None
    return path


def newest_pointer(s, runner, dest):
    """(version, record): the pointer package's newest version and the champion.json it holds, the champion in force
    on Artifact Registry. (None, None) before the first push."""
    r = gcloud(runner, "artifacts", "versions", "list", *repo_args(s), f"--package={s['pointer']}",
               "--format=value(name.basename())")
    if r.returncode:
        if "NOT_FOUND" in r.stderr or "does not exist" in r.stderr:
            return None, None
        raise SystemExit(f"--push stopped: listing {s['pointer']}'s versions failed\n{(r.stderr or r.stdout).strip()}")
    versions = sorted(r.stdout.split())
    if not versions:
        return None, None
    path = download(s, s["pointer"], versions[-1], "champion.json", dest, runner)
    if path is None:
        raise SystemExit(f"--push stopped: {s['pointer']} {versions[-1]} has no champion.json to read")
    return versions[-1], json.loads(path.read_text(encoding="utf-8"))


POINTER = "%Y%m%dt%H%M%Sz"


def pointer_version(after=None):
    """The pointer package's versions sort by time: the newest is the champion in force. Always later than `after`,
    the newest there now, even within the same second or from a machine whose clock is behind."""
    stamp = time.strftime(POINTER, time.gmtime())
    if after and stamp <= after:
        try:
            stamp = time.strftime(POINTER, time.gmtime(calendar.timegm(time.strptime(after, POINTER)) + 1))
        except ValueError:  # a version name not made here: the clock's is fine
            pass
    return stamp


def pushed(record, s, runner, say, model=None, dirty="false"):
    """Make Artifact Registry hold `record`: its version's model file and champion.json, then the pointer's newest
    version naming it. Only what isn't there yet is sent, and everything is checked before anything is sent. model:
    the champion's model file here, or None. dirty: git_dirty when the model was registered, None when another store
    registered it. False if it skipped."""
    why = gcp_missing(s, runner)
    if why:
        say(f"--push skipped: {why}. The registry and champion.json here are done; nothing went to GCP")
        return False
    from model.evaluate import file_md5
    package, version = s["package"], record["version"]
    where = f"{s['location']}/{s['repository']} in {s['project']}"
    there = gcp_files(s, package, version, runner)
    with tempfile.TemporaryDirectory() as d:
        champion = Path(d) / "champion.json"
        champion.write_bytes(champion_bytes(record))
        send = []
        for name, md5, path in ((record["model_file"], record["model_md5"], model),
                                ("champion.json", file_md5(champion), champion)):
            if name in there:
                if there[name] != md5:
                    raise SystemExit(f"--push stopped: {package} {version[:12]} on {where} has another {name} (md5 "
                                     f"{there[name]}; this one's is {md5}). A version is one model and its record, so "
                                     "nothing was uploaded")
            elif path is None:
                raise SystemExit(f"--push stopped: {package} {version[:12]} isn't on {where}, and its {name} isn't "
                                 "on this machine: push from the one that registered it")
            else:
                send.append(path)
        if record["model_file"] not in there and dirty != "false":
            raise SystemExit("--push needs committed code: the Artifact Registry version is the git sha, and " + {
                "true": "this model was registered with uncommitted changes, which aren't in it",
                None: "another MLflow store registered this model, so this one can't tell whether its code was "
                      "committed: push from the machine that registered it"}.get(
                dirty, "git couldn't tell whether the code that made this model was committed (a Docker image has "
                       "no .git): register and push from a git checkout"))
        upload(s, package, version, send, runner)
        newest, current = newest_pointer(s, runner, Path(d) / "pointer")
        stamp = None if current == record else pointer_version(newest)
        if stamp:
            upload(s, s["pointer"], stamp, [champion], runner)
    sent = (f"{package} {version[:12]} ({' and '.join(p.name for p in send)})" if send
            else f"{package} {version[:12]} was there already")
    tail = f"{s['pointer']} {stamp}" if stamp else f"and {s['pointer']} {newest} names it already: nothing new to send"
    say(f"pushed to {where}: {sent}, {tail}")
    return True


# Decisions 6 and 7 (Model-Pipeline/packets/rochan-tracking/decisions.md)


def moves(values, need):
    """How often champion moves when same-config models arrive in this order: the first is promoted, then each one
    goes through verdict() against the champion."""
    champion, n = values[0], 0
    for v in values[1:]:
        if verdict(v, champion, True, need)[0]:
            champion, n = v, n + 1
    return n


def decide_6(noise, s):
    """{rule: {margin, in seed order, orders with a move, mean moves over all orders}} on seed_noise.json."""
    values, out = noise["pr_auc"], {}
    for rule in CHOICES["rule"]:
        need = margin({**s, "rule": rule}, noise["sd"])
        every = [moves(list(order), need) for order in itertools.permutations(values)]
        out[rule] = {"margin": need, "seed_order": moves(values, need), "orders": len(every),
                     "orders_moved": sum(m > 0 for m in every), "mean_moves": statistics.mean(every)}
    return out


def pick_6(res):
    """decisions.md 6: the rule that moves champion 0 times in seed order; both: the noise rule."""
    zero = [rule for rule in ("noise", "any") if res[rule]["seed_order"] == 0]
    return zero[0] if zero else None


def build_table(config_text, out, published):
    """Utkarsh's data.py on a config given as text: the feature table it writes."""
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "model.toml"
        cfg.write_text(config_text, encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "model.data", "--config", str(cfg), "--published", str(published),
                            "--out", str(out)], cwd=HERE, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"data.py failed:\n{(r.stderr or r.stdout)[-2000:]}")
    return Path(out) / "features.parquet"


def decide_7(config, published, candidate, noise, s, window=180):
    """Build the table from the block in `published` as configured and with window_days = `window`; fit the
    candidate's config on the first; its stored val PR-AUC there against its re-scored one on the second."""
    from model import train
    from model.evaluate import file_md5
    text = Path(config).read_text(encoding="utf-8")
    run_id = json.loads((Path(published) / "latest.json").read_text(encoding="utf-8"))["file"].split(".")[0]
    base = re.sub(r'(?m)^run_id = "[0-9a-f]+"', f'run_id = "{run_id}"', text)
    changed = re.sub(r"(?m)^window_days = \d+", f"window_days = {window}", base)
    out = {"block": run_id, "window": window}
    with tempfile.TemporaryDirectory() as d:
        t0 = time.perf_counter()
        a = build_table(base, Path(d) / "a", published)
        b = build_table(changed, Path(d) / "b", published)
        out["build_s"] = round(time.perf_counter() - t0, 1)
        out["features_md5"] = [file_md5(a), file_md5(b)]
        da, db = train.load_features(a), train.load_features(b)
        model, scores, val_a = train.fit_and_score(da, candidate["kind"], int(candidate["seed"]), candidate["params"])
        val_b = train.rows(db, "val")
        out["stored"] = float(train.pr_auc(val_a["label"].to_numpy(), scores))
        out["rescored"] = float(train.pr_auc(val_b["label"].to_numpy(), train.score_with(model, val_b)))
        for key, val in (("a", val_a), ("b", val_b)):
            out[f"val_n_{key}"], out[f"val_positives_{key}"] = len(val), int(val["label"].sum())
    out["gap"] = abs(out["stored"] - out["rescored"])
    out["margin"] = margin({**s, "rule": "noise"}, noise["sd"])
    return out


def pick_7(res):
    """decisions.md 7: re-score when the table changed if the gap is over the margin; else stored."""
    return "rescore" if res["gap"] > res["margin"] else "stored"


def log_decision(n, metrics, picked, config, features):
    from model import tracking
    import mlflow
    with tracking.start_run(f"decision-{n}", owner="rochan", decision=f"rochan-{n}", config=config,
                            features=features):
        mlflow.log_param("picked", picked)
        mlflow.log_metrics({k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--candidate", default=str(REPORTS / "candidate.json"))
    p.add_argument("--metrics", default=str(REPORTS / "metrics.json"))
    p.add_argument("--bias", default=str(REPORTS / "bias_report.json"))
    p.add_argument("--seed-noise", default=str(REPORTS / "seed_noise.json"))
    p.add_argument("--config", default=str(HERE / "model.toml"))
    p.add_argument("--features", default=str(HERE / "data" / "features.parquet"))
    p.add_argument("--champion", default=str(CHAMPION), help="where champion.json is written")
    p.add_argument("--push", action="store_true", help="upload to Artifact Registry on promotion or rollback")
    p.add_argument("--rollback", action="store_true", help="put champion back on previous")
    p.add_argument("--decide", type=int, nargs="+", choices=[6, 7], help="run decisions.md 6 and 7")
    p.add_argument("--published", default=str(HERE.parent / "published"), help="decision 7's block (latest.json)")
    a = p.parse_args(argv)
    from model.data import load_config
    s = settings(load_config(a.config))
    try:
        if a.decide:
            noise = read_seed_noise(a.seed_noise)
            for n in a.decide:
                if n == 6:
                    res = decide_6(noise, s)
                    picked = pick_6(res)
                    for rule, r in res.items():
                        print(f"decision 6, {rule}: margin {r['margin']:.4f}; seed order: {r['seed_order']} move(s); "
                              f"{r['orders_moved']} of {r['orders']} orders move it, {r['mean_moves']:.2f} a time")
                    metrics = {f"{rule}_{k}": v for rule, r in res.items() for k, v in r.items()}
                else:
                    from model.evaluate import read_candidate as evaluate_read
                    res = decide_7(a.config, a.published, evaluate_read(a.candidate), noise, s)
                    picked = pick_7(res)
                    print(f"decision 7, block {res['block']}, window_days 365 v {res['window']} ({res['build_s']} s "
                          f"to build both): stored val PR-AUC {res['stored']:.4f} (N = {res['val_n_a']:,}, "
                          f"{res['val_positives_a']:,} positive), re-scored {res['rescored']:.4f} "
                          f"(N = {res['val_n_b']:,}, {res['val_positives_b']:,}), gap {res['gap']:.4f} "
                          f"against the margin {res['margin']:.4f}")
                    metrics = res
                print(f"decision {n}: picked {picked}")
                log_decision(n, metrics, picked, a.config, a.features)
            return 0
        if a.rollback:
            rollback(s, a.champion, a.push)
            return 0
        paths = {"candidate": a.candidate, "metrics": a.metrics, "bias": a.bias, "noise": a.seed_noise}
        register(paths, s, a.config, a.features, a.champion, a.push)
        return 0
    except ContractError as e:
        sys.exit(f"model.registry: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
