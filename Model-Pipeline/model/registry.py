"""Registry (step G, Rochan): where models live. Nov 8: the champion record and the readers. The rest comes Nov 9.

On Nov 9 this module registers each model in the local MLflow registry, moves `champion` by decision 6, pushes the
champion to the Artifact Registry repo `meridian-models`, and rolls back (packets/rochan-tracking, section G). What's
here now is what other people build against before then (packets/README.md, Integration tests): the shape of
registry/champion.json, and the keys registry.py will read from metrics.json, bias_report.json and seed_noise.json.
That way a missing key fails a test today, not a run on integration day.

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
import json
import re
import sys
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


def main(argv=None):
    sys.exit("model.registry: registering, promotion, the Artifact Registry push and --rollback come Nov 9 "
             "(packets/rochan-tracking, section G). Today it holds only the champion record and the readers")


if __name__ == "__main__":
    main()
