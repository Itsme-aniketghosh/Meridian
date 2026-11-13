"""Shared by the gate faults (1, 2, 9, 12): test_gate's Case, the Contract 4 examples made to agree, with the
candidate's and lines x files' val PR-AUC set to what the fault produces."""
from functools import lru_cache

from model import gate
from model.train import baseline_scores, chance_ceiling, pr_auc, rows
from tests import test_gate as G
from tests.fake_table import fake_table

PARAMS = {"n_estimators": 100, "min_samples_leaf": 5}


@lru_cache(None)
def _table():
    return fake_table(n=20000, seed=7)  # test_gate's planted models: val big enough for real-sized seed noise


@lru_cache(None)
def planted(name):
    """(val PR-AUC of decision 6's planted model `name` (a forest), lines x files' val PR-AUC, val's positive rate,
    the 99th percentile of PR-AUC from random scores on val)."""
    df = _table()
    change, drop = gate.planted()[name]
    value = gate._planted_score(df, "forest", PARAMS, 1, change, drop)
    val = rows(df, "val")
    y = val["label"].astype(int).to_numpy()
    return value, pr_auc(y, baseline_scores(val)["lines_x_files"]), float(y.mean()), chance_ceiling(y)


class GateFault(G.Case):
    def set_pr_auc(self, value, reference, champion=None):
        m = self.docs["metrics.json"]
        m["models"][m["candidate"]["column"]]["pr_auc"]["value"] = value
        m["models"]["lines_x_files"]["pr_auc"]["value"] = reference
        if champion is not None:
            self.docs["champion.json"]["metrics"]["pr_auc"]["value"] = champion
