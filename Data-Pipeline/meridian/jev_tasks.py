"""Jev's task set (decision 19, GAPS.md gap 2, Rachna): 60 real writer tasks from a block, for a person to label.

    python -m meridian.jev_tasks --run-id <run_id>       # -> Model-Pipeline/packets/rachna-models/labels/

Writes two files:
- jev_tasks.csv: one row per task, with a short summary and an empty `label` column. A person fills in the model
  they'd start it on: small, mid or large (meridian.agents' LADDER, smallest first), and their initials in
  `labelled_by`. Labels are a person's, never a model's: they're the answer key every model is scored against
- jev_tasks_facts.json: each task's kind and Contract 5 facts, so the comparison rebuilds the exact tasks without
  the block

The 60 are drawn evenly across the writer's task kinds (seeded), and split 30 / 30: `prompt` tasks may be read
while writing Jev's prompt, `held_out` tasks may not, and decisions 13, 14 and 21 are scored on `held_out` only.
A task whose facts name a person is never drawn (the writer never writes it either). Read-only on the block.
"""
import argparse
import csv
import json
import random
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from meridian import agents as G
from meridian.common import CONFIG, HERE, load_config

ROOT = HERE.parent
LABELS = ROOT / "Model-Pipeline" / "packets" / "rachna-models" / "labels"
COLUMNS = ("task_id", "split", "kind", "repo", "owner_team", "target", "n_facts", "summary", "label", "labelled_by")
LABEL_CHOICES = ("small", "mid", "large")


def summary(task, width=240):
    """One line a person can judge the task from: its facts, compact, cut at `width`."""
    text = json.dumps(task.facts["facts"], sort_keys=True, ensure_ascii=False, separators=(", ", ": "))
    return text if len(text) <= width else text[: width - 1] + "…"


def n_facts(facts):
    """How many leaf values the facts hold: a rough size of the task."""
    if isinstance(facts, dict):
        return sum(n_facts(v) for v in facts.values())
    if isinstance(facts, list):
        return sum(n_facts(v) for v in facts)
    return 1


def draw(tasks, people, n=60, seed=1):
    """[(task, split)]: n tasks spread evenly over the kinds, seeded, none naming a person; half prompt, half
    held_out within each kind. Fewer than n if the block has fewer."""
    by_kind = defaultdict(list)
    for t in tasks:
        if not G.names_a_person(json.dumps(t.facts, ensure_ascii=False), people):
            by_kind[t.kind].append(t)
    rng = random.Random(seed)
    for kind in by_kind:
        rng.shuffle(by_kind[kind])
    picked, kinds = [], sorted(by_kind)
    while len(picked) < n and any(by_kind.values()):    # round-robin over the kinds until n
        for kind in kinds:
            if by_kind[kind] and len(picked) < n:
                picked.append(by_kind[kind].pop())
    out, count = [], defaultdict(int)
    for t in sorted(picked, key=lambda t: (t.kind, G.facts_hash(t.facts))):
        out.append((t, "prompt" if count[t.kind] % 2 == 0 else "held_out"))
        count[t.kind] += 1
    return out


def write(drawn, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "jev_tasks.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for t, split in drawn:
            w.writerow({"task_id": G.facts_hash(t.facts)[:12], "split": split, "kind": t.kind, "repo": t.repo,
                        "owner_team": t.owner_team, "target": t.target, "n_facts": n_facts(t.facts["facts"]),
                        "summary": summary(t), "label": "", "labelled_by": ""})
    facts = [{"task_id": G.facts_hash(t.facts)[:12], "split": split, "kind": t.kind, "repo": t.repo,
              "owner_team": t.owner_team, "target": t.target, "target_kind": t.target_kind, "facts": t.facts}
             for t, split in drawn]
    (folder / "jev_tasks_facts.json").write_text(json.dumps(facts, indent=1, ensure_ascii=False) + "\n",
                                                 encoding="utf-8")
    return folder / "jev_tasks.csv"


def read_labels(path):
    """{task_id: (split, label)} from a labelled sheet. Refuses an empty or unknown label: half a key isn't a key."""
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            label = row["label"].strip().lower()
            if label not in LABEL_CHOICES:
                raise ValueError(f"task {row['task_id']}: label {row['label']!r}, not one of {LABEL_CHOICES}")
            out[row["task_id"]] = (row["split"], label)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--db", type=Path, help="the block (default out/<run_id>/meridian.sqlite)")
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=LABELS)
    a = ap.parse_args(argv)
    path = a.db or ROOT / "out" / a.run_id / "meridian.sqlite"
    if not path.is_file():
        raise SystemExit(f"{path} not found")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (a.run_id,)).fetchone()
        if row is None:
            raise SystemExit(f"run {a.run_id} isn't in {path}")
        tasks = G.tasks(db, load_config(a.config), a.run_id, row[0])
        _, people = G.names(db)
    drawn = draw(tasks, people, a.n, a.seed)
    sheet = write(drawn, a.out)
    kinds = defaultdict(int)
    for t, _ in drawn:
        kinds[t.kind] += 1
    print(f"{len(tasks)} writer tasks in the block; drew {len(drawn)} ({dict(sorted(kinds.items()))}), "
          f"{sum(s == 'held_out' for _, s in drawn)} held out")
    if len(drawn) < a.n:
        print(f"note: the block has only {len(drawn)} drawable tasks, not {a.n}")
    print(f"wrote {sheet} and jev_tasks_facts.json: fill in `label` (small, mid or large) and `labelled_by`")


if __name__ == "__main__":
    main()
