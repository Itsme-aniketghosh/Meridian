"""GX · the weekly audit: settings.toml [validate]'s generic checks as a Great Expectations suite, with Data Docs.

    python -m meridian.gx --run-id <run_id> --config meridian.toml                       # docs under this data root
    python -m meridian.gx --run-id <run_id> --config meridian.toml --publish-root <root>  # the rebuild: docs where
    views read

Runs in its own Python environment ([gx] python: great_expectations and pandas from requirements-gx.txt), in the weekly
rebuild only, next to bias. Reads out/<run_id>/meridian.sqlite, which validate builds. Writes out/<run_id>/gx.json and
the Data Docs site to <publish root>/<[publish] dir>/gx/<run_id>/ (index.html), with gx/latest.json pointing at it.

It never gates. meridian.validate is the gate, on every update; nothing downstream of gx waits for it.
- A failed expectation: an alert ("warn") listing it, and the task still succeeds.
- A check GX couldn't evaluate (an exception inside GX, reported as a plain failure with no counts): an alert
  ("error") and exit 4 (validate.ALERTED: alerted already), so a broken audit is never read as clean data. The
  rebuild goes on without it.

The checks come from [validate], the same settings validate reads: rows, missing, formats, ranges and dates. The
domain checks stay in meridian.validate only: coverage floors, outliers, drift, sorted JSON lists.
"""
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from meridian import alert
from meridian.common import CONFIG, data_root, handoff, load_config, load_settings, log, parser
from meridian.validate import ALERTED

TIME = "%Y-%m-%dT%H:%M:%SZ"


def specs(exp, table, cols, snapshot_at):
    """One table's checks from [validate], as plain dicts (no GX needed to build them):
    {"kind": rows|not_null|regex|between|dates, "column", ..., "severity", "check"}."""
    out = []
    if need := exp.get("rows", {}).get(table):
        out.append({"kind": "rows", "min": need, "severity": "error", "check": "rows"})
    limits = exp.get("missing", {})
    for c in cols:
        limit = limits.get(f"{table}.{c}", limits.get(f"*.{c}", limits.get("default", 1.0)))
        if limit < 1.0:
            out.append({"kind": "not_null", "column": c, "mostly": round(1 - limit, 6), "severity": "warn",
                        "check": "missing"})
        for rule in exp.get("formats", []):
            if rule["pattern"] == "sorted_json_list" or not re.fullmatch(rule["columns"], c):
                continue   # sorted JSON lists stay in validate: GX has no such check
            if not rule.get("tables") or table in rule["tables"]:
                out.append({"kind": "regex", "column": c, "regex": f"^(?:{rule['pattern']})$", "name": rule["name"],
                            "severity": "error", "check": "formats"})
        for where, (lo, hi) in exp.get("ranges", {}).items():
            t, col = where.split(".")
            if col == c and t in (table, "*"):
                out.append({"kind": "between", "column": c, "min": lo, "max": hi, "severity": "error",
                            "check": "ranges"})
        if (d := exp.get("dates")) and re.fullmatch(d["columns"], c) and snapshot_at:
            out.append({"kind": "dates", "column": c, "min": d["earliest"], "max": snapshot_at, "severity": "warn",
                        "check": "dates"})
    return out


def as_datetime(v):
    """GX won't compare ISO strings, and pandas timestamps stop at year 1677: a plain datetime keeps year 0010.
    A value that isn't a UTC time is None here: its format check reports it, the date check doesn't count it twice."""
    try:
        return dt.datetime.strptime(v, TIME) if isinstance(v, str) and v else None
    except ValueError:
        return None


def expectation(gx, s):
    """(GX expectation, the column it reads) for one spec. A date check reads a copy of its column as datetimes, so
    the column's own format check still sees the ISO text."""
    E, meta = gx.expectations, {"severity": s["severity"], "check": s["check"]}
    if s["kind"] == "rows":
        return E.ExpectTableRowCountToBeBetween(min_value=s["min"], meta=meta), None
    if s["kind"] == "not_null":   # validate's "missing" counts NULL and '': '' is turned into NULL first (prepare)
        return E.ExpectColumnValuesToNotBeNull(column=s["column"], mostly=s["mostly"] or 1.0, meta=meta), s["column"]
    if s["kind"] == "regex":
        return E.ExpectColumnValuesToMatchRegex(column=s["column"], regex=s["regex"],
                                                meta=meta | {"name": s["name"]}), s["column"]
    if s["kind"] == "between":
        return E.ExpectColumnValuesToBeBetween(column=s["column"], min_value=s["min"], max_value=s["max"],
                                               meta=meta), s["column"]
    col = f"{s['column']}__datetime"
    return E.ExpectColumnValuesToBeBetween(column=col, min_value=as_datetime(s["min"]), max_value=as_datetime(s["max"]),
                                           meta=meta), col


def prepare(df, table_specs):
    """The frame as the checks need it: '' is missing (like validate), date columns get a datetime copy."""
    for s in table_specs:
        if s["kind"] == "not_null":
            df[s["column"]] = df[s["column"]].replace("", None)
        elif s["kind"] == "dates":
            df[f"{s['column']}__datetime"] = df[s["column"]].map(as_datetime).astype(object)
    return df


def crashed(r):
    """True when GX couldn't evaluate the expectation: its result says failed, with an exception and no counts."""
    info = r.exception_info or {}
    return bool(info.get("raised_exception")) or any(isinstance(v, dict) and v.get("raised_exception")
                                                     for v in info.values())


def audit(block, exp, project):
    """Run every table's suite on the block. -> (expectations run, failed, crashed, the Data Docs site folder)."""
    import great_expectations as gx   # only in [gx] python's environment
    import pandas as pd

    db = sqlite3.connect(block)
    try:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        run = db.execute("SELECT snapshot_at FROM run").fetchone() if "run" in tables else None
        ctx = gx.get_context(mode="file", project_root_dir=str(project))
        source = ctx.data_sources.add_or_update_pandas("meridian")
        n, failed, broken = 0, [], []
        for t in tables:
            cols = [r[1] for r in db.execute(f'PRAGMA table_info("{t}")')]
            table_specs = specs(exp, t, cols, run[0] if run else None)
            if not table_specs:
                continue
            df = prepare(pd.read_sql(f'SELECT * FROM "{t}"', db), table_specs)
            suite = ctx.suites.add_or_update(gx.ExpectationSuite(name=f"meridian_{t}"))
            for s in table_specs:
                suite.add_expectation(expectation(gx, s)[0])
            asset = source.add_dataframe_asset(name=t)
            check = gx.ValidationDefinition(name=f"meridian_{t}", data=asset.add_batch_definition_whole_dataframe(t),
                                            suite=suite)
            result = ctx.validation_definitions.add_or_update(check).run(batch_parameters={"dataframe": df},
                                                                         result_format="SUMMARY")
            for r in result.results:
                n += 1
                cfg, meta, res = r.expectation_config, r.expectation_config.meta or {}, r.result or {}
                where = f"{t}.{cfg.kwargs['column'].removesuffix('__datetime')}" if "column" in cfg.kwargs else t
                if crashed(r):
                    broken.append({"check": meta.get("check"), "where": where})
                elif not r.success and not (meta.get("check") == "dates" and (res.get("unexpected_count") or 0)
                                            <= exp.get("dates", {}).get("allow", {}).get(where, 0)):  # as validate
                    failed.append({"severity": meta.get("severity"), "check": meta.get("check"), "where": where,
                                   "unexpected": res.get("unexpected_count"), "of": res.get("element_count"),
                                   "examples": [str(v)[:60] for v in (res.get("partial_unexpected_list") or [])[:3]]})
        ctx.build_data_docs()
    finally:
        db.close()
    return n, failed, broken, project / "gx" / "uncommitted" / "data_docs" / "local_site"


def publish_docs(site, root, publish_dir, run_id):
    """Copy the Data Docs site to <root>/<publish_dir>/gx/<run_id>/ (replaced whole, through a temp folder), and
    point gx/latest.json at it. -> the index.html."""
    base = Path(root) / publish_dir / "gx"
    dest, tmp = base / run_id, base / f".{run_id}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(site, tmp)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    latest = base / "latest.json"
    latest.with_name("latest.json.tmp").write_text(json.dumps({"run_id": run_id,
                                                               "index": f"{run_id}/index.html"}) + "\n",
                                                   encoding="utf-8", newline="\n")
    latest.with_name("latest.json.tmp").replace(latest)
    return dest / "index.html"


def main(run_id, config=CONFIG, root=None, publish_root=None, settings=None):
    cfg, root = load_config(config), Path(root or data_root())
    out = handoff(cfg, run_id, root)
    block = out / "meridian.sqlite"
    if not block.exists():
        raise SystemExit(f"no {block}: run meridian.validate first (it builds the block)")
    exp, publish_dir = load_settings("validate", settings), load_settings("publish", settings).get("dir", "published")
    project = out / "gx_project"
    shutil.rmtree(project, ignore_errors=True)   # rebuilt every time: the run's docs are only this run's results
    n, failed, broken, site = audit(block, exp, project)
    index = publish_docs(site, publish_root or root, publish_dir, run_id)
    (out / "gx.json").write_text(json.dumps({"run_id": run_id, "expectations": n, "failed": failed, "crashed": broken,
                                             "docs": str(index)}, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8", newline="\n")
    log(f"{n} expectations · {len(failed)} failed · {len(broken)} couldn't run -> {index}")
    for f in failed:
        log(f"  {f['severity']:5} {f['check']:8} {f['where']:40} {f['unexpected']} of {f['of']} "
            f"e.g. {f['examples'][:2]}")
    if failed:
        alert.send(f"meridian GX {run_id}: {len(failed)} expectations failed (weekly audit)",
                   [f"{f['check']} {f['where']}: {f['unexpected']} of {f['of']}" for f in failed] + [f"docs: {index}"],
                   "warn")
    if broken:
        alert.send(f"meridian GX {run_id}: {len(broken)} checks couldn't run",
                   [f"{b['check']} {b['where']}" for b in broken] + [f"docs: {index}"], "error")
        return ALERTED
    return 0


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--publish-root", type=Path,
                    help="where the Data Docs go: the main data root (the rebuild passes it)")
    a = ap.parse_args()
    sys.exit(main(a.run_id, a.config, publish_root=a.publish_root))
