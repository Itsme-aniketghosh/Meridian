"""Step B: the feature table (packets/README.md, Contract 1). Owner: Utkarsh.

python -m model.data --config model.toml

Reads published/latest.json (under $MERIDIAN_DATA, else the repo root), opens that block, and writes
data/features.parquet and data/features.schema.json. Both go in git: no email, name or person id is ever written.

- Rows: one per first-parent commit of [data] repo authored in [data] start_year or later, sorted by authored_at, sha.
- label = 1 if a bug_inducing row names the commit as inducing_sha and its fix_sha was authored 0 to window_days
  later. Every row gets a label; split = none marks the ones whose follow-up isn't complete.
- split: train, val, test by year ([split]); none for later years and for anything after follow_up_end.
- History features look only at earlier first-parent commits (in first-parent order, from the repo's first commit),
  never at the snapshot. file_bug_fixes is computed at the snapshot, so it's never read; prior_bug_fixes is counted
  as of each commit instead. Renames carry a file's history to its new path.
- A fix is what SZZ calls one (Data-Pipeline/meridian/szz.py, fixes): first-parent, not a merge, from [szz] fix_since
  on, keyed to a ticket matching [szz] fix that isn't bulk-closed, and not a revert. The rule is read from
  Data-Pipeline/meridian.toml so the two can't drift.
- Author identity is the merged person (person_alias), not the raw email (decision 3).
- Binary files count as files with 0 lines (decision 7).
- module is the top-level directory with the most changed lines, one level deeper inside sql/ (decision 5).
- Same block and config give the same bytes (no wall clock, no pandas metadata, fixed sort).
"""
import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import tomllib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parents[1]  # Model-Pipeline/
ROOT = HERE.parent
MERIDIAN_TOML = ROOT / "Data-Pipeline" / "meridian.toml"
FMT = "%Y-%m-%dT%H:%M:%SZ"
MINOR = re.compile(r"^\[MINOR\]", re.IGNORECASE)
SPLIT_IN_SUBDIR = {"sql"}  # decision 5: these top-level directories are split one level down

# (name, parquet type, meaning): the order is the column order.
COLUMNS = [
    ("sha", "str", "commit SHA"),
    ("authored_at", "str", "UTC, %Y-%m-%dT%H:%M:%SZ"),
    ("year", "int", "from authored_at"),
    ("split", "str", "train, val, test by year; none after the test year or after follow_up_end"),
    ("label", "int", "1 if a fix commit authored within window_days after this one blames it (bug_inducing)"),
    ("la", "int", "lines added (binary files count 0)"),
    ("ld", "int", "lines deleted (binary files count 0)"),
    ("nf", "int", "files touched"),
    ("nd", "int", "directories touched"),
    ("ns", "int", "top-level directories touched"),
    ("entropy", "float", "Shannon entropy (bits) of changed lines across files"),
    ("ndev", "int", "distinct earlier authors (merged persons) on these files"),
    ("age", "float", "mean days since these files last changed; 0 when none changed before"),
    ("nuc", "int", "distinct earlier commits on these files"),
    ("exp", "int", "the author's earlier commits (merged person)"),
    ("sexp", "int", "the author's earlier commits in this commit's module"),
    ("prior_bug_fixes", "int", "fix commits on these files in the prior_fix_days before this commit"),
    ("fix", "int", "1 if a linked ticket's effective_type is Bug"),
    ("has_ticket", "int", "1 if the commit carries a ticket key that exists in the tracker"),
    ("ticket_type", "str", "the linked ticket's effective_type (smallest key), or none"),
    ("is_minor", "int", "subject starts with [MINOR], any case"),
    ("module", "str", "slice: top-level directory with the most changed lines; inside sql/, the second level"),
    ("component", "str", "slice: the linked ticket's first Jira component (smallest key), or none"),
    ("exp_bucket", "str", "slice: 0, 1, 2-10, >10 earlier commits"),
    ("size_bucket", "str", "slice: la + ld by quartile on train, Q1 to Q4"),
]
INT = [n for n, t, _ in COLUMNS if t == "int"]
FLOAT = [n for n, t, _ in COLUMNS if t == "float"]
STR = [n for n, t, _ in COLUMNS if t == "str"]


def ts(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def days(s):
    """authored_at -> days since the epoch, as a float."""
    return ts(s).timestamp() / 86400.0


def load_config(path):
    cfg = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    for s, keys in {"data": ["run_id", "window_days", "follow_up_end", "start_year", "repo", "prior_fix_days"],
                    "split": ["train", "val", "test"]}.items():
        missing = [k for k in keys if k not in cfg.get(s, {})]
        if missing:
            raise SystemExit(f"{path}: [{s}] is missing {', '.join(missing)}")
    return cfg


def fix_rule(path=MERIDIAN_TOML):
    s = tomllib.loads(Path(path).read_text(encoding="utf-8"))["szz"]
    return s["fix"], s["fix_since"]


def published_dir():
    return Path(os.environ.get("MERIDIAN_DATA") or ROOT) / "published"


def open_block(published, run_id):
    """(sqlite path, latest.json). Stops if latest.json names another run than [data] run_id."""
    latest = json.loads((Path(published) / "latest.json").read_text(encoding="utf-8"))
    if latest["run_id"] != run_id:
        raise SystemExit(f"published/latest.json is run {latest['run_id']}, model.toml [data] run_id is {run_id}. "
                         "Change one so they agree.")
    db = Path(published) / latest["file"]
    if not db.exists():
        raise SystemExit(f"{db} is missing: build the block first (Data-Pipeline/reproduce.py)")
    return db, latest


def read_block(db, run_id, repo):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = lambda sql, *p: pd.read_sql(sql, con, params=[run_id, *p])
    run = q("select snapshot_at, status from run where run_id = ?")
    if run.empty:
        raise SystemExit(f"{db} has no run row for {run_id}")
    out = {
        "snapshot_at": run.snapshot_at[0], "status": run.status[0],
        "commits": q("select sha, parent_sha, seq, is_merge, authored_at, author_email, subject from git_commit "
                     "where run_id = ? and repo = ? and first_parent = 1 order by seq", repo),
        "files": q("select sha, path, old_path, added, deleted from commit_file where run_id = ? and repo = ?", repo),
        "bug_inducing": q("select fix_sha, inducing_sha from bug_inducing where run_id = ? and repo = ?", repo),
        "commit_ticket": q("select ct.sha, ct.key, t.effective_type, t.resolution, t.bulk_closed, t.components "
                           "from commit_ticket ct join ticket t on t.run_id = ct.run_id and t.key = ct.key "
                           "where ct.run_id = ? and ct.repo = ?", repo),
        "alias": q("select email, person_id from person_alias where run_id = ?"),
    }
    con.close()
    return out


def fix_commits(commits, commit_ticket, rule, since):
    """SHAs SZZ counts as fix commits (meridian/szz.py, fixes), restricted to first-parent commits."""
    ok = commit_ticket[(commit_ticket.bulk_closed == 0)]
    for k, v in rule.items():
        ok = ok[ok[k] == v]
    c = commits[(commits.is_merge == 0) & (commits.authored_at >= since)
                & ~commits.subject.str.startswith('Revert "')]
    return set(c.sha) & set(ok.sha)


def labels(commits, bug_inducing, window_days):
    """{inducing sha} blamed by a fix authored 0..window_days after it."""
    t = dict(zip(commits.sha, commits.authored_at.map(days)))
    bi = bug_inducing.drop_duplicates()
    bi = bi[bi.fix_sha.isin(t) & bi.inducing_sha.isin(t)]
    gap = bi.fix_sha.map(t) - bi.inducing_sha.map(t)
    return set(bi.inducing_sha[(gap >= 0) & (gap <= window_days)])


def top(path):
    return path.split("/", 1)[0] if "/" in path else "(root)"


def module_of(path):
    parts = path.split("/")
    if len(parts) > 2 and parts[0] in SPLIT_IN_SUBDIR:
        return f"{parts[0]}/{parts[1]}"
    return top(path)


def entropy(lines):
    total = sum(lines)
    if total <= 0:
        return 0.0
    return 0.0 - sum(n / total * math.log2(n / total) for n in lines if n > 0)  # 0.0 - 0.0 is 0.0, never -0.0


def history(commits, files, person, fixes, prior_fix_days):
    """Per-commit change and history features, walking first-parent commits in order. {sha: {feature: value}}"""
    by_sha = defaultdict(list)
    for r in files.itertuples(index=False):
        a = 0 if pd.isna(r.added) else int(r.added)
        d = 0 if pd.isna(r.deleted) else int(r.deleted)
        by_sha[r.sha].append((r.path, None if pd.isna(r.old_path) else r.old_path, a, d))
    state = {}  # path -> [last change (days), {author idx}, {commit idx}, [(fix time, fix idx)]]
    exp, sexp = defaultdict(int), defaultdict(int)
    out = {}
    for i, c in enumerate(commits.itertuples(index=False)):
        t = days(c.authored_at)
        who = person[c.author_email]
        fs = sorted(by_sha.get(c.sha, []))
        lines = [a + d for _, _, a, d in fs]
        if fs:
            mod_lines = defaultdict(int)
            for (p, _, a, d) in fs:
                mod_lines[module_of(p)] += a + d
            module = min(mod_lines, key=lambda m: (-mod_lines[m], m))
        else:
            module = "(none)"
        prev = [state.get(old or p) for p, old, _, _ in fs]
        seen = [s for s in prev if s is not None]
        authors, earlier, fixed = set(), set(), set()
        for s in seen:
            authors |= s[1]
            earlier |= s[2]
            fixed.update(j for ft, j in s[3] if t - prior_fix_days <= ft < t)
        out[c.sha] = {
            "la": sum(a for _, _, a, _ in fs), "ld": sum(d for _, _, _, d in fs), "nf": len(fs),
            "nd": len({p.rsplit("/", 1)[0] if "/" in p else "" for p, _, _, _ in fs}),
            "ns": len({top(p) for p, _, _, _ in fs}),
            "entropy": round(entropy(lines), 6),
            "ndev": len(authors),
            "age": round(sum(t - s[0] for s in seen) / len(seen), 4) if seen else 0.0,
            "nuc": len(earlier),
            "exp": exp[who], "sexp": sexp[(who, module)],
            "prior_bug_fixes": len(fixed),
            "module": module,
        }
        # now this commit is history
        is_fix = c.sha in fixes
        for (p, old, _, _), s in zip(fs, prev):
            if old and old in state:
                state.pop(old)
            s = s or [t, set(), set(), []]
            s[0] = t
            s[1].add(who)
            s[2].add(i)
            if is_fix:
                s[3].append((t, i))
            state[p] = s
        exp[who] += 1
        sexp[(who, module)] += 1
    return out


def split_of(year, authored_at, cfg):
    s = cfg["split"]
    if authored_at > cfg["data"]["follow_up_end"]:
        return "none"
    if s["train"][0] <= year <= s["train"][1]:
        return "train"
    if year == s["val"]:
        return "val"
    if year == s["test"]:
        return "test"
    return "none"


def exp_bucket(n):
    return "0" if n == 0 else "1" if n == 1 else "2-10" if n <= 10 else ">10"


def check_window(cfg, snapshot_at):
    d = cfg["data"]
    if ts(d["follow_up_end"]) + timedelta(days=d["window_days"]) > ts(snapshot_at):
        raise SystemExit(f"follow_up_end {d['follow_up_end']} + window_days {d['window_days']} is after the "
                         f"snapshot ({snapshot_at}): labels near follow_up_end would be cut short")
    last_test = f"{cfg['split']['test']}-12-31T23:59:59Z"
    if last_test > d["follow_up_end"]:
        raise SystemExit(f"the test year {cfg['split']['test']} ends after follow_up_end {d['follow_up_end']}")


def build(block, cfg, rule=None):
    """The feature table as a DataFrame, plus the size-bucket edges."""
    d = cfg["data"]
    check_window(cfg, block["snapshot_at"])
    rule, since = rule or fix_rule()
    commits, files = block["commits"], block["files"]
    person = dict(zip(block["alias"].email, block["alias"].person_id))
    missing = set(commits.author_email) - set(person)
    if missing:
        raise SystemExit(f"{len(missing)} author emails have no person_alias row")
    ids = {p: i for i, p in enumerate(sorted(set(person.values())))}
    person = {e: ids[p] for e, p in person.items()}  # never leaves this function
    fixes = fix_commits(commits, block["commit_ticket"], rule, since)
    feats = history(commits, files, person, fixes, d["prior_fix_days"])
    pos = labels(commits, block["bug_inducing"], d["window_days"])

    ct = block["commit_ticket"].sort_values(["sha", "key"])
    first = ct.drop_duplicates("sha").set_index("sha")
    bug = set(ct.sha[ct.effective_type == "Bug"])

    rows = commits[commits.authored_at >= f"{d['start_year']}-01-01T00:00:00Z"]
    df = pd.DataFrame({"sha": rows.sha.values, "authored_at": rows.authored_at.values,
                       "subject": rows.subject.values})
    df["year"] = df.authored_at.str[:4].astype(int)
    df["split"] = [split_of(y, a, cfg) for y, a in zip(df.year, df.authored_at)]
    df["label"] = df.sha.isin(pos).astype(int)
    f = pd.DataFrame.from_dict(feats, orient="index").loc[df.sha].reset_index(drop=True)
    df = pd.concat([df, f], axis=1)
    df["fix"] = df.sha.isin(bug).astype(int)
    df["has_ticket"] = df.sha.isin(first.index).astype(int)
    df["ticket_type"] = df.sha.map(first.effective_type).fillna("none")
    comp = first.components.map(lambda j: (json.loads(j) or ["none"])[0])
    df["component"] = df.sha.map(comp).fillna("none")
    df["is_minor"] = df.subject.str.match(MINOR).astype(int)
    df["exp_bucket"] = df.exp.map(exp_bucket)
    size = df.la + df.ld
    edges = [float(x) for x in np.quantile(size[df.split == "train"], [0.25, 0.5, 0.75])]
    df["size_bucket"] = ["Q1" if n <= edges[0] else "Q2" if n <= edges[1] else "Q3" if n <= edges[2] else "Q4"
                         for n in size]
    df = df.sort_values(["authored_at", "sha"], kind="mergesort").reset_index(drop=True)
    df = df[[n for n, _, _ in COLUMNS]]
    for n in INT:
        df[n] = df[n].astype("int64")
    for n in FLOAT:
        df[n] = df[n].astype("float64")
    for n in STR:
        df[n] = df[n].astype(str)
    check(df)
    return df, edges


def check(df):
    bad = [n for n in STR if df[n].str.contains("@", regex=False).any()]
    if bad:
        raise SystemExit(f"columns {bad} contain an '@': no email may reach the feature table")
    nan = [n for n in INT + FLOAT if df[n].isna().any()]
    if nan:
        raise SystemExit(f"columns {nan} have missing values")
    if not df.sha.is_unique:
        raise SystemExit("duplicate commits in the feature table")


def to_bytes(df):
    table = pa.Table.from_pandas(df, preserve_index=False).replace_schema_metadata(None)
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", compression_level=9)
    return sink.getvalue().to_pybytes()


def label_counts(df):
    by = lambda col: {str(k): {"N": int(len(g)), "positives": int(g.label.sum()),
                               "share": round(float(g.label.mean()), 4)} for k, g in df.groupby(col, sort=True)}
    return {"by_year": by("year"), "by_split": by("split")}


def schema(df, md5, edges, cfg, latest, block):
    return {
        "table": "features.parquet", "md5": md5, "rows": int(len(df)),
        "block_run_id": latest["run_id"], "snapshot_at": block["snapshot_at"], "block_status": block["status"],
        "config": {"data": cfg["data"], "split": {k: v for k, v in cfg["split"].items() if k != "test_allowed"}},
        "fix_rule": dict(zip(["fix", "fix_since"], fix_rule())),
        "columns": [{"name": n, "type": t, "meaning": m} for n, t, m in COLUMNS],
        "size_bucket_edges": {"Q1": f"<= {edges[0]:g}", "Q2": f"<= {edges[1]:g}", "Q3": f"<= {edges[2]:g}",
                              "Q4": f"> {edges[2]:g}"},
        "labels": label_counts(df),
        "notes": "Decisions and label checks: Model-Pipeline/packets/utkarsh-data/decisions.md",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m model.data", description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=str(HERE / "model.toml"))
    ap.add_argument("--published", default=None, help="folder with latest.json (default: $MERIDIAN_DATA/published)")
    ap.add_argument("--out", default=str(HERE / "data"))
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    db, latest = open_block(a.published or published_dir(), cfg["data"]["run_id"])
    block = read_block(db, cfg["data"]["run_id"], cfg["data"]["repo"])
    df, edges = build(block, cfg)
    data = to_bytes(df)
    md5 = hashlib.md5(data).hexdigest()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "features.parquet").write_bytes(data)
    (out / "features.schema.json").write_text(
        json.dumps(schema(df, md5, edges, cfg, latest, block), indent=1, sort_keys=False) + "\n", encoding="utf-8")
    s = label_counts(df)["by_split"]
    print(f"{len(df):,} rows  md5={md5}  {len(data) / 1e6:.2f} MB  "
          + "  ".join(f"{k}: {v['N']:,} ({v['positives']:,} positive)" for k, v in s.items()))


if __name__ == "__main__":
    sys.exit(main())
