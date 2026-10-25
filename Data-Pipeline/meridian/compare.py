"""Compare · the weekly rebuild against the updates: every table, byte for byte (UPDATE-PLAN.md, DAGs).

    python -m meridian.compare --run-id <run_id> --config meridian.toml --against-root <other data root>

Compares each out/<run_id>/<table>.jsonl under this data root ($MERIDIAN_DATA) with the same file under --against-root.
run.jsonl is left out: its image_digest is the machine's fingerprint. A table on one side only differs. Each differing
table is logged with both row counts. Any difference alerts ([alerts]) and exits 1; the same tables exit 0.
"""
import filecmp
from pathlib import Path

from meridian import alert
from meridian.common import CONFIG, data_root, handoff, load_config, log, parser


def rows(path):
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))


def compare(a, b):
    """The tables whose .jsonl differ between out folders a and b: [(table, rows in a, rows in b)], None = missing."""
    names = sorted({p.name for d in (a, b) for p in d.glob("*.jsonl")} - {"run.jsonl"})
    return [(n[:-len(".jsonl")], rows(a / n), rows(b / n)) for n in names
            if not ((a / n).exists() and (b / n).exists() and filecmp.cmp(a / n, b / n, shallow=False))]


def main(run_id, against, config=CONFIG, root=None, settings=None):
    cfg = load_config(config)
    a, b = handoff(cfg, run_id, Path(root or data_root())), handoff(cfg, run_id, Path(against))
    if not any(a.glob("*.jsonl")):
        raise SystemExit(f"no tables in {a}: nothing to compare")
    diff = compare(a, b)
    lines = [f"{t}: {na} rows here, {nb} under {against}" + (f" ({na - nb:+})" if None not in (na, nb) else "")
             for t, na, nb in diff]
    for line in lines:
        log(f"  {line}")
    if diff:
        alert.send(f"meridian {run_id}: the rebuild differs from the update in {len(diff)} tables", lines, "error", settings)
        raise SystemExit(f"{len(diff)} tables differ between {a} and {b}")
    log(f"0 differences between {a} and {b}")
    return diff


if __name__ == "__main__":
    ap = parser(__doc__, "the run computed under both data roots")
    ap.add_argument("--against-root", type=Path, required=True, help="the other data root (the updates')")
    a = ap.parse_args()
    main(a.run_id, a.against_root, a.config)
