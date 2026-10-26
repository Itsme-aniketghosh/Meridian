"""Validate · schema, statistics and anomalies of one run's block, before anyone reads it.

    python -m meridian.validate --run-id <run_id> --config meridian.toml                       # first run
    python -m meridian.validate --run-id <run_id> --config meridian.toml --prev-run <run_id>   # and drift from the last

Reads out/<run_id>/*.jsonl and settings.toml [validate]. Writes out/<run_id>/meridian.sqlite (the block, by meridian.writer),
stats.json and anomalies.json. Neither JSON is a table: the writer loads only *.jsonl.

1. Schema. The block loads with meridian.writer against schema.sql: column names, types, NOT NULL, enums, one run_id,
   no duplicate key. Any violation is an error, and nothing else is checked.
2. Statistics, per table and column: rows, missing (NULL or ''), distinct, min / max / mean of numbers, the most
   common values of a column with few of them. Written whole to stats.json, so the next run can compare.
3. Checks, thresholds in settings.toml [validate] (not hashed into the run_id: a check never changes a row):
   rows       a table below its minimum                                   error
   formats    times, 16-hex IDs, 40-hex SHAs, 'owner/name' repos, repo-relative POSIX paths, sorted JSON lists  error
   ranges     a number outside its allowed range (share in 0..1, line >= 1, ...)                                error
   coverage   a coverage metric below its floor                           error
   missing    a column's share of NULL or '' above its limit              warn
   dates      a time before the earliest plausible date, or after the run's snapshot_at, above its allowance  warn
   outliers   a number column's share of values beyond Q1 - k*IQR or Q3 + k*IQR above the limit               warn
   drift      since --prev-run, a table of min_rows or more moved more than the limit, or a column became one value  warn
An error exits 1, so Airflow fails the task and alerts; warnings alert and the run goes on. Both go to meridian.alert.
"""
import json, math, re, sqlite3
from pathlib import Path

import meridian.writer as W
from meridian import alert
from meridian.common import CONFIG, data_root, handoff, load_config, load_settings, log, parser

NUMERIC = ("INTEGER", "REAL")


def column_stats(db, table, cols, top_n):
    """(rows, {column: stats}) for one table, all in SQL so a big table never sits in memory. One aggregate query
    reads the table once for every column's missing / distinct / min / max / mean; top values, one query per column
    that has them."""
    exprs = ["count(*)"]
    for name, typ, *_ in cols:
        q = f'"{name}"'
        exprs += [f"sum({q} IS NULL OR {q} = '')", f"count(DISTINCT {q})"] + ([f"min({q})", f"max({q})", f"avg({q})"] if typ in NUMERIC else [])
    got = iter(db.execute(f'SELECT {", ".join(exprs)} FROM "{table}"').fetchone())
    rows, out = next(got), {}
    for name, typ, *_ in cols:
        q = f'"{name}"'
        missing, distinct = next(got), next(got)
        s = {"missing": missing or 0, "distinct": distinct}
        if typ in NUMERIC:
            lo, hi, mean = next(got), next(got), next(got)
            s |= {"min": lo, "max": hi, "mean": None if mean is None else round(mean, 4)}
        if 0 < distinct <= top_n and distinct < rows:  # every value unique (run's image_digest): nothing common to list
            s["top"] = [list(r) for r in db.execute(
                f'SELECT {q}, count(*) FROM "{table}" GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {top_n}')]
        out[name] = s
    return rows, out


def quantile(db, table, col, n, p):
    """The p-quantile of a non-null number column with n values (nearest rank)."""
    return db.execute(f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL ORDER BY 1 LIMIT 1 OFFSET ?',
                      (max(0, math.ceil(p * n) - 1),)).fetchone()[0]


def outliers(db, table, col, k, log_scale=False):
    """(share of values beyond the IQR fences, low fence, high fence), or None when there are too few values.
    log_scale: fences on log(1 + x), for counts with a long tail (commit sizes), mapped back to x."""
    n = db.execute(f'SELECT count("{col}") FROM "{table}"').fetchone()[0]
    if n < 20:
        return None
    q1, q3 = quantile(db, table, col, n, 0.25), quantile(db, table, col, n, 0.75)
    if log_scale:
        a, b = math.log1p(q1), math.log1p(q3)
        lo, hi = math.expm1(a - k * (b - a)), math.expm1(b + k * (b - a))
    else:
        lo, hi = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
    far = db.execute(f'SELECT count(*) FROM "{table}" WHERE "{col}" < ? OR "{col}" > ?', (lo, hi)).fetchone()[0]
    return far / n, lo, hi


def sorted_json_list(v):
    try:
        x = json.loads(v)
    except (TypeError, ValueError):
        return False
    return isinstance(x, list) and x == sorted(x, key=lambda e: json.dumps(e, sort_keys=True))


def check(db, spec, stats, exp, prev):
    """[(severity, check, where, message)] for one block."""
    found = []
    add = lambda sev, kind, where, msg: found.append([sev, kind, where, msg])
    for table, need in sorted(exp.get("rows", {}).items()):
        if table in stats and stats[table]["rows"] < need:
            add("error", "rows", table, f"{stats[table]['rows']:,} rows, expected at least {need:,}")

    db.create_function("REGEXP", 2, lambda p, v: v is not None and re.fullmatch(p, str(v)) is not None, deterministic=True)
    db.create_function("SORTED_LIST", 1, sorted_json_list, deterministic=True)
    for table, cols in sorted(spec.items()):
        names = [c[0] for c in cols]
        for col in names:
            for rule in exp.get("formats", []):
                if not re.fullmatch(rule["columns"], col) or (rule.get("tables") and table not in rule["tables"]):
                    continue
                test = "NOT SORTED_LIST(\"{0}\")" if rule["pattern"] == "sorted_json_list" else "NOT \"{0}\" REGEXP ?"
                args = () if rule["pattern"] == "sorted_json_list" else (rule["pattern"],)
                bad = db.execute(f'SELECT count(*), min("{col}") FROM "{table}" WHERE "{col}" IS NOT NULL AND "{col}" != \'\' '
                                 f'AND {test.format(col)}', args).fetchone()
                if bad[0]:
                    add("error", "formats", f"{table}.{col}", f"{bad[0]:,} values aren't {rule['name']}, e.g. {str(bad[1])[:60]!r}")
            for where, (lo, hi) in exp.get("ranges", {}).items():
                t, c = where.split(".")
                if (t, c) == (table, col) or (t == "*" and c == col):
                    bad = db.execute(f'SELECT count(*) FROM "{table}" WHERE "{col}" < ? OR "{col}" > ?', (lo, hi)).fetchone()[0]
                    if bad:
                        add("error", "ranges", f"{table}.{col}", f"{bad:,} values outside {lo}..{hi}")

    dates = exp.get("dates", {})
    allow = dates.get("allow", {})  # bad dates the source itself has: warn only above this many, so an alert stays news
    if dates and (now := db.execute("SELECT snapshot_at FROM run").fetchone()):
        for table, cols in sorted(spec.items()):
            for col in [c[0] for c in cols if re.fullmatch(dates["columns"], c[0])]:
                bad = db.execute(f'SELECT count(*), min("{col}"), max("{col}") FROM "{table}" WHERE "{col}" != \'\' '
                                 f'AND ("{col}" < ? OR "{col}" > ?)', (dates["earliest"], now[0])).fetchone()
                if bad[0] > allow.get(f"{table}.{col}", 0):
                    add("warn", "dates", f"{table}.{col}", f"{bad[0]:,} dates before {dates['earliest'][:10]} or after "
                        f"snapshot_at, from {bad[1]} to {bad[2]}")

    for metric, floor in sorted(exp.get("coverage", {}).items()):
        row = db.execute("SELECT numerator, denominator FROM coverage WHERE metric = ?", (metric,)).fetchone()
        if row and row[1] and row[0] / row[1] < floor:
            add("error", "coverage", metric, f"{row[0]:,} / {row[1]:,} = {row[0] / row[1]:.3f}, floor {floor}")

    limits = exp.get("missing", {})
    for table, s in sorted(stats.items()):
        for col, cs in sorted(s["columns"].items()):
            limit = limits.get(f"{table}.{col}", limits.get(f"*.{col}", limits.get("default", 1.0)))
            if s["rows"] and cs["missing"] / s["rows"] > limit:
                add("warn", "missing", f"{table}.{col}", f"{cs['missing']:,} of {s['rows']:,} missing, limit {limit:.0%}")

    o = exp.get("outliers", {})
    for target in o.get("columns", []):
        table, col = target.split(".")
        if table in stats and (got := outliers(db, table, col, o.get("iqr_k", 3.0), col in o.get("log_scale", []))):
            share, lo, hi = got
            stats[table]["columns"][col]["outlier_share"] = round(share, 4)
            if share > o.get("max_share", 0.05):
                add("warn", "outliers", target, f"{share:.1%} of values outside {lo:.4g}..{hi:.4g} (IQR fences, k = {o.get('iqr_k', 3.0)})")

    if prev:
        limit, least = exp.get("drift", {}).get("max_row_change", 0.2), exp.get("drift", {}).get("min_rows", 100)
        for table, s in sorted(stats.items()):
            before = prev.get(table, {}).get("rows")
            if not before or before < least:  # a small table's swings are noise
                continue
            if abs(s["rows"] - before) / before > limit:
                add("warn", "drift", table, f"{before:,} -> {s['rows']:,} rows since the last run, limit {limit:.0%}")
            for col, cs in sorted(s["columns"].items()):
                was = prev[table].get("columns", {}).get(col, {}).get("distinct", 0)
                if was > 1 and cs["distinct"] == 1 and s["rows"] > 1:
                    add("warn", "drift", f"{table}.{col}", f"one value in every row, {was:,} distinct last run")
    return found


def main(run_id, config=CONFIG, root=None, prev_run=None, settings=None):
    cfg = load_config(config)
    out = handoff(cfg, run_id, Path(root or data_root()))
    exp = load_settings("validate", settings)
    for name in ("anomalies.json", "stats.json"):  # publish reads them: an earlier run's must never outlive a crash here
        (out / name).unlink(missing_ok=True)
    try:
        W.write(out, complete=True)  # the schema check: the writer refuses any row schema.sql doesn't allow, or a missing table
    except (W.BadRow, OSError) as e:
        kind, why = ("schema", "the block breaks schema.sql") if isinstance(e, W.BadRow) else ("write", f"could not write the block: {e}")
        alert.send(f"meridian {run_id}: {why}", [str(e)], "error", settings)  # first: a full disk may refuse anomalies.json too
        (out / "anomalies.json").write_text(json.dumps([["error", kind, "", str(e)]], indent=1) + "\n", encoding="utf-8", newline="\n")
        raise SystemExit(f"{kind}: {e}")

    spec = W.tables(W.SCHEMA.read_text(encoding="utf-8"))
    with sqlite3.connect(out / "meridian.sqlite") as db:
        stats = {}
        for table, cols in sorted(spec.items()):
            rows, columns = column_stats(db, table, cols, exp.get("top_values", 10))
            stats[table] = {"rows": rows, "columns": columns}
        prev = None
        if prev_run:
            p = handoff(cfg, prev_run, Path(root or data_root())) / "stats.json"
            if not p.exists():
                raise SystemExit(f"no {p}: validate the previous run first")
            prev = json.loads(p.read_text(encoding="utf-8"))["tables"]
        found = check(db, spec, stats, exp, prev)
    db.close()

    (out / "stats.json").write_text(json.dumps({"run_id": run_id, "tables": stats}, indent=1, sort_keys=True) + "\n",
                                    encoding="utf-8", newline="\n")
    (out / "anomalies.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8", newline="\n")
    for sev, kind, where, msg in found:
        log(f"  {sev:5} {kind:9} {where:40} {msg}")
    errors = [f for f in found if f[0] == "error"]
    log(f"{sum(s['rows'] for s in stats.values()):,} rows in {len(stats)} tables · {len(errors)} errors · "
        f"{len(found) - len(errors)} warnings -> {out / 'stats.json'}, {out / 'anomalies.json'}")
    if found:
        alert.send(f"meridian {run_id}: {len(errors)} errors, {len(found) - len(errors)} warnings in the data",
                   [f"{sev} {kind} {where}: {msg}" for sev, kind, where, msg in found], "error" if errors else "warn", settings)
    if errors:
        raise SystemExit(f"{len(errors)} data errors: see {out / 'anomalies.json'}")
    return stats, found


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--prev-run", default=None, help="the last validated run, to measure drift against "
                    "(default: settings.toml [pipeline] prev_run)")
    a = ap.parse_args()
    main(a.run_id, a.config, prev_run=a.prev_run or load_settings("pipeline").get("prev_run") or None)
