"""Registry (step G, Rochan): where models live (packets/rochan-tracking, section G).

    python -m model.registry                       # register reports/'s candidate; promote it if it wins
    python -m model.registry --push                # and, on promotion, upload it to Artifact Registry
    python -m model.registry --rollback [--push]   # champion back to previous
    python -m model.registry --decide 6 7          # decisions.md 6 and 7

Register. The candidate's model file (candidate.json's model_file, next to it, with the md5 train.py recorded) becomes
a new version of [registry] name (`defect-risk`) in the local MLflow registry. The same model on the same table isn't
registered twice, unless it would now be promoted. Its run holds the model, metrics.json,
the bias report, seed noise, candidate.json and the champion record the version would get, with Contract 3's tags.

Promote (decision 6). The version takes `champion` only if bias_report.json passes and its val PR-AUC beats the
champion's by more than the margin: [registry] noise_sds standard deviations of seed noise (rule "noise"), or any
gain (rule "any"). The first model only needs the bias report. A version that isn't promoted stays registered, with a
`reason` tag that says why. On promotion, `previous` moves to the old champion and registry/champion.json is
written. CI calls this only after gate.py passes.

What the champion's PR-AUC is (decision 7): [registry] compare "rescore" re-scores the champion's own model on today's
val rows when the table changed (its features_md5 isn't today's), since a stored number describes other rows. On the
same table, and always with "stored", it's the number stored when the champion was registered.

Push. On promotion, the model file and champion.json go to Artifact Registry ([registry] project, location,
repository), package [registry] package, version <git sha> (`gcloud artifacts generic upload`). The newest version of
package [registry] pointer holds the champion.json in force: every promotion and rollback uploads one there. Without
[registry] project, gcloud or a signed-in account, --push skips and says so. Uncommitted code is refused, because the
version has to name the code. Local work never needs GCP.

Rollback. `champion` goes back to `previous`, `previous` to the champion before that, and registry/champion.json is
the restored version's record again. With --push, that record is uploaded as the new pointer.

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
    """The model version an alias points at, or None."""
    from mlflow.exceptions import MlflowException
    try:
        return client.get_model_version_by_alias(name, which)
    except MlflowException:
        return None


def stored_record(client, version):
    """The champion record kept in a version's run."""
    import mlflow
    with tempfile.TemporaryDirectory() as d:
        path = mlflow.artifacts.download_artifacts(run_id=version.run_id, artifact_path="champion.json", dst_path=d)
        return json.loads(Path(path).read_text(encoding="utf-8"))


def rescore(version, features):
    """Decision 7: a version's own model scored on today's val rows. (PR-AUC, N)."""
    import joblib
    import mlflow
    from model import train
    with tempfile.TemporaryDirectory() as d:
        path = mlflow.artifacts.download_artifacts(run_id=version.run_id,
                                                   artifact_path=f"model/{version.tags['model_file']}", dst_path=d)
        model = joblib.load(path)
    val = train.rows(train.load_features(features), "val")
    return float(train.pr_auc(val["label"].to_numpy(), train.score_with(model, val))), len(val)


def champion_pr_auc(champion, s, features_md5, features):
    """The champion's val PR-AUC to beat, and how it was got (decision 7)."""
    if s["compare"] == "stored" or champion.tags.get("features_md5") == features_md5:
        return float(champion.tags["pr_auc"]), "stored: same table" if s["compare"] == "rescore" else "stored"
    value, n = rescore(champion, features)
    return value, f"re-scored on today's val (N = {n:,}): the table changed"


def write_champion(record, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def register(paths, s, config, features, champion_path=CHAMPION, push=False, runner=subprocess.run, say=print):
    """Register reports/'s candidate and promote it if it wins. Returns the version's tags."""
    import mlflow
    from model import tracking
    cand, model, metrics, bias, noise = read_inputs(paths)
    block = candidate_block(metrics)
    need = margin(s, noise["sd"])
    _, client = _client()
    name = s["name"]
    if not client.search_registered_models(filter_string=f"name = '{name}'"):
        client.create_registered_model(name)
    champion = alias(client, name)
    against, how = (None, "no champion yet") if champion is None else champion_pr_auc(
        champion, s, metrics["features_md5"], features)
    promote, reason = verdict(block["pr_auc"]["value"], against, bias["pass"], need)
    same = next((v for v in client.search_model_versions(f"name = '{name}'")
                 if v.tags.get("model_md5") == cand["model_md5"] and v.tags.get("features_md5") == metrics[
                     "features_md5"]), None)
    is_champion = same is not None and champion is not None and same.version == champion.version
    if same is not None and (is_champion or not promote):
        why = "it's the champion" if is_champion else reason
        say(f"{name} version {same.version} is this model on this table already, so it isn't registered again: {why}")
        if push:
            say("--push: nothing to upload, nothing was promoted")
        return {**same.tags, "version": same.version, "promoted": "false", "reason": why}
    sha, dirty = tracking.git_state()
    previous = stored_record(client, champion) if champion else None

    with tracking.start_run(f"registry-{cand['column']}", owner="rochan", config=config, features=features) as run:
        mlflow.log_artifact(str(model), artifact_path="model")
        for key in ("metrics", "bias", "noise", "candidate"):
            mlflow.log_artifact(str(paths[key]))
        record = champion_record(metrics, run.info.run_id, sha, cand["model_file"], cand["model_md5"], previous)
        mlflow.log_dict(record, "champion.json")
        mlflow.log_metrics({"val_pr_auc": block["pr_auc"]["value"], "margin": need})
        artifacts = run.info.artifact_uri
    tags = {"pr_auc": repr(block["pr_auc"]["value"]), "features_md5": metrics["features_md5"],
            "model_md5": cand["model_md5"], "model_file": cand["model_file"], "git_sha": sha, "git_dirty": dirty,
            "promoted": str(promote).lower(), "reason": reason, "compared": how,
            "champion_pr_auc": "" if against is None else repr(against)}
    version = client.create_model_version(name, source=f"{artifacts}/model", run_id=record["run_id"], tags=tags)
    say(f"registered {name} version {version.version} ({cand['kind']} {cand['column']}, val PR-AUC "
        f"{block['pr_auc']['value']:.4f}, N = {block['pr_auc']['n']:,}). Champion: {how}")
    if not promote:
        say(f"not promoted: {reason}")
        if push:
            say("--push: nothing to upload, nothing was promoted")
        return {**tags, "version": version.version}
    if champion is not None:
        client.set_registered_model_alias(name, "previous", champion.version)
    client.set_registered_model_alias(name, "champion", version.version)
    write_champion(record, champion_path)
    say(f"promoted to champion: {reason}. previous = "
        f"{champion.version if champion else 'none'}; wrote {champion_path}")
    if push:
        pushed(record, s, dirty, runner, say, model)
    return {**tags, "version": version.version}


def rollback(s, champion_path=CHAMPION, push=False, runner=subprocess.run, say=print):
    """`champion` back on `previous`, `previous` on the one before that, champion.json the restored record."""
    from model import tracking
    _, client = _client()
    name = s["name"]
    champion, previous = alias(client, name), alias(client, name, "previous")
    if previous is None:
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
        pushed(record, s, tracking.git_state()[1], runner, say)
    return record


# The Artifact Registry push


def gcp_missing(s, runner=subprocess.run):
    """Why --push can't upload from here, or None."""
    if not s["project"]:
        return "model.toml [registry] project isn't set"
    if not shutil.which("gcloud"):
        return "gcloud isn't installed"
    r = runner(["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
               capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        return "no gcloud account is signed in"
    return None


def upload(s, package, version, files, runner=subprocess.run):
    """One `gcloud artifacts generic upload` per file. Stops on the first failure, with gcloud's message."""
    for f in files:
        cmd = ["gcloud", "artifacts", "generic", "upload", f"--project={s['project']}", f"--location={s['location']}",
               f"--repository={s['repository']}", f"--package={package}", f"--version={version}", f"--source={f}"]
        r = runner(cmd, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"upload failed: {' '.join(cmd)}\n{(r.stderr or r.stdout).strip()}")


def pointer_version():
    """The pointer package's versions sort by time: the newest is the champion in force."""
    return time.strftime("%Y%m%dt%H%M%Sz", time.gmtime())


def pushed(record, s, dirty, runner, say, model=None):
    """Upload on promotion (model and champion.json) or rollback (champion.json). False if it was skipped."""
    why = gcp_missing(s, runner)
    if why:
        say(f"--push skipped: {why}. The registry and champion.json here are done; nothing went to GCP")
        return False
    if dirty != "false":
        raise SystemExit("--push needs committed code: the Artifact Registry version is the git sha, and uncommitted "
                         "changes aren't in it")
    with tempfile.TemporaryDirectory() as d:
        champion = Path(d) / "champion.json"
        champion.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        if model is not None:
            upload(s, s["package"], record["version"], [model, champion], runner)
        stamp = pointer_version()
        upload(s, s["pointer"], stamp, [champion], runner)
    where = f"{s['location']}/{s['repository']} in {s['project']}"
    say(f"pushed to {where}: " + (f"{s['package']} {record['version'][:12]} (model and champion.json), " if model
                                  else "") + f"{s['pointer']} {stamp}")
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
