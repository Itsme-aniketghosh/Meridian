"""Rachna's Contract 4 examples in tests/contracts/: val_scores.csv, candidate.json, seed_noise.json.

    python -m tests.make_contracts_rachna          # from Model-Pipeline/: rewrite the three files

Each comes from a real run of train.py's code on tests/fake_table.py, so it has the real shape. Consumers (evaluate,
bias, gate, registry) test against these files, not against train.py. The scores are one row per val row of
fake_table(n=1500, seed=21), in the table's order: join slices on sha from that same table.
"""
import json
import shutil
import tempfile
from pathlib import Path

from model.train import evaluate, pick_candidate, seed_noise
from tests.fake_table import fake_table

CONTRACTS = Path(__file__).resolve().parent / "contracts"
TABLE = {"n": 1500, "seed": 21}
# One seeded model, so seed_noise.json has a real spread (logistic regression ignores the seed: sd 0).
MODELS = {"forest": {"n_estimators": 50, "min_samples_leaf": 10}}
SEED = 1


def make(out_dir):
    """Write val_scores.csv, candidate.json and seed_noise.json into out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = fake_table(**TABLE)
    with tempfile.TemporaryDirectory() as tmp:
        evaluate(df, SEED, tmp, MODELS, "example", reps=200)
        candidate = pick_candidate(tmp, "example", SEED, reps=200)
        shutil.copy(Path(tmp) / "example_val_scores.csv", out_dir / "val_scores.csv")
        noise = seed_noise(df, candidate["kind"], candidate["params"], tmp)
    candidate["scores_file"] = "val_scores.csv"
    candidate["example_of"] = f"tests/fake_table.py fake_table(n={TABLE['n']}, seed={TABLE['seed']})"
    noise["example_of"] = candidate["example_of"]
    (out_dir / "candidate.json").write_text(json.dumps(candidate, indent=2) + "\n")
    (out_dir / "seed_noise.json").write_text(json.dumps(noise, indent=2) + "\n")
    return out_dir


if __name__ == "__main__":
    print(f"wrote {make(CONTRACTS)}/val_scores.csv, candidate.json, seed_noise.json")
