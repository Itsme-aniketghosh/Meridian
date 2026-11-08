"""What gate.py reads from each Contract 4 file (Chaitali): one list, so the gate and the contract tests agree.

Each spec is a list of dotted paths into the JSON. `{candidate}` and `{reference}` are filled from the file itself
(metrics.json names its candidate column and its reference baseline). `missing(doc, kind)` returns the paths that
aren't there, so a producer's example in tests/contracts/ fails its test on a missing key, before integration.

The champion's keys follow Contract 4's words (run id, Artifact Registry version, metrics, model md5); Rochan's
registry.py isn't written yet, so they are checked against his example when it lands.
"""
import json
from pathlib import Path

SPECS = {
    "metrics": ["split", "n", "positives", "candidate.column", "reference",
                "models.{candidate}.pr_auc.value", "models.{candidate}.pr_auc.interval",
                "models.{reference}.pr_auc.value",
                "models.{candidate}.vs_{reference}.pr_auc_difference",
                "models.{candidate}.vs_{reference}.interval"],
    "bias_report": ["split", "candidate", "n", "positives", "threshold.rule", "overall.recall", "overall.fpr",
                    "overall.pr_auc", "slices", "tests", "flags", "open_flags", "pass"],
    "seed_noise": ["kind", "n", "seeds", "pr_auc", "mean", "sd"],
    "candidate": ["kind", "params", "scores_file", "column", "seed"],
    "champion": ["run_id", "version", "metrics", "model_md5"],
}
SLICE_KEYS = ("n", "positives", "recall", "fpr", "pr_auc")
METRIC_KEYS = ("value", "n", "ci", "judged", "flagged")


def _get(doc, path):
    for part in path.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return KeyError
        doc = doc[part]
    return doc


def missing(doc, kind):
    """The dotted paths of SPECS[kind] that `doc` doesn't have. For a bias report, also every slice's numbers."""
    fill = {}
    if kind == "metrics":
        cand = _get(doc, "candidate.column")
        fill = {"candidate": cand if isinstance(cand, str) else "?", "reference": doc.get("reference", "?")}
    out = [p for p in (s.format(**fill) for s in SPECS[kind]) if _get(doc, p) is KeyError]
    if kind == "bias_report" and isinstance(doc.get("slices"), dict):
        for dim, groups in doc["slices"].items():
            for g, e in groups.items():
                out += [f"slices.{dim}.{g}.{k}" for k in SLICE_KEYS if k not in e]
                for m in ("recall", "fpr", "pr_auc"):
                    out += [f"slices.{dim}.{g}.{m}.{k}" for k in METRIC_KEYS if k not in e.get(m, {})]
    return out


def read(path, kind):
    """The JSON at path, checked. Raises ValueError naming the file and every missing key."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{path}: not found") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not JSON ({e})") from None
    gone = missing(doc, kind)
    if gone:
        raise ValueError(f"{path}: missing {', '.join(gone[:8])}{' ...' if len(gone) > 8 else ''}")
    return doc
