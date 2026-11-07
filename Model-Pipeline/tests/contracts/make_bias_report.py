"""Writes tests/contracts/bias_report.json: bias.py's real output shape, on tests/fake_table.py, never real data.

    python -m tests.contracts.make_bias_report        # from Model-Pipeline/, after bias.py's output changes

tests/test_contracts_bias.py fails while the committed file differs from what this writes.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from model import bias
from tests.fake_table import fake_table

HERE = Path(__file__).resolve().parent
EVAL = {"bootstrap_reps": 200, "interval": 0.95, "threshold": "alert_rate", "threshold_precision": 0.8,
        "threshold_min_alerts": 5, "threshold_alert_rate": 0.2}
BIAS = {"slices": ["module", "component", "exp_bucket", "size_bucket"], "min_n": 30, "max_gap": 0.10,
        "power_gap": 0.15, "pool_below": 30, "baseline": "lines_x_files", "accepted": []}


def example():
    """The report as a dict: made-up val rows, a made-up model that is noise on module core (so one slice flags)."""
    t = fake_table(n=6000, seed=4, years=[2021, 2022, 2023])
    rng = np.random.default_rng(4)
    t["exp_bucket"] = rng.choice(["0", "1", "2-10", ">10"], len(t))
    t["size_bucket"] = pd.qcut(t["la"] + t["ld"], 4, labels=["Q1", "Q2", "Q3", "Q4"]).astype(str)
    val = t[t["split"] == "val"].reset_index(drop=True)
    core = (val["module"] == "core").to_numpy()
    model = np.where(core, rng.random(len(val)), val["label"] * 0.5 + rng.random(len(val)) * 0.6)
    scores = {"model": model, "lines_x_files": ((val["la"] + val["ld"]) * val["nf"]).to_numpy(dtype=float)}
    return bias.build(val, scores, "model", BIAS, EVAL, seed=1)


def text():
    return json.dumps(example(), indent=2) + "\n"


if __name__ == "__main__":
    path = HERE / "bias_report.json"
    path.write_text(text(), encoding="utf-8", newline="\n")
    print(f"wrote {path}")
