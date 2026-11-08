"""Rochan's contract examples: tests/contracts/metrics.json and champion.json (packets/README.md, Integration tests).

    python -m tests.contracts.make_rochan        # from Model-Pipeline/: rewrites both files

Both come from one run on tests/fake_table.py, so they have the real shape. train.py fits the candidate and writes its
val scores, evaluate.py writes metrics.json from them with model.toml's [eval], and registry.champion_record builds
champion.json from that metrics.json and the fitted model's md5.

Two things differ from a real run, on purpose:
- The threshold is chosen by alert rate (decision 3's third rule), not by precision. On the fake table no threshold
  reaches precision 0.80 on 30 alerts, so the team's rule would write a threshold of null. The example shows a
  champion with a threshold; a real run on a weak model can still write null, and consumers must handle that.
- The MLflow run id and the git sha are placeholders (all zeros): the example has no run and no commit of its own.

test_evaluate.py and test_registry.py build both again and check the committed files have the same keys, so a change
to either shape fails a test until this is run again and the change is announced (S10).
"""
import contextlib
import functools
import io
import json
import re
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent  # tests/contracts/
MODEL_TOML = HERE.parents[1] / "model.toml"
ROWS, TABLE_SEED = 3000, 0  # fake_table's size and seed
KIND, PARAMS, SEED = "lgbm", {"num_leaves": 15, "n_estimators": 100}, 1  # the candidate, as candidate.json names it
RUN_ID, VERSION = "0" * 32, "0" * 40  # placeholders: the example has no MLflow run and no commit


def example_config(path):
    """model.toml as it is, except the threshold rule: by alert rate, so the fake table gets a threshold."""
    text, n = re.subn(r'^threshold = "[a-z_]+"', 'threshold = "alert_rate"', MODEL_TOML.read_text(encoding="utf-8"),
                      flags=re.M)
    if n != 1:
        raise SystemExit(f"{MODEL_TOML}: expected one [eval] threshold line, found {n}")
    Path(path).write_text(text, encoding="utf-8")
    return path


@functools.lru_cache(maxsize=1)
def build():
    """(metrics, champion) from one run on the fake table, as dicts. Cached, so the tests that read it share one run.
    Don't change what it returns: copy it first."""
    import joblib

    from model import evaluate, registry, train
    from tests.fake_table import fake_table

    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
        d = Path(d)
        table = fake_table(n=ROWS, seed=TABLE_SEED)
        table.to_csv(d / "features.csv", index=False, lineterminator="\n")  # LF: the same md5s on Windows
        train.evaluate(table, SEED, d, {KIND: PARAMS}, "fake", reps=200)  # writes fake_val_scores.csv
        scores = d / "fake_val_scores.csv"
        scores.write_bytes(scores.read_bytes().replace(b"\r\n", b"\n"))  # pandas writes CRLF on Windows
        (d / "candidate.json").write_text(json.dumps({"kind": KIND, "params": PARAMS, "scores_file":
                                                      "fake_val_scores.csv", "column": KIND, "seed": SEED}))
        evaluate.main(["--config", str(example_config(d / "model.toml")), "--features", str(d / "features.csv"),
                       "--candidate", str(d / "candidate.json"), "--out", str(d / "out"), "--no-mlflow",
                       "--no-charts"])
        metrics = json.loads((d / "out" / "metrics.json").read_text(encoding="utf-8"))
        model, _, _ = train.fit_and_score(table, KIND, SEED, PARAMS)
        joblib.dump(model, d / "model.joblib")
        champion = registry.champion_record(metrics, RUN_ID, VERSION, "model.joblib",
                                            evaluate.file_md5(d / "model.joblib"))
    return metrics, champion


def shape(x):
    """The keys and the kind of each value, without the values: what a consumer relies on."""
    if isinstance(x, dict):
        return {k: shape(v) for k, v in x.items()}
    if isinstance(x, list):
        return [shape(v) for v in x]
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "true or false"
    return "number" if isinstance(x, (int, float)) else "text"


def main():
    metrics, champion = build()
    for name, data in (("metrics.json", metrics), ("champion.json", champion)):
        (HERE / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {(HERE / name).relative_to(HERE.parents[1]).as_posix()}")


if __name__ == "__main__":
    main()
