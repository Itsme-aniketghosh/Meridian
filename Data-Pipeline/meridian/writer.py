"""Writer · step 1 · D3: load out/<run_id>/*.jsonl into one SQLite file, using schema.sql. See CONTRACTS.md.

    python -m meridian.writer --run-id <run_id> --config meridian.toml        # -> out/<run_id>/meridian.sqlite
    python -m meridian.writer --run-id dev --db /tmp/dev.sqlite

Every row is checked against the schema (column names, types, NOT NULL, run_id, duplicate keys) before anything is
written. Then rows go in sorted by primary key, tables in name order, in one transaction, into a fresh file, which is
VACUUMed and moved into place.
Any bad row stops the load with its file and line: a half-right database is worse than none.

Every row carries the folder's run_id, except in a table that keeps rows across runs (KNOWN_AT: call_site and the
stage-6 tracking tables). Those hold every row so far, so earlier run_ids are allowed there, never a row known after
this run's snapshot_at. A tracking table without a run_id column is checked on its *_at column alone.

A column with REFERENCES in schema.sql must name a row of that table in the same block (a NULL names nothing).
The tracking tables (TRACKING) come from the product, not from a run: a complete block may leave their files out,
and they load empty.
"""
import json
import sqlite3
import sys
from pathlib import Path

from meridian.common import SCHEMA, handoff, load_config, log, parser

PY_TYPES = {"TEXT": (str,), "INTEGER": (int,), "REAL": (int, float)}
# Tables whose rows from earlier runs ride along, and the column that says when each became known
KNOWN_AT = {"call_site": "known_at", "suggestion": "offered_at", "outcome": "decided_at", "push_alert": "decided_at",
            "policy": "created_at", "match": "decided_at"}
# Stage 6: written by the product and the auditor, not by a pipeline run. Missing from a complete block = empty
TRACKING = {"suggestion", "outcome", "push_alert", "policy", "match"}


class BadRow(Exception):
    pass


def tables(schema_sql):
    """{table: [(column, type, not_null, has_default, pk_position)]}, read from the schema itself so the writer never
    drifts."""
    db = sqlite3.connect(":memory:")
    db.executescript(schema_sql)
    spec = {}
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
        spec[t] = [(c[1], c[2].upper(), bool(c[3]), c[4] is not None, c[5])
                   for c in db.execute(f"PRAGMA table_info('{t}')")]
    return spec


def references(schema_sql):
    """[(table, column, parent table, parent column)] from schema.sql's REFERENCES clauses."""
    db = sqlite3.connect(":memory:")
    db.executescript(schema_sql)
    return [(t, fk[3], fk[2], fk[4])
            for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
            for fk in db.execute(f"PRAGMA foreign_key_list('{t}')")]


def check_references(loaded, refs):
    for table, col, parent, pcol in refs:
        keys = {r[pcol] for _, r in loaded.get(parent, [])}
        for where, r in loaded.get(table, []):
            if r.get(col) is not None and r[col] not in keys:
                raise BadRow(f"{where}: {col} {r[col]!r} has no {parent} row with that {pcol}")


def read_rows(path, cols, run_id, snapshot_at=None):
    """Checked rows of one table, as (where, row), sorted by primary key."""
    by_name = {c[0]: c for c in cols}
    required = {c[0] for c in cols if c[2] and not c[3]}
    pk = [c[0] for c in sorted((c for c in cols if c[4]), key=lambda c: c[4])]
    rows = []
    with open(path, "rb") as f:  # bytes, so a bad byte is refused with its line, not a bare traceback
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            where = f"{path.name}:{n}"
            try:
                r = json.loads(line.decode("utf-8"))
            except UnicodeDecodeError as e:
                raise BadRow(f"{where}: not UTF-8 ({e})")
            except json.JSONDecodeError as e:
                raise BadRow(f"{where}: not JSON ({e})")
            if not isinstance(r, dict):
                raise BadRow(f"{where}: not a JSON object")
            if extra := sorted(set(r) - set(by_name)):
                raise BadRow(f"{where}: columns not in schema.sql: {extra}")
            if missing := sorted(required - set(r)):
                raise BadRow(f"{where}: missing columns: {missing}")
            for k, v in r.items():
                _, typ, not_null, _, _ = by_name[k]
                if v is None:
                    if not_null:
                        raise BadRow(f"{where}: {k} is null")
                elif isinstance(v, bool) or not isinstance(v, PY_TYPES.get(typ, (object,))):
                    raise BadRow(f"{where}: {k} should be {typ}, got {type(v).__name__} {str(v)[:40]!r}")
            known = KNOWN_AT.get(path.stem)
            if "run_id" in by_name and r["run_id"] != run_id:
                if not known:
                    raise BadRow(f"{where}: run_id {r['run_id']!r}, but the folder is {run_id!r}")
                if snapshot_at is None or r[known] > snapshot_at:
                    raise BadRow(f"{where}: an earlier run's version (run_id {r['run_id']!r}) must be known by this "
                                 f"run's snapshot_at {snapshot_at!r}, it says {r[known]!r}")
            elif "run_id" not in by_name and known and r.get(known) is not None and snapshot_at is not None \
                    and r[known] > snapshot_at:
                raise BadRow(f"{where}: {known} {r[known]!r} is after this run's snapshot_at {snapshot_at!r}")
            rows.append((tuple(r[k] for k in pk), where, r))
    rows.sort(key=lambda x: x[0])
    for a, b in zip(rows, rows[1:]):
        if a[0] == b[0]:
            raise BadRow(f"{b[1]}: same primary key as {a[1]}: {dict(zip(pk, a[0]))}")
    return [(where, r) for _, where, r in rows]


def write(src, db=None, schema=SCHEMA, complete=False):
    """complete: every table's file must be there (validate's block). Off, a part's tests load just their tables."""
    src = Path(src)
    run_id = src.name
    db = Path(db) if db else src / "meridian.sqlite"
    run = src / "run.jsonl"
    if not src.is_dir() or complete and not run.exists():
        raise SystemExit(f"no {run}: run meridian.collect (and the parts before validate) first")
    sql = Path(schema).read_text(encoding="utf-8")
    spec = tables(sql)
    files = sorted(src.glob("*.jsonl"))
    if unknown := [p.name for p in files if p.stem not in spec]:
        raise BadRow(f"no table in schema.sql for {unknown}")
    if complete and (missing := sorted(set(spec) - TRACKING - {p.stem for p in files})):
        raise BadRow(f"no {', '.join(t + '.jsonl' for t in missing)}: a part didn't write its table "
                     "(an empty file is an empty table)")
    try:
        snapshot_at = (json.loads(run.read_text(encoding="utf-8").split("\n")[0])["snapshot_at"]
                       if run.exists() and run.stat().st_size else None)
    except (ValueError, KeyError, TypeError) as e:  # UnicodeDecodeError is a ValueError
        raise BadRow(f"{run.name}:1: can't read snapshot_at ({type(e).__name__}: {e})")
    loaded = {p.stem: read_rows(p, spec[p.stem], run_id, snapshot_at) for p in files}
    check_references(loaded, references(sql))

    tmp = db.with_name(db.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp, isolation_level=None)
    try:
        con.executescript(sql)
        con.execute("BEGIN")
        for t in sorted(loaded):
            for where, r in loaded[t]:
                cols = [c[0] for c in spec[t] if c[0] in r]  # a left-out column takes its schema DEFAULT
                try:
                    con.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                [r[c] for c in cols])
                except sqlite3.IntegrityError as e:  # CHECK constraints: enums, 0/1, depth
                    raise BadRow(f"{where}: {e}")
        con.execute("COMMIT")
        con.execute("VACUUM")
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    tmp.replace(db)
    return {t: len(loaded.get(t, [])) for t in spec}


if __name__ == "__main__":
    ap = parser(__doc__, "loads out/<run_id>/")
    ap.add_argument("--db", help="default: out/<run_id>/meridian.sqlite")
    a = ap.parse_args()
    try:
        counts = write(handoff(load_config(a.config), a.run_id), a.db)
    except BadRow as e:
        sys.exit(f"not written: {e}")
    for t, n in counts.items():
        if n:
            log(f"{n:>10,}  {t}")
    log(f"{sum(1 for n in counts.values() if not n)} tables empty: {', '.join(t for t, n in counts.items() if not n)}")
