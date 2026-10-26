"""Compare · the weekly rebuild against the updates: every table, byte for byte (UPDATE-PLAN.md, DAGs).

    python -m meridian.compare --run-id <run_id> --config meridian.toml --against-root <other data root>

Compares each out/<run_id>/<table>.jsonl under this data root ($MERIDIAN_DATA) with the same file under --against-root,
and changes.json, the run's change report (meridian.report). The rebuild is the truth: where they differ, the update
was wrong. run.jsonl is left out: its image_digest is the machine's fingerprint. A file on one side only differs.

When files differ it says where it went wrong: the parts (dags/pipeline_spec.py STEPS, and report) whose inputs are
the same on both sides but whose outputs aren't. Those are where the difference started; the parts after them differ only because
their inputs do. Rerun such a part on both roots, with its [cache] switch on and off, to find the bug. Per table: the
rows only the update has, only the rebuild has, and a few of each. All of it goes to out/<run_id>/compare.json here,
and to the alert.

Exit 0 = the same tables, 3 = they differ (alerted). Any other code is a crash: the DAG fails the task, never publishes.
"""
import filecmp
import hashlib
import json
from collections import Counter
from pathlib import Path

from dags.pipeline_spec import DAGS, STEPS
from meridian import alert
from meridian.common import CONFIG, data_root, handoff, load_config, log, parser

DIFFERENT = 3
EXAMPLES = 3
REPORT = "changes"   # changes.json
PARTS = {**STEPS, "report": (DAGS["rebuild"]["report"], ("run", "git_commit", "call_site", "call_edge", "blast_radius"),
                              (REPORT,))}
# shortcut: checks the last published run's report only, not each update's since the last rebuild (each would need its
# own rebuild); a wrong earlier report shows up here only if it left wrong tables behind


def rows(path):
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))


def compare(a, b):
    """The tables (and the report) that differ between out folders a and b: [(name, lines in a, lines in b)], None =
    missing."""
    names = sorted({p.name for d in (a, b) for p in [*d.glob("*.jsonl"), d / f"{REPORT}.json"] if p.exists()}
                   - {"run.jsonl"})
    return [(n.rsplit(".", 1)[0] if n.endswith(".jsonl") else REPORT, rows(a / n), rows(b / n)) for n in names
            if not ((a / n).exists() and (b / n).exists() and filecmp.cmp(a / n, b / n, shallow=False))]


def where(tables):
    """The parts where a difference started: some output differs, every input is the same."""
    return [p for p, (_, reads, writes) in PARTS.items()
            if set(writes) & tables and "*" not in reads and not set(reads) & tables]


def row_diff(a, b):
    """(rows only in a, rows only in b, up to EXAMPLES of each). Rows are compared as whole lines, by digest."""
    def count(path):
        if not path.exists():
            return Counter()
        with open(path, "rb") as f:
            return Counter(hashlib.blake2b(line, digest_size=16).digest() for line in f)
    ca, cb = count(a), count(b)
    only_a, only_b = ca - cb, cb - ca

    def examples(path, only):
        out, left = [], Counter(only)
        if path.exists():
            with open(path, "rb") as f:
                for line in f:
                    if len(out) == EXAMPLES:
                        break
                    if left[d := hashlib.blake2b(line, digest_size=16).digest()] > 0:
                        left[d] -= 1
                        out.append(line.decode("utf-8").rstrip("\n"))
        return out
    return sum(only_a.values()), sum(only_b.values()), examples(a, only_a), examples(b, only_b)


def main(run_id, against, config=CONFIG, root=None, settings=None):
    cfg = load_config(config)
    a, b = handoff(cfg, run_id, Path(root or data_root())), handoff(cfg, run_id, Path(against))
    if not any(a.glob("*.jsonl")):
        raise SystemExit(f"no tables in {a}: nothing to compare")
    diff = compare(a, b)
    started = where({t for t, *_ in diff})
    report = {"run_id": run_id, "rebuild": str(a), "update": str(b), "went_wrong_in": started, "tables": []}
    lines = [f"went wrong in: {', '.join(started) or 'no single part (see tables)'}"] if diff else []
    for t, na, nb in diff:
        f = f"{t}.json" if t == REPORT else f"{t}.jsonl"
        only_rebuild, only_update, ex_rebuild, ex_update = row_diff(a / f, b / f)
        report["tables"].append({"table": t, "rows_rebuild": na, "rows_update": nb, "only_rebuild": only_rebuild,
                                 "only_update": only_update, "examples_rebuild": ex_rebuild, "examples_update": ex_update})
        lines.append(f"{t}: {na} rows in the rebuild, {nb} in the update; {only_rebuild} only in the rebuild, "
                     f"{only_update} only in the update")
    (a / "compare.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    for line in lines:
        log(f"  {line}")
    if diff:
        alert.send(f"meridian {run_id}: the update was wrong in {len(diff)} files (the weekly rebuild is the truth)",
                   lines + [f"details: {a / 'compare.json'}"], "error", settings)
        log(f"{len(diff)} tables differ between {a} and {b}: {a / 'compare.json'}")
        raise SystemExit(DIFFERENT)
    log(f"0 differences between {a} and {b}")
    return diff


if __name__ == "__main__":
    ap = parser(__doc__, "the run computed under both data roots")
    ap.add_argument("--against-root", type=Path, required=True, help="the other data root (the updates')")
    a = ap.parse_args()
    main(a.run_id, a.against_root, a.config)
