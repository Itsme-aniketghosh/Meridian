"""A Contract 1 feature table made up from a seed, for tests. Not real commits."""
import numpy as np
import pandas as pd

from model.train import CONTRACT_COLUMNS


def split_of(year):
    if 2015 <= year <= 2022:
        return "train"
    return {2023: "val", 2024: "test"}.get(year, "none")


def fake_table(n=3000, seed=0, years=range(2015, 2026)):
    rng = np.random.default_rng(seed)
    year = rng.choice(list(years), size=n)
    la = rng.poisson(40, n)
    ld = rng.poisson(15, n)
    nf = 1 + rng.poisson(2, n)
    prior = rng.poisson(1.5, n)
    risk = 0.004 * (la + ld) + 0.25 * nf + 0.3 * prior - 2.5
    label = (rng.random(n) < 1 / (1 + np.exp(-risk))).astype(int)
    df = pd.DataFrame({
        "sha": [f"{i:040x}" for i in range(n)],
        "authored_at": [f"{y}-06-01T00:00:00Z" for y in year],
        "year": year,
        "split": [split_of(int(y)) for y in year],
        "label": label,
        "la": la, "ld": ld, "nf": nf,
        "nd": np.minimum(nf, 1 + rng.poisson(1, n)),
        "ns": 1 + rng.poisson(0.3, n),
        "entropy": rng.random(n),
        "ndev": rng.poisson(5, n), "age": rng.random(n) * 300, "nuc": rng.poisson(20, n),
        "exp": rng.poisson(100, n), "sexp": rng.poisson(30, n),
        "prior_bug_fixes": prior,
        "fix": rng.integers(0, 2, n), "has_ticket": rng.integers(0, 2, n),
        "is_minor": rng.integers(0, 2, n),
        "ticket_type": rng.choice(["Bug", "Improvement", "none"], n),
        "module": rng.choice(["core", "sql", "python"], n),
        "component": rng.choice(["SQL", "Spark Core", "none"], n),
        "exp_bucket": rng.choice(["0", "1", "2-10", ">10"], n),
        "size_bucket": rng.choice(["q1", "q2", "q3", "q4"], n),
    })
    df = df.sort_values(["authored_at", "sha"]).reset_index(drop=True)
    return df[CONTRACT_COLUMNS]
