"""Meridian data pipeline: a guided demo for graders. Run from Data-Pipeline/:

    python run_demo.py short       # ~1-3 min, no team data, no Docker: fresh clone + pip install -r requirements.txt
    python run_demo.py long        # everything: short, all unit tests, GX, data download, team-run tests,
                                   # Airflow (Docker)
    python run_demo.py download    # only get the data: raw ticket pulls, then collect's repo checkouts

Options: --zip <path-or-url> (the team zip), --public-pull (pull Jira/GitLab from the public APIs: slow, a run of
your own), --airflow-run (trigger meridian_update and wait), --force (run a step even below its RAM/disk guard),
--keep (keep the temp folder). MERIDIAN_DEMO_NO_DOCKER=1 skips every docker command.

Each step prints the rubric item it shows, the command it runs, the result, and one line on what you just saw.
Everything it writes goes to a temp folder, except `download` (snapshots/) and long's Docker step.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
GB = 1024 ** 3
PY = sys.executable
ZIP_NAME = "meridian-raw-973531f7fd4cfd5e.zip"
ZIP_SHA = "d422ba26c976beeca953e8831e0f8f0eaad339a0359cdbd6fe4b9ea6c8f2d523"
RESULTS = []          # (step, PASS/SKIP/FAIL, note)
ARGS = TMP = None
STATE = {}


class Skip(Exception):
    pass


# ---------------------------------------------------------------- printing and running

def heading(title):
    print("\n" + "=" * 100 + f"\n[step {len(RESULTS) + 1}/{STATE['n']}]  {title}\n" + "=" * 100, flush=True)


def saw(text):
    print("\n" + textwrap.fill("WHAT YOU JUST SAW: " + text, 100, subsequent_indent="  "), flush=True)


def show(cmd, env=None, cwd=HERE):
    pre = "".join(f"{k}={v} " for k, v in (env or {}).items())
    where = "" if Path(cwd) == HERE else f"(in {cwd}) "
    print(f"\n$ {where}{pre}" + " ".join("python" if a == PY else (f'"{a}"' if " " in a or "{" in a else a)
                                         for a in cmd),
          flush=True)


def run(cmd, env=None, cwd=HERE, timeout=None, quiet=False):
    """Show the command, run it, stream its output indented. Returns (exit code, output)."""
    show(cmd, env, cwd)
    full = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    p = subprocess.Popen(cmd, cwd=cwd, env=full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    out, start = [], time.monotonic()
    for line in p.stdout:
        out.append(line)
        if not quiet:
            print("  " + line.rstrip(), flush=True)
        if timeout and time.monotonic() - start > timeout:
            p.kill()
            raise Skip(f"timed out after {timeout} s")
    return p.wait(), "".join(out)


def table(rows, header):
    rows = [[str(c) for c in r] for r in rows]
    w = [max(len(x) for x in col) for col in zip(header, *rows)]
    for r in [header, ["-" * n for n in w], *rows]:
        print("  " + "  ".join(c.ljust(n) for c, n in zip(r, w)))


def step(title, fn):
    heading(title)
    try:
        status, note = "PASS", fn() or ""
    except Skip as e:
        status, note = "SKIP", str(e)
        print(f"\nSKIPPED: {e}")
    except SystemExit as e:
        status, note = "FAIL", f"exited: {e}"
        print(f"\nFAILED: {e}")
    except Exception as e:
        status, note = "FAIL", f"{type(e).__name__}: {e}"
        traceback.print_exc()
        print(f"\nFAILED: {note}")
    RESULTS.append((title.split(" (")[0], status, note.splitlines()[0][:60] if note else ""))


# ---------------------------------------------------------------- resources and guards

def free_ram_gb():
    """Free (available) memory in GB, stdlib only; psutil if it happens to be installed. None if unknown."""
    try:
        import psutil
        return psutil.virtual_memory().available / GB
    except ImportError:
        pass
    try:
        if sys.platform == "win32":
            import ctypes

            class Mem(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("total", "avail", "tpage", "apage", "tvirt", "avirt", "aext")]
            m = Mem()
            m.length = ctypes.sizeof(Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.avail / GB
        if Path("/proc/meminfo").exists():
            kb = re.search(r"MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text())
            return int(kb.group(1)) * 1024 / GB
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
            page = int(re.search(r"page size of (\d+)", out).group(1))
            return sum(int(n) for _, n in re.findall(r"Pages (free|inactive|speculative):\s+(\d+)", out)) * page / GB
    except Exception:
        pass
    return None


def free_disk_gb(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free / GB


def reachable(host):
    try:
        socket.create_connection((host, 443), timeout=5).close()
        return True
    except OSError:
        return False


def guard(ram=0, disk=0, disk_at=None, hosts=()):
    """Skip the step (recorded as SKIP) unless there is enough RAM, disk and network. --force overrides."""
    if problem := resources_short(ram, disk, disk_at, hosts):
        raise Skip(problem)


def resources_short(ram=0, disk=0, disk_at=None, hosts=()):
    """What is short, or "" (also "" under --force, after saying what it overrides)."""
    problems = []
    if ram and (r := free_ram_gb()) is not None and r < ram:
        problems.append(f"low memory: {r:.1f} GB free, needs {ram} GB (close other apps)")
    if disk and (d := free_disk_gb(disk_at or data_root())) < disk:
        problems.append(f"low disk: {d:.1f} GB free at {disk_at or data_root()}, needs {disk:.1f} GB")
    problems += [f"no network: can't reach {h}" for h in hosts if not reachable(h)]
    if problems and not ARGS.force:
        return "; ".join(problems) + " (--force runs it anyway)"
    for p in problems:
        print(f"  guard overridden by --force: {p}")
    return ""


def data_root():
    return Path(os.environ.get("MERIDIAN_DATA") or REPO)


def settings():
    import tomllib
    return tomllib.loads((HERE / "settings.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- short

def env_check():
    ram, ok = free_ram_gb(), sys.version_info[:2] == (3, 11)
    rows = [["Python", platform.python_version(),
             "ok" if ok else "the team uses 3.11: other versions may give other bytes"],
            ["OS", f"{platform.system()} {platform.release()}", ""],
            ["free RAM", f"{ram:.1f} GB" if ram is not None else "unknown", "full pipeline needs 3 GB"],
            ["free disk", f"{free_disk_gb(data_root()):.1f} GB", f"at {data_root()}; full pipeline needs 5 GB"],
            ["git", "yes" if shutil.which("git") else "MISSING", "needed by the push check and collect"],
            ["docker CLI", "yes" if shutil.which("docker") else "no", "only long's Airflow step"]]
    missing = []
    for mod in ("tree_sitter", "tree_sitter_python", "jedi", "pandas", "numpy", "fairlearn", "sklearn", "dvc"):
        found = importlib.util.find_spec(mod) is not None
        rows.append([mod, "yes" if found else "MISSING", ""])
        missing += [] if found or mod == "dvc" else [mod]
    table(rows, ["check", "value", "note"])
    if missing:
        print("\nFIX: create the environment, then rerun:\n"
              "  conda create -y -n meridian python=3.11 && conda activate meridian\n"
              "  pip install -r requirements.txt        (from the repo root)")
        raise SystemExit(f"missing packages: {', '.join(missing)}")
    saw("the machine and the pinned dependencies from requirements.txt. "
        "Python 3.11 + these imports = the pipeline runs.")
    return f"Python {platform.python_version()}, deps ok"


def dags():
    from dags.pipeline_spec import DAGS, GATES, STEPS
    show([PY, "-c", "from dags.pipeline_spec import DAGS; print(DAGS)"])
    for name, tasks in DAGS.items():
        print(f"\n  DAG meridian_{name}  ({len(tasks)} tasks; task <- the tasks it waits for)")
        for t, up in tasks.items():
            gate = f"   [gate: exit {GATES[t][0]} = go on, {GATES[t][1]} = stop]" if t in GATES else ""
            writes = ", ".join(STEPS[t][2]) if t in STEPS and STEPS[t][2] else ""
            print(f"    {t:13} <- {', '.join(up) or '(start)':28} {('writes ' + writes)[:60] if writes else ''}{gate}")
    saw("both Airflow DAGs as data (dags/meridian_dag.py builds them from this file; tests/test_dag.py checks it). "
        "history and scan->resolve both wait only on collect, so they run side by side: that parallel split is the "
        "flow optimisation (rubric 9; ~1,130 s in sequence -> ~700 s, README 'Pipeline flow').")


def dvc():
    if importlib.util.find_spec("dvc") is None:
        print("  dvc isn't installed: the stages, from dvc.yaml:")
        for name in re.findall(r"^  (\w+):\n    cmd:", (HERE / "dvc.yaml").read_text(encoding="utf-8"), re.M):
            print(f"    {name}")
    else:
        rc, out = run([PY, "-m", "dvc", "dag", "--mermaid"], quiet=True)
        nodes = dict(re.findall(r"(node\d+)\[\"(\w+)\"\]", out))
        ups = {}
        for a, b in re.findall(r"(node\d+)-->(node\d+)", out):
            ups.setdefault(nodes[b], []).append(nodes[a])
        if rc or not nodes:
            print(out)
            raise SystemExit("dvc dag failed (another dvc command may hold the lock)")
        print("  stage <- the stages whose outputs it reads")
        for s in sorted(nodes.values(), key=lambda s: len(ups.get(s, []))):
            print(f"    {s:9} <- {', '.join(sorted(ups.get(s, []))) or '(raw inputs)'}")
    for f in ("snapshots/_cache/jira.dvc", "snapshots/_cache/gitlab.dvc"):
        size = re.search(r"size: (\d+)", (REPO / f).read_text())
        print(f"  {f}: pins the raw pull ({int(size.group(1)) / 1e6:.0f} MB) by hash")
    saw("DVC's view of the same pipeline (dvc.yaml: one stage per part; `dvc repro` reruns what changed). dvc.lock "
        "in git holds every output's hash, the .dvc files pin the raw ticket pulls; publish runs dvc commit + push.")


def fixture_pipeline():
    import tests.test_kb  # noqa: F401  (the fixture's story: KNOWLEDGE-BASE.md)
    from tests.test_dashboard import fixture_block
    from tests.test_graph import CONFIG
    (TMP / "meridian.toml").write_text(CONFIG)
    show([PY, "-c", "from tests.test_dashboard import fixture_block; fixture_block(TMP)"])
    out = fixture_block(TMP)
    print(f"  wrote {len(list(out.glob('*.jsonl')))} input tables to {out}")
    env = {"MERIDIAN_DATA": str(TMP)}
    for cmd in (["graph"], ["writer"]):
        rc, _ = run([PY, "-m", f"meridian.{cmd[0]}", "--run-id", "R2", "--config", str(TMP / "meridian.toml")], env)
        if rc:
            raise SystemExit(f"meridian.{cmd[0]} exited {rc}")
    with sqlite3.connect(out / "meridian.sqlite") as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        rows = [[t, db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]] for t in names]
    db.close()
    print()
    table([r for r in rows if r[1]], ["table", "rows"])
    print(f"  ({sum(1 for r in rows if not r[1])} more tables empty in this fixture)")
    saw("the acme/shop fixture (tests/test_kb.py) stands in for collect -> join's outputs, which need the private "
        "repos and tickets; graph then computed blocked_by / blast_radius / chain for real, and the writer loaded "
        "every table into one SQLite block against schema.sql. Same commands, same code as the team run.")
    return f"{sum(r[1] for r in rows)} rows in {sum(1 for r in rows if r[1])} tables"


def golden():
    rc, out = run([PY, "-m", "unittest", "tests.test_golden", "-v"])
    if rc:
        raise SystemExit("fixture bytes differ from golden/fixture.json")
    saw("the fixture through graph and the writer gives byte-identical output to golden/fixture.json (sha256 of every "
        "file and table). The team-run half (skipped here) checks the real outputs against dvc.lock's hashes.")


def write_settings(path, missing="", extra=""):
    """test_validate's settings for the fixture; alerts on, but no Slack/email channel: logged only."""
    from tests.test_validate import BASE
    base = textwrap.dedent(BASE.format(floor=0.5, missing=1.0)).replace("default = 1.0\n", "default = 1.0\n" + missing)
    path.write_text(base + extra +
                    '[alerts]\non = ["failure", "error", "warn"]\nslack_webhook_env = "MERIDIAN_DEMO_NO_CHANNEL"\n')


def validate():
    write_settings(TMP / "settings.toml")
    env = {"MERIDIAN_DATA": str(TMP), "MERIDIAN_SETTINGS": str(TMP / "settings.toml")}
    rc, _ = run([PY, "-m", "meridian.validate", "--run-id", "R2", "--config", str(TMP / "meridian.toml")], env)
    if rc:
        raise SystemExit(f"validate failed on the clean fixture (exit {rc})")
    stats = json.loads((TMP / "out/R2/stats.json").read_text())["tables"]
    print("\n  stats.json, call_site (one of 25 tables):")
    table([[c, s["missing"], s["distinct"], s.get("min", ""), s.get("max", ""),
            s.get("top", "")[:2] if s.get("top") else ""]
           for c, s in stats["call_site"]["columns"].items() if c in ("status", "bug_fixes", "owner_team",
                                                                      "line", "known_at")],
          ["column", "missing", "distinct", "min", "max", "top values"])
    saw("validate = schema (the writer refuses any row schema.sql forbids), then statistics per table and column "
        "into stats.json (next run's drift baseline), then the checks. Clean fixture: 0 errors, 0 warnings. The "
        "weekly rebuild repeats these checks as a Great Expectations suite (long mode).")


def anomalies():
    bad = TMP / "bad"
    shutil.copytree(TMP / "out", bad / "out")
    shutil.copy(TMP / "meridian.toml", bad)
    write_settings(bad / "settings.toml", '"commit_file.deleted" = 0.0\n',
                   '[validate.outliers]\ncolumns = ["commit_file.added"]\n')
    print("  injected into a copy of the block:")
    for name, col, value in (("ticket", "created_at", "06/01/2026 9am"), ("file_owner", "share", 1.7),
                             ("commit_file", "deleted", None), ("commit_file", "added", 250000)):
        p = bad / "out/R2" / f"{name}.jsonl"
        rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
        print(f"    {name}.{col}: {rows[-1][col]!r} -> {value!r}")
        rows[-1][col] = value
        if (name, col) == ("commit_file", "added"):   # outliers need >= 20 values: 10 more ordinary file changes
            rows += [dict(rows[0], path=f"docs/page{i}.md", added=i % 5 + 1, deleted=0) for i in range(10)]
            print("    commit_file: + 10 ordinary rows (1-5 lines added), so the IQR check has 25 values")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    env = {"MERIDIAN_DATA": str(bad), "MERIDIAN_SETTINGS": str(bad / "settings.toml")}
    rc, out = run([PY, "-m", "meridian.validate", "--run-id", "R2", "--config", str(bad / "meridian.toml")], env)
    found = json.loads((bad / "out/R2/anomalies.json").read_text())
    print(f"\n  exit code {rc}; anomalies.json:")
    table([f[:3] + [f[3][:50]] for f in found], ["severity", "check", "where", "detail"])
    if not rc or "ALERT" not in out or {f[1] for f in found} != {"formats", "ranges", "missing", "outliers"}:
        raise SystemExit("validate did not catch all four anomalies")
    saw("validate caught all four: the bad time format and the out-of-range share are errors (non-zero exit; in "
        "Airflow the task retries, then fails, publish never runs and the last good block stays live), the missing "
        "value and the outlier (IQR fences) are warnings (the run goes on). Either raises the ALERT. No Slack/email is "
        "set here, so it is logged; settings.toml [alerts] + .env send the same text to Slack and/or email.")
    return f"exit {rc}, {len(found)} anomalies, alert raised"


def bias():
    import tests.test_bias as TB
    show([PY, "-c", "import tests.test_bias as TB; TB.Bias.setUpClass()"])
    TB.Bias.setUpClass()   # writes the synthetic Spark-like history test_bias slices
    try:
        tmp = TB.Bias.tmp
        env = {"MERIDIAN_DATA": str(tmp), "MERIDIAN_SETTINGS": str(tmp / "settings.toml")}
        rc, _ = run([PY, "-m", "meridian.bias", "--run-id", "R", "--config", str(tmp / "meridian.toml")], env)
        if rc:
            raise SystemExit(f"meridian.bias exited {rc}")
        report = json.loads((tmp / "out/R/bias.json").read_text())
    finally:
        TB.Bias.tearDownClass()
    rows, flagged = [], 0
    for part in ("owner_rule", "ticket_mapping", "resolver"):
        for name, r in sorted(report.get(part, {}).items()):
            if not isinstance(r, dict) or "overall" not in r:
                continue   # mitigations and other summaries, below
            metric = next(m for m in r["overall"] if m != "n")
            bad = {f["slice"] for f in r.get("flagged", [])}
            flagged += len(bad)
            for g, v in r["by_group"].items():
                rows.append([part, name, g, f"{v[metric]:.2f}", f"{r['overall'][metric]:.2f}", int(v["n"]),
                             "FLAGGED" if g in bad else ""])
    print("\n  every slice (bias.json):")
    table(rows, ["rule", "metric by slice", "slice", "slice value", "overall", "N", ""])
    print("\n  owner-rule mitigations, scored:")
    table([[k, f"{m.get('files_owned')}/{m.get('of')}", m.get("accuracy"), m.get("flagged_modules")]
           for k, m in report.get("owner_rule", {}).get("mitigations", {}).items()],
          ["owner-rule variant", "files owned", "accuracy", "modules flagged"])
    saw("bias.py slices the pipeline's three deciding rules (owner rule, ticket mapping, resolver) by module, team, "
        "component, year and repo with Fairlearn's MetricFrame and flags slices that fall below overall (the slices "
        "here are tiny, so few or none clear the bar). The 'core' module is unowned: its author has no team in 180 "
        "days, and the 730-day lookback variant owns it. Real-run findings, the mitigation chosen and its trade-offs: "
        "BIAS.md.")
    return f"{len(rows)} slices, {flagged} flagged"


def situations():
    import tests.test_kb as K
    q = {name: (sql, params, expect) for name, sql, params, expect in K.blocks()}
    db = sqlite3.connect(TMP / "out/R2/meridian.sqlite")
    db.execute("CREATE TEMP TABLE asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT)")
    db.execute("INSERT INTO asof VALUES (?, ?, ?, ?)", (*K.R2, K.REPO, K.SYMBOL))
    db.executescript(q["setup"][0])
    bad = []
    for name, header, who in (("staff_headline", ["call sites left", "open tickets on them"], "Staff engineer"),
                              ("owner_teams", ["team", "sites left", "risky", "days idle", "status"], "API owner")):
        sql, params, expect = q[name]
        got = [list(r) for r in db.execute(sql, params)]
        print(f"\n  {who}: `-- name: {name}` from KNOWLEDGE-BASE.md, on the block built above")
        table(got, header)
        print(f"  doc expects {expect}: {'same' if got == expect else 'DIFFERENT'}")
        bad += [] if got == expect else [name]
    db.close()

    if not shutil.which("git"):
        raise Skip("git isn't installed: the push check needs it")
    import tests.test_pushcheck as T
    co, base = TMP / "shop", TMP / "shop-base.sqlite"
    co.mkdir()
    print("\n  Push check: a repo whose base commit is scanned into a block, then Pia (payments) pushes one commit")
    T.sh(co, "init", "-q")
    T.commit(co, T.BASE, "ann", "start")
    T.block(base, co)
    T.commit(co, T.HEAD, "pia", "PAY-99 add refund, see CHK-1")
    (TMP / "shop.toml").write_text(T.CFG_TEXT, encoding="utf-8")
    (TMP / "push.toml").write_text("[push_check]\n" + "".join(f"{k} = {v}\n" for k, v in T.SETTINGS.items()))
    rc, out = run([PY, "-m", "meridian.pushcheck", "--base", str(base), "--repo", T.REPO, "--changed", "HEAD~1...HEAD",
                   "--checkout", str(co), "--config", str(TMP / "shop.toml")],
                  {"MERIDIAN_SETTINGS": str(TMP / "push.toml")})
    checks = sorted(set(re.findall(r"\*\*(P\d) ", out)))
    if rc or bad:
        raise SystemExit(f"pushcheck exit {rc}; doc mismatch: {bad}")
    saw("what the block is for: the staff engineer's one-line headline, the API owner's per-team status (done / "
        "blocked / stalled / moving), and a push check: one PR comment listing P1-P9 findings, read-only on the block. "
        "All 43 KNOWLEDGE-BASE.md queries are checked the same way by tests/test_kb.py.")
    return f"2 views as documented; push check raised {checks[0]}-{checks[-1]}"


def next_steps():
    port = "8080"
    print(textwrap.dedent(f"""\
        - report/REPORT.md       the rubric checklist (section 3) with where each item lives, results on the team run
        - README.md              setup, run, folder structure, DVC, data quality, alerts, bias, flow
        - BIAS.md                bias findings, mitigation and trade-offs;   TESTING.md: every test and how to run it
        - Airflow UI (needs Docker, ~3 GB free RAM), from Data-Pipeline/:
              cp .env.example .env               (set AIRFLOW_ADMIN_PASSWORD)
              docker compose up -d --build
              open http://localhost:{port}  -> unpause meridian_update -> Trigger DAG w/ config {{"force": true}}
              a run -> Graph (tasks, logs per task) and Gantt (history beside scan->resolve: the bottleneck view)
        - python run_demo.py long      everything: all unit tests, GX, data download, team-run tests, Airflow"""))
    saw("where to read further. Nothing above was written outside the temp folder.")


# ---------------------------------------------------------------- long

def unit_tests():
    guard(ram=0.5)
    env = {k: v for k, v in os.environ.items() if k != "MERIDIAN_RUN_ID"}   # the no-team-data suite, as CI runs it
    show([PY, "-m", "unittest", "discover", "-s", "tests", "-t", "."])
    p = subprocess.run([PY, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=HERE, env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = p.stderr.strip().splitlines()
    print("  " + "\n  ".join(lines[-4:] if not p.returncode else lines[-40:]))
    if p.returncode:
        raise SystemExit("unit tests failed")
    saw("one test file per part plus the DAGs, validation, alerts, bias, the KB queries, push checks and golden "
        "bytes. Skips are by design: team data, Airflow's DagBag (Docker), GX (its own environment).")
    return next((l for l in reversed(lines) if l.startswith("Ran ")), "") + " " + lines[-1]


def gx():
    gx_py = os.environ.get("MERIDIAN_GX_PYTHON") or (PY if importlib.util.find_spec("great_expectations") else "")
    if not gx_py:
        raise Skip("no GX environment. Make one: python -m venv gx-env && "
                   "gx-env/bin/pip install -r requirements-gx.txt (Windows: gx-env\\Scripts\\pip), "
                   "then MERIDIAN_GX_PYTHON=<that python> python run_demo.py long")
    rc, _ = run([gx_py, "-m", "unittest", "tests.test_gx", "-v"])
    if rc:
        raise SystemExit("GX tests failed")
    saw("the weekly Great Expectations audit: validate's generic checks as a GX suite with Data Docs. It alerts but "
        "never gates a run.")


def raw_present(root):
    return ((root / "snapshots/_cache/jira/SPARK/.pull.json").exists()
            and (root / "snapshots/_cache/gitlab/.pull.json").exists())


def gcs_credentials():
    home = Path.home()
    return bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")) or any(p.exists() for p in (
        home / ".config/gcloud/application_default_credentials.json",
        Path(os.environ.get("APPDATA", home)) / "gcloud/application_default_credentials.json"))


def fetch(url, dest):
    """Download url to dest with a progress line, resuming a partial dest.part. Says size and destination first."""
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:
            have = 0   # the server ignored the range: start over
        size = int(r.headers.get("Content-Length") or 0) + have
        if "text/html" in r.headers.get("Content-Type", ""):
            raise SystemExit(f"{url} answers with a web page, not the zip (a login or confirm page): download it in a "
                             "browser and pass --zip <path>")
        print(f"  downloading {size / 1e6:.0f} MB from {url}\n  -> {dest}"
              + (f" (resuming at {have / 1e6:.0f} MB)" if have else ""))
        guard(disk=size / GB + 1, disk_at=dest.parent)
        done, shown = have, -1
        with open(part, "ab" if have else "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if size and (pct := done * 100 // size) // 5 != shown:
                    shown = pct // 5
                    print(f"  {pct:3d}%  {done / 1e6:.0f} / {size / 1e6:.0f} MB", flush=True)
    part.replace(dest)


def download():
    root = data_root()
    if raw_present(root):
        print(f"  {root / 'snapshots/_cache'}: jira/SPARK/.pull.json and gitlab/.pull.json present")
        saw("the raw Jira and GitLab pulls are already here: nothing to download.")
        return "raw pulls already present"
    sizes = {f: int(re.search(r"size: (\d+)", (REPO / f"snapshots/_cache/{f}.dvc").read_text()).group(1))
             for f in ("jira", "gitlab")}
    total = sum(sizes.values()) / GB
    print(f"  raw pulls missing under {root / 'snapshots/_cache'} "
          f"(jira {sizes['jira'] / 1e6:.0f} MB, gitlab {sizes['gitlab'] / 1e6:.0f} MB)")

    print("\n  route 1: dvc pull")
    remotes = subprocess.run([PY, "-m", "dvc", "remote", "list"], cwd=REPO, capture_output=True, text=True).stdout \
        if importlib.util.find_spec("dvc") else ""
    if root.resolve() != REPO.resolve():
        print(f"    not tried: dvc pull writes to {REPO / 'snapshots'}, and MERIDIAN_DATA points elsewhere ({root})")
    elif not remotes.strip():
        print("    not tried: dvc isn't installed or no remote is set (.dvc/config)")
    elif "gs://" in remotes and not gcs_credentials():
        print(f"    not tried: the remote is GCS ({remotes.split()[1]}), a placeholder until the team's GCP is set up, "
              "and there are no Google credentials here")
    elif low := resources_short(disk=total * 2 + 1, disk_at=root,
                                hosts=("storage.googleapis.com",) if "gs://" in remotes else ()):
        print(f"    not tried: {low}")
    else:
        print(f"    {total * 1e3:.0f} MB -> {REPO / 'snapshots/_cache'}")
        rc, _ = run([PY, "-m", "dvc", "pull", "snapshots/_cache/jira.dvc", "snapshots/_cache/gitlab.dvc"], cwd=REPO)
        if rc == 0 and raw_present(root):
            saw("dvc pull fetched the exact raw pulls dvc.lock pins (hash-checked by DVC).")
            return "dvc pull"
        print("    dvc pull didn't get them (see above)")

    print("\n  route 2: the team zip (--zip <path-or-url>)")
    known = HERE / "shared-snapshot" / ZIP_NAME   # where shared-snapshot/README.md says to put it
    if z := ARGS.zip or (str(known) if known.exists() else ""):
        if re.match(r"https?://", z):
            if "drive.google.com" in z:
                raise SystemExit("Google Drive links need a browser and a team login: download the zip by hand, then "
                                 "--zip <path>")
            dest = HERE / "shared-snapshot" / ZIP_NAME
            if not dest.exists():
                guard(hosts=(re.match(r"https?://([^/:]+)", z).group(1),))
                fetch(z, dest)
            z = dest
        z = Path(z)
        print(f"  checking sha256 of {z} ({z.stat().st_size / 1e6:.0f} MB)")
        with open(z, "rb") as f:
            got = hashlib.file_digest(f, "sha256").hexdigest()
        if got != ZIP_SHA:
            raise SystemExit(f"sha256 {got[:12]}... is not the team zip's {ZIP_SHA[:8]}...{ZIP_SHA[-6:]}: "
                             "wrong or broken file")
        print(f"  sha256 ok. Unzipping into {root / 'snapshots'}")
        with zipfile.ZipFile(z) as zf:
            guard(disk=sum(i.file_size for i in zf.infolist()) / GB + 1, disk_at=root)
            zf.extractall(root / "snapshots")
        if not raw_present(root):
            raise SystemExit("unzipped, but snapshots/_cache/jira/SPARK/.pull.json is still missing")
        saw("the team zip, checksum verified, unzipped into snapshots/: the exact raw pulls of run 70dd1dabfed544b8.")
        return "team zip, sha256 ok"
    print("    not given")

    print("\n  route 3: pull Jira and GitLab yourself from their public APIs (--public-pull)")
    if ARGS.public_pull:
        guard(ram=1, disk=5, disk_at=root, hosts=("issues.apache.org", "gitlab.com", "github.com"))
        print("    collect pulls every SPARK ticket (about 35 min, 1.8 GB raw / ~120 MB gzipped) and the GitLab issues "
              f"(~120 MB), then clones the 22 repos (~2.5 GB), all into {root / 'snapshots'}. "
              "Resumable: rerun after a stop.\n"
              "    Today's tickets differ from the team's, so this makes a run of your own: another run_id, "
              "other numbers than 70dd1dabfed544b8 in REPORT.md.")
        rc, out = run([PY, "-m", "meridian.collect", "--run-id", "new", "--config", "meridian.toml"])
        if rc:
            raise SystemExit(f"collect exited {rc}: rerun to resume")
        STATE["own_run"] = (re.findall(r"run_id=(\w+)", out) or ["?"])[-1]
        saw(f"collect pulled the public tickets and cloned the repos: your own run {STATE['own_run']}.")
        return f"public pull: run {STATE['own_run']}"
    print("    not asked for (it takes about an hour or more and ~3 GB)")
    raise Skip("no raw data. The ticket pulls hold real names and are team-only: ask the team for "
               f"{ZIP_NAME} (145 MB, sha256 {ZIP_SHA[:8]}...{ZIP_SHA[-6:]}, Drive link in shared-snapshot/README.md) "
               "and rerun with --zip <path>; or use --public-pull for a run of your own")


def collect():
    root, run_id = data_root(), settings()["pipeline"]["run_id"]
    if STATE.get("own_run"):
        raise Skip(f"the public pull already built your own run {STATE['own_run']}")
    if (root / "snapshots" / run_id / "manifest.json").exists():
        print(f"  {root / 'snapshots' / run_id} exists")
        saw("collect's snapshot of the 22 repos at their pinned SHAs is already built: nothing to do.")
        return "snapshot already built"
    if not raw_present(root):
        raise Skip("the raw ticket pulls aren't here (previous step): collect would pull fresh tickets and make "
                   "another run")
    guard(ram=1, disk=5, disk_at=root, hosts=("github.com",))
    if not shutil.which("git"):
        raise Skip("git isn't installed")
    print(f"  clones the 22 repos at the SHAs in meridian.toml (~2.5 GB) into {root / 'snapshots'}; reads the raw "
          "pulls, never contacts Jira or GitLab")
    rc, out = run([PY, "-m", "meridian.collect", "--run-id", run_id, "--config", "meridian.toml"])
    if rc or f"run_id={run_id}" not in out.strip().splitlines()[-1]:
        raise SystemExit(f"collect exit {rc}; its last line must say run_id={run_id}")
    saw(f"data acquisition: the same code SHAs + raw pulls + meridian.toml gave the same run_id={run_id} "
        "as the team's.")
    return f"run_id={run_id}"


def team_tests():
    run_id = os.environ.get("MERIDIAN_RUN_ID")
    root = data_root()
    if not run_id or not (root / "snapshots" / run_id).is_dir() or not (root / "out" / run_id).is_dir():
        raise Skip("the team run's data is private (team only): set MERIDIAN_RUN_ID=70dd1dabfed544b8 with "
                   "snapshots/ and out/ of that run present (TESTING.md)")
    guard(ram=3, disk=5)
    rc, _ = run([PY, "-m", "unittest", "tests.test_golden", "tests.test_scan", "tests.test_resolve", "-v"],
                {"MERIDIAN_RUN_ID": run_id})
    if rc:
        raise SystemExit("team-run tests failed")
    saw("the Django answer keys (542/542 aliases, 265/265 edit lines), the packages' precision/recall, and the v1 "
        "golden hashes, on the real team run.")


def task_commands():
    from dags.pipeline_spec import DAGS, command
    run_id = settings()["pipeline"]["run_id"]
    print("  the team run, without Airflow, from Data-Pipeline/ (in order; needs the data from `download`):\n")
    for t in [*DAGS["update"], "bias"]:
        print(f"    {command('update', t, run_id, 'meridian.toml', 'python')}")
    print("\n  or let DVC run whatever changed:  dvc repro")
    saw("every task is one plain command (the exact strings the DAG runs, from dags/pipeline_spec.py), so the "
        "pipeline runs, and each part can be rerun, with or without Airflow.")


def docker_ok():
    if os.environ.get("MERIDIAN_DEMO_NO_DOCKER"):
        return "MERIDIAN_DEMO_NO_DOCKER is set"
    if not shutil.which("docker"):
        return "docker isn't installed"
    try:
        if subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode:
            return "the Docker daemon isn't running (start Docker Desktop)"
    except subprocess.TimeoutExpired:
        return "docker info timed out"
    return ""


def airflow():
    cmds = ["docker compose up -d --build",
            "docker compose run --rm --no-deps -e CONNECTION_CHECK_MAX_COUNT=0 -w /opt/meridian/Data-Pipeline "
            "airflow-scheduler python -m unittest tests.test_dag -v",
            "docker compose exec airflow-scheduler airflow dags unpause meridian_update",
            "docker compose exec airflow-scheduler airflow dags trigger meridian_update --conf '{\"force\": true}'"]
    env = dict(re.findall(r"^(\w+)=(.*)$",
                          (HERE / (".env" if (HERE / ".env").exists() else ".env.example")).read_text(), re.M))
    ui = f"http://localhost:{env.get('AIRFLOW_PORT') or 8080}"
    if why := docker_ok():
        print("  run these yourself, from Data-Pipeline/ (cp .env.example .env first):\n    " + "\n    ".join(cmds))
        print(f"  then {ui} -> meridian_update -> a run -> Graph / Gantt / task logs")
        raise Skip(why)
    guard(ram=3, disk=5, disk_at=HERE)
    if not (HERE / ".env").exists():
        shutil.copy(HERE / ".env.example", HERE / ".env")
        print("  made .env from .env.example (UI login admin / change-me; change it there)")
    for cmd in (["docker", "compose", "up", "-d", "--build"], cmds[1].split()):
        rc, _ = run(cmd)
        if rc:
            raise SystemExit(f"{' '.join(cmd[:3])} exited {rc}")
    print(f"\n  Airflow UI: {ui}  (login: .env AIRFLOW_ADMIN_USER / AIRFLOW_ADMIN_PASSWORD)\n"
          "  trigger: meridian_update -> Trigger DAG w/ config -> {\"force\": true}, or:\n    "
          + "\n    ".join(cmds[2:]))
    note = "image built, DagBag loads both DAGs"
    if ARGS.airflow_run:
        ex = ["docker", "compose", "exec", "-T", "airflow-scheduler", "airflow", "dags"]
        run(ex + ["unpause", "meridian_update"])
        run(ex + ["trigger", "meridian_update", "--conf", '{"force": true}'])
        print("  waiting for the run (needs the team data in snapshots/; Ctrl+C stops waiting, not the run)")
        for _ in range(240):
            time.sleep(30)
            out = subprocess.run(ex + ["list-runs", "-d", "meridian_update", "-o", "json"], cwd=HERE,
                                 capture_output=True,
                                 text=True).stdout
            state = (json.loads(out[out.find("["):] if "[" in out else "[]") or [{}])[0].get("state")
            print(f"  {time.strftime('%H:%M:%S')}  latest run: {state}", flush=True)
            if state in ("success", "failed"):
                break
        if state != "success":
            raise SystemExit(f"meridian_update run ended {state}: see its task logs in the UI or logs/")
        note += ", meridian_update run: success"
    saw("Airflow in Docker: the image builds, Airflow's own DagBag parses both DAGs, and the UI shows each run's "
        "graph, per-task logs, retries and the Gantt chart (rubric 4, 6, 9).")
    return note


# ---------------------------------------------------------------- main

SHORT = [("Environment check (11. Reproducibility)", env_check),
         ("4. Airflow DAGs (and 9. Flow optimisation)", dags),
         ("5. Data versioning with DVC", dvc),
         ("1-2. Pipeline end to end on the built-in fixture", fixture_pipeline),
         ("11. Reproducibility: fixture bytes = frozen golden file", golden),
         ("7. Schema & statistics", validate),
         ("8. Anomaly detection & alerts (6. Logging, 12. Error handling)", anomalies),
         ("Bias via slicing: detection and mitigation", bias),
         ("Product situations from KNOWLEDGE-BASE.md", situations)]
DOWNLOAD = [("1. Data acquisition: raw ticket pulls", download),
            ("1. Data acquisition: collect builds the snapshot", collect)]
LONG = [("3. Test modules: the full unit-test suite", unit_tests),
        ("7. Schema & statistics: Great Expectations suite", gx),
        *DOWNLOAD,
        ("11. Reproducibility: team-run tests", team_tests),
        ("2. Every task, one command each", task_commands),
        ("4. Airflow in Docker", airflow)]
END = [("Where to look next", next_steps)]


def main():
    global ARGS, TMP
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["short", "long", "download"])
    ap.add_argument("--zip", help="the team zip: a local path or a direct URL")
    ap.add_argument("--public-pull", action="store_true", help="no team zip: pull Jira and GitLab from the public APIs")
    ap.add_argument("--airflow-run", action="store_true",
                    help="long: trigger meridian_update in Docker and wait for it")
    ap.add_argument("--force", action="store_true", help="run steps even below their RAM/disk guard")
    ap.add_argument("--keep", action="store_true", help="keep the temp folder")
    ARGS = ap.parse_args()
    TMP = Path(tempfile.mkdtemp(prefix="meridian-demo-"))
    start = time.monotonic()
    ram = free_ram_gb()
    print(f"Meridian data pipeline demo · {ARGS.mode} · Python {platform.python_version()} · "
          f"free RAM {f'{ram:.1f} GB' if ram is not None else 'unknown'} · "
          f"free disk {free_disk_gb(data_root()):.1f} GB · "
          f"data root {data_root()} · temp {TMP}")
    steps = {"short": SHORT + END, "long": SHORT + LONG + END, "download": DOWNLOAD}[ARGS.mode]
    STATE["n"] = len(steps)
    try:
        for title, fn in steps:
            step(title, fn)
            if fn is env_check and RESULTS[-1][1] == "FAIL":
                break   # nothing else can run without the dependencies
    finally:
        if not ARGS.keep:
            shutil.rmtree(TMP, ignore_errors=True)
    print("\n" + "=" * 100 + f"\nSUMMARY ({ARGS.mode}, {time.monotonic() - start:.0f} s)\n" + "=" * 100)
    table([[n, s, note] for n, s, note in RESULTS], ["step", "result", "note"])
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\n{len(RESULTS) - len(failed) - sum(r[1] == 'SKIP' for r in RESULTS)} passed, "
          f"{sum(r[1] == 'SKIP' for r in RESULTS)} skipped (each says why), {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
