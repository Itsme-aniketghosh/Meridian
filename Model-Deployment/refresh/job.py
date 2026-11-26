"""S4, the refresh job (Model-Deployment/PLAN.md §3, packets/utkarsh-refresh). Cloud Run job `meridian-refresh`.

    python Model-Deployment/refresh/job.py --data-root ~/refresh-data --store local:~/refresh-store   # a laptop
    python Model-Deployment/refresh/job.py                    # in the image: data root /work, GCS from deploy.toml
    python Model-Deployment/refresh/job.py --dry-run          # say what it would run, run nothing

One run, daily (Cloud Scheduler, deploy.toml [schedule]):

1. pull     The last block from the store into <data-root>/published: latest.json, then the file it names. Then
            the caches (S10): snapshots/_cache from the cache bucket (job-cache/snapshots-cache.tar.gz), so a run on
            Cloud Run's empty disk fetches only new commits and tickets and blames only new fixes. None yet: a
            cold run, logged.
2. new?     meridian.status on the Spark-only config. Exit 99 = nothing new: the run ends there, exit 0.
3. update   The data pipeline for Spark only, following master: collect, then history, scan, resolve, owners, join,
            graph, validate, publish. The same parts and commands as the meridian_update DAG, so a cloud block is
            made exactly like a laptop's. The other datasets (Django and its packages, GitLab) aren't collected:
            the model reads Spark only ([data] repo in model.toml).
4. history_pkl
            S2's serve.precompute (Rachna) on the new block: history.pkl, the replayed history /score reads to score a
            push in milliseconds. Only this job has the block, so it's made here; deploy.yml pulls history.pkl
            from deploy.toml [serve_history] into the serve image's build context. Skipped, and logged, while
            serve/precompute.py isn't in the tree.
5. table    model.data on the new block with model.toml's split as it is. Only [data] run_id moves, so every
            commit past follow_up_end comes out as split "none": the new commits drift is measured on.
6. drift    S5's monitor.drift (Vishwa) on that table, wrapped as the D2 report.
7. push     The block file, then history.pkl (as history/<run_id>.pkl and deploy.toml [serve_history]'s object, in
            its bucket), then latest.json, so a reader never sees a pointer to a missing file; then the D2 report.
            GCS buckets.block, the [serve_history] bucket and buckets.monitor.
   fallback S10's fallback-rate slice report (monitor.fallback, Vishwa): the writer on the new block with no LLM (the
            committed cache answers), the gate's fallback rate per repo, team and task, and Slack when a slice is
            flagged. To buckets.monitor as fallback/<run_id>.json. A failed report warns; the run still succeeds.
   cache    After the push, snapshots/_cache back to the cache bucket for the next run (S10). A failed save warns
            and the run still succeeds: the block is already up, and the next run is only slower.
8. retrain  When D2 says retrain, deploy.toml [retrain] decides. Off (the default): "due, switch off" is logged and
            nothing else happens. On: model.refresh --refit on the new block, in a writable copy of Model-Pipeline
            under /work, stopped after [retrain] until ("gate": nothing is registered). A refit that doesn't pass
            warns; the run still succeeds, the block is already up. Past the registry, a promotion sends D3
            (repository_dispatch "champion-updated") when [retrain] dispatch is on. notify() sends S7's Slack
            messages: drift found (unless [retrain] slack_drift is off) and retrain triggered, with D2's why.

Every step writes one D5 log line (event "refresh", or "drift"), JSON on stdout, which Cloud Logging parses. The
parts' own logs go to stderr. No line carries an email, a name or a person id: only counts, ids and timings.
A failed step alerts (meridian.alert: Slack when MERIDIAN_SLACK_WEBHOOK is set) and the job exits 1.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEPLOY = HERE.parent                      # Model-Deployment/
ROOT = DEPLOY.parent
DATA_PIPELINE = ROOT / "Data-Pipeline"
MODEL_PIPELINE = ROOT / "Model-Pipeline"
MERIDIAN_TOML = DATA_PIPELINE / "meridian.toml"
SETTINGS_TOML = DATA_PIPELINE / "settings.toml"
MODEL_TOML = MODEL_PIPELINE / "model.toml"
CHAMPION = MODEL_PIPELINE / "registry" / "champion.json"
PRECOMPUTE = DEPLOY / "serve" / "precompute.py"   # S2's (Rachna): the history state the endpoint scores pushes from
sys.path.insert(0, str(DEPLOY))
import deploy_config  # noqa: E402

DATASET = "spark"         # model.toml [data] repo = "apache/spark"
BRANCH = "master"         # apache/spark's default branch: collect follows a ref that names a branch
RETRAIN_OFF = {"enabled": False, "until": "gate", "dispatch": False, "slack_drift": False}
D3_EVENT = "champion-updated"   # packets/README.md D3, received by deploy.yml
CACHE_OBJECT = "job-cache/snapshots-cache.tar.gz"   # buckets.cache, beside history/ (deploy reads only history/)
PARTS = ("history", "scan", "resolve", "owners", "join", "graph", "validate")   # the DAG's CORE, in its order
NOTHING_NEW = 99          # meridian.status and model.refresh: nothing new since the last block
GATE_REFUSED = 3          # model.gate.REFUSED, passed on by model.refresh: a worse model, not an error
FMT = "%Y-%m-%dT%H:%M:%SZ"


# ───────────── the Spark-only config ─────────────

def spark_only(text, branch=BRANCH):
    """meridian.toml's text with only [datasets.spark] and [teams.spark] kept, Spark following `branch` instead of
    its pinned SHA, and its snapshot_at dropped: collect then takes the pull time as the snapshot. Every other
    section is kept as it is, so the parts compute what they compute on a laptop."""
    out, keep, section = [], True, None
    for line in text.splitlines(keepends=True):
        head = re.match(r"\[([^\]]+)\]\s*(#.*)?$", line)
        if head:
            section = head.group(1).strip()
            kind, _, name = section.partition(".")
            keep = kind not in ("datasets", "teams") or name == DATASET
        if not keep:
            continue
        if section == f"datasets.{DATASET}":
            if re.match(r"\s*snapshot_at\s*=", line):
                continue
            line = re.sub(r'(\bref\s*=\s*)"[^"]*"', rf'\g<1>"{branch}"', line)
        out.append(line)
    new = "".join(out)
    cfg = tomllib.loads(new)
    repos = cfg["datasets"][DATASET]["repos"]
    if set(cfg["datasets"]) != {DATASET} or set(cfg.get("teams", {})) - {DATASET} or \
            [r.get("ref") for r in repos] != [branch] or "snapshot_at" in cfg["datasets"][DATASET]:
        raise SystemExit("spark_only: meridian.toml's layout changed, the Spark-only config came out wrong")
    return new


# What a run with no tracked old names (Spark has no [datasets.spark] symbols) can't fill: the migration side's
# tables and their coverage. validate's floors for them (settings.toml [validate.rows], [validate.coverage]) were
# measured on the Django runs, so the Spark-only settings drop just those. Every Spark, ticket and SZZ check stays.
MIGRATION_TABLES = ("candidate", "resolution", "call_site", "call_edge", "module_import", "blast_radius")
MIGRATION_COVERAGE = ("call_sites_resolved", "call_sites_owned")


# SZZ processes in the job: one per CPU of [jobs.refresh] (2). settings.toml's 4 peaked at 5.15 GiB in the image
# (run 6, Nov 24), over the job's 4 GiB, with 4 processes on 2 CPUs
SZZ_WORKERS = 2


def spark_settings(text, szz_workers=SZZ_WORKERS):
    """settings.toml's text without the row floors of MIGRATION_TABLES and the floors of MIGRATION_COVERAGE, and
    with [history] szz_workers set to the job's CPUs."""
    drop = {"validate.rows": MIGRATION_TABLES, "validate.coverage": MIGRATION_COVERAGE}
    out, section, set_workers = [], None, False
    for line in text.splitlines(keepends=True):
        head = re.match(r"\[([^\]]+)\]\s*(#.*)?$", line)
        if head:
            section = head.group(1).strip()
        key = re.match(r"\s*([A-Za-z_]+)\s*=", line)
        if key and key.group(1) in drop.get(section, ()):
            continue
        if key and section == "history" and key.group(1) == "szz_workers":
            line, set_workers = f"szz_workers = {szz_workers}   # the refresh job: one per CPU (job.py)\n", True
        out.append(line)
    new = "".join(out)
    if not set_workers or tomllib.loads(new)["history"]["szz_workers"] != szz_workers:
        raise SystemExit("spark_settings: settings.toml has no [history] szz_workers to set")
    old, cfg = tomllib.loads(text)["validate"], tomllib.loads(new)["validate"]
    if set(old["rows"]) - set(cfg["rows"]) != set(MIGRATION_TABLES) or \
            set(old["coverage"]) - set(cfg["coverage"]) != set(MIGRATION_COVERAGE):
        raise SystemExit("spark_settings: settings.toml's [validate] layout changed, the Spark-only settings came "
                         "out wrong")
    return new


def model_toml_for(text, run_id):
    """model.toml's text with [data] run_id set to the new block, nothing else changed (split, follow_up_end)."""
    sys.path.insert(0, str(MODEL_PIPELINE))
    from model.refresh import set_keys   # the same comment-keeping setter model.refresh rolls model.toml with
    return set_keys(text, "data", {"run_id": json.dumps(run_id)})


# ───────────── where the block and the reports live ─────────────

class LocalStore:
    """A folder standing in for the buckets: <root>/<bucket key>/<name>."""

    def __init__(self, root):
        self.root = Path(root).expanduser()

    def get(self, bucket, name, dest):
        src = self.root / bucket / name
        if not src.exists():
            return False
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(src.read_bytes())
        return True

    def put(self, src, bucket, name):
        dst = self.root / bucket / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(Path(src).read_bytes())
        tmp.replace(dst)

    def where(self, bucket, name):
        return str(self.root / bucket / name)


class GcsStore:
    """deploy.toml's buckets through the Cloud Storage JSON API. The token comes from the metadata server in Cloud
    Run (the job's own service account, meridian-refresh), else from `gcloud auth print-access-token`. No client
    library: the image stays the pipeline's pins.

    The token is fetched for every call, never kept: a token lives about an hour, and the uploads come at the end of
    a run that may take longer. The metadata server keeps and renews the token itself, so asking it is cheap."""

    API = "https://storage.googleapis.com"

    def __init__(self, buckets):
        self.buckets = buckets

    def token(self):
        try:
            req = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r)["access_token"]
        except OSError:
            return subprocess.run(["gcloud", "auth", "print-access-token"], check=True,
                                  capture_output=True, text=True).stdout.strip()

    def _call(self, url, data=None, method="GET", ctype=None, size=None):
        headers = {"Authorization": f"Bearer {self.token()}"}
        if ctype:
            headers["Content-Type"] = ctype
        if size is not None:   # a file object as the body: http.client sends it in blocks, never all in memory
            headers["Content-Length"] = str(size)
        return urllib.request.urlopen(urllib.request.Request(url, data, headers, method=method), timeout=600)

    def get(self, bucket, name, dest):
        b = self.buckets[bucket]
        url = f"{self.API}/storage/v1/b/{b}/o/{urllib.parse.quote(name, safe='')}?alt=media"
        try:
            r = self._call(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        with r, open(dest, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        return True

    def put(self, src, bucket, name):
        b = self.buckets[bucket]
        url = f"{self.API}/upload/storage/v1/b/{b}/o?uploadType=media&name={urllib.parse.quote(name, safe='')}"
        ctype = "application/json" if name.endswith(".json") else "application/octet-stream"
        with open(src, "rb") as f:   # streamed: the 361 MB block never sits in memory
            self._call(url, f, "POST", ctype, size=os.fstat(f.fileno()).st_size).close()

    def where(self, bucket, name):
        return f"gs://{self.buckets[bucket]}/{name}"


def make_store(spec, cfg):
    """--store: 'gcs' (deploy.toml's buckets) or 'local:<folder>'."""
    if spec == "gcs":
        return GcsStore(cfg["buckets"])
    if spec.startswith("local:"):
        return LocalStore(spec.split(":", 1)[1])
    raise SystemExit(f"--store {spec!r}: 'gcs' or 'local:<folder>'")


# ───────────── running it ─────────────

def model_version(path=CHAMPION):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["version"]
    except (OSError, KeyError, ValueError):
        return "unknown"


class Job:
    def __init__(self, data_root, store, python=sys.executable, dry_run=False, out=sys.stdout, serve_history=None,
                 retrain=None, github=None):
        self.root, self.store, self.python, self.dry_run, self.out = Path(data_root), store, python, dry_run, out
        # where deploy.yml reads history.pkl: deploy.toml [serve_history], a [buckets] key and an object under history/
        h = serve_history or {"bucket": "cache", "object": "history/latest.pkl"}
        self.history_bucket, self.history_object = h["bucket"], h["object"]
        self.retrain_cfg = {**RETRAIN_OFF, **(retrain or {})}   # deploy.toml [retrain]; missing: off
        self.github = github                                     # deploy.toml [github] repository, for D3
        self.work = self.root / "refresh"
        self.env = {**os.environ, "MERIDIAN_DATA": str(self.root), "PYTHONUNBUFFERED": "1"}
        self.timings = {}
        self.history_path = None   # history.pkl for the new block, once made

    def event(self, step, ms, event="refresh", severity="INFO", **fields):
        """One D5 log line."""
        # read per line, not once at start: a promotion during the run (the retrain step) changes champion.json
        line = {"severity": severity, "event": event, "step": step, "model_version": model_version(),
                "latency_ms": ms, **fields}
        print(json.dumps(line, sort_keys=True), file=self.out, flush=True)

    def sh(self, args, cwd, ok=(0,)):
        """Run one command, its output streamed to stderr. Returns (exit code, its output)."""
        print("+ " + " ".join(map(str, args)), file=sys.stderr, flush=True)
        if self.dry_run:
            return ok[0], ""
        p = subprocess.Popen(list(map(str, args)), cwd=cwd, env=self.env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        lines = []
        for line in p.stdout:
            sys.stderr.write(line)
            lines.append(line)
        code = p.wait()
        if code not in ok:
            raise StepFailed(f"exit {code}: {' '.join(map(str, args))}", "".join(lines[-20:]))
        return code, "".join(lines)

    def part(self, name, run_id, config, *extra, ok=(0,)):
        return self.sh([self.python, "-m", f"meridian.{name}", "--run-id", run_id, "--config", config, *extra],
                       DATA_PIPELINE, ok)

    def timed(self, step, fn, **fields):
        t = time.monotonic()
        result = fn()
        ms = int((time.monotonic() - t) * 1000)
        self.timings[step] = ms
        self.event(step, ms, **fields, **(result if isinstance(result, dict) else {}))
        return result

    # the steps

    def pull(self):
        published = self.root / "published"
        if not self.store.get("block", "latest.json", published / "latest.json"):
            return {"last_run": None}
        latest = json.loads((published / "latest.json").read_text(encoding="utf-8"))
        if not (published / latest["file"]).exists() and \
                not self.store.get("block", latest["file"], published / latest["file"]):
            raise StepFailed(f"latest.json names {latest['file']}, which the store doesn't have", "")
        return {"last_run": latest["run_id"]}

    def restore_cache(self):
        """snapshots/_cache from the cache bucket. Unpacked beside the folder, then swapped in, so a broken archive
        leaves the disk as it was. Only plain files and folders under _cache are taken (tarfile's "data" filter)."""
        cache = self.root / "snapshots" / "_cache"
        if self.dry_run:
            return {"cache": "dry run"}
        archive = self.root / "snapshots" / "_cache.tar.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not self.store.get("cache", CACHE_OBJECT, archive):
                return {"cache": "cold: none in the store yet"}
            mb = round(archive.stat().st_size / 1e6, 1)
            tmp = self.root / "snapshots" / "_cache.restoring"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            with tarfile.open(archive, "r:gz") as t:
                t.extractall(tmp, filter="data")
            if not (tmp / "_cache").is_dir():
                raise tarfile.TarError("the archive holds no _cache folder")
            shutil.rmtree(cache, ignore_errors=True)
            (tmp / "_cache").rename(cache)
            shutil.rmtree(tmp, ignore_errors=True)
            return {"cache": "restored", "cache_mb": mb}
        except (OSError, tarfile.TarError, urllib.error.URLError) as e:   # never fatal: a cold run instead
            return {"cache": f"cold: restore failed ({scrub(str(e))[:200]})"}
        finally:
            archive.unlink(missing_ok=True)

    def save_cache(self):
        """snapshots/_cache to the cache bucket, as one gzip'd tar written to disk, then streamed up."""
        cache = self.root / "snapshots" / "_cache"
        if self.dry_run or not cache.is_dir():
            return {"cache": "dry run" if self.dry_run else "nothing to save"}
        archive = self.root / "snapshots" / "_cache.tar.gz"
        try:
            with tarfile.open(archive, "w:gz", compresslevel=1) as t:   # git packs don't shrink: level 1 is enough
                t.add(cache, arcname="_cache")
            mb = round(archive.stat().st_size / 1e6, 1)
            self.store.put(archive, "cache", CACHE_OBJECT)
            return {"cache": "saved", "cache_mb": mb, "where": self.store.where("cache", CACHE_OBJECT)}
        except (OSError, tarfile.TarError, urllib.error.URLError) as e:
            return {"cache": f"not saved ({scrub(str(e))[:200]})", "severity": "WARNING"}
        finally:
            archive.unlink(missing_ok=True)

    def config(self):
        self.work.mkdir(parents=True, exist_ok=True)
        path = self.work / "meridian.spark.toml"
        path.write_text(spark_only(MERIDIAN_TOML.read_text(encoding="utf-8")), encoding="utf-8")
        settings = self.work / "settings.spark.toml"
        settings.write_text(spark_settings(SETTINGS_TOML.read_text(encoding="utf-8")), encoding="utf-8")
        self.env["MERIDIAN_SETTINGS"] = str(settings)   # every part reads settings.toml through it (common.py)
        return path

    def anything_new(self, config):
        code, _ = self.sh([self.python, "-m", "meridian.status", "--config", config], DATA_PIPELINE,
                          ok=(0, NOTHING_NEW))
        return {"new": code == 0}

    def update(self, config):
        _, out = self.sh([self.python, "-m", "meridian.collect", "--run-id", "new", "--config", config],
                         DATA_PIPELINE)
        found = re.findall(r"run_id=([0-9a-f]{16})", out)
        run_id = found[-1] if found else ("0" * 16 if self.dry_run else None)
        if not run_id:
            raise StepFailed("collect printed no run_id=", out[-2000:])
        for name in PARTS:
            self.timed(name, lambda name=name: self.part(name, run_id, config), run_id=run_id)
        self.part("publish", run_id, config)
        return run_id

    def history_pkl(self, run_id):
        """history.pkl for the new block (serve.precompute), or a skip while S2's precompute isn't in the tree."""
        if not PRECOMPUTE.exists():
            return {"history": "skipped: Model-Deployment/serve/precompute.py isn't here (S2)"}
        out = self.work / "history.pkl"
        block = self.root / "published" / f"{run_id}.sqlite"   # publish's name for the block
        self.sh([self.python, "-m", "serve.precompute", "--block", block, "--run-id", run_id, "--out", out], DEPLOY)
        if self.dry_run:
            return {"history": "dry run"}
        self.history_path = out
        return {"history_md5": hashlib.md5(out.read_bytes()).hexdigest(),
                "history_mb": round(out.stat().st_size / 1e6, 1)}

    def table(self, run_id):
        cfg_path = self.work / "model.toml"
        cfg_path.write_text(model_toml_for(MODEL_TOML.read_text(encoding="utf-8"), run_id), encoding="utf-8")
        out = self.work / "table"
        self.sh([self.python, "-m", "model.data", "--config", cfg_path, "--published", self.root / "published",
                 "--out", out], MODEL_PIPELINE)
        return out / "features.parquet"

    def drift(self, run_id, table):
        raw = self.work / "drift.json"
        if not (DEPLOY / "monitor" / "drift.py").exists():
            raise StepFailed("Model-Deployment/monitor/drift.py isn't here: S5 (vishwa-monitor) isn't merged yet", "")
        self.sh([self.python, "-m", "monitor.drift", "--table", table, "--out", raw], DEPLOY)
        report = d2(run_id, now(), json.loads(raw.read_text(encoding="utf-8")) if not self.dry_run else None)
        path = self.work / "report.json"
        path.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return report, path

    def retrain_workspace(self):
        """A writable Model-Pipeline for model.refresh, which rolls model.toml and writes data/ and reports/ in place:
        /app is read-only for the job's user. The code, model.toml, the champion and the committed best params are
        copied; Data-Pipeline is linked, read only."""
        ws = self.work / "retrain"
        shutil.rmtree(ws, ignore_errors=True)
        mp = ws / "Model-Pipeline"
        shutil.copytree(MODEL_PIPELINE / "model", mp / "model", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(MODEL_TOML, mp / "model.toml")
        (mp / "registry").mkdir()
        for f in (CHAMPION, CHAMPION.parent / "model.joblib"):
            if f.exists():
                shutil.copy2(f, mp / "registry" / f.name)
        (mp / "reports").mkdir()
        for f in (MODEL_PIPELINE / "reports").glob("tuning_*.json"):
            shutil.copy2(f, mp / "reports" / f.name)
        (mp / "data").mkdir()
        (ws / "Data-Pipeline").symlink_to(DATA_PIPELINE, target_is_directory=True)
        return mp

    def retrain(self, report, run_id):
        """D2's retrain flag, acted on as deploy.toml [retrain] says."""
        if not report.get("retrain"):
            return {"retrain": False, "outcome": "not needed"}
        cfg = self.retrain_cfg
        if cfg.get("slack_drift", False):   # off: S6's log-based alert is the team's one drift message
            notify("drift", report, run_id)
        if not cfg["enabled"]:
            return {"retrain": True, "outcome": "due, switch off ([retrain] enabled = false)"}
        if self.dry_run:
            return {"retrain": True, "outcome": "dry run"}
        mp = self.retrain_workspace()
        before = json.loads((mp / "registry" / "champion.json").read_text(encoding="utf-8"))["version"]
        args = [self.python, "-m", "model.refresh", "--refit", "--config", mp / "model.toml",
                "--published", self.root / "published"]
        if cfg.get("until"):
            args += ["--until", cfg["until"]]
        # Only the gate's refusal is "not better" (a warning) and 99 "nothing new". Any other exit (a crash, an
        # out-of-memory kill, a data check, a missing file) raises StepFailed: the run fails and alerts
        code, _ = self.sh(args, mp, ok=(0, GATE_REFUSED, NOTHING_NEW))
        if code == GATE_REFUSED:
            notify("retrain", report, run_id, outcome="not better, kept")
            return {"retrain": True, "outcome": "not better, kept: the gate refused the refit, the champion stays",
                    "severity": "WARNING"}
        if code == NOTHING_NEW:
            return {"retrain": True, "outcome": "nothing new: model.refresh saw no newer block than model.toml's"}
        out = {"retrain": True}
        if cfg.get("until") and cfg["until"] != "registry":
            out["outcome"] = f"refit passed {cfg['until']}; stopped there, nothing registered"
            notify("retrain", report, run_id, outcome=out["outcome"])
            return out
        champion = json.loads((mp / "registry" / "champion.json").read_text(encoding="utf-8"))
        if champion["version"] == before:
            out["outcome"] = "not better, kept"
        else:
            out.update(outcome="promoted", version=champion["version"], md5=champion["model_md5"])
            if cfg.get("dispatch"):
                out["dispatch"] = dispatch(self.github, champion, report.get("drift", {}).get("why", ""),
                                           self.store.where("monitor", f"reports/{run_id}.json"))
        notify("retrain", report, run_id, outcome=out["outcome"])
        return out

    def fallback(self, run_id):
        """S10's fallback-rate slice report (monitor.fallback) on the new block, LLM off; Slack when a slice is
        flagged. Like the cache's save, a report that fails warns and the run still succeeds."""
        out = self.work / "fallback.json"
        block = self.root / "published" / f"{run_id}.sqlite"
        try:
            self.sh([self.python, "-m", "monitor.fallback", "--db", block, "--run-id", run_id, "--out", out], DEPLOY)
            if self.dry_run:
                return {"fallback": "dry run"}
            rep = json.loads(out.read_text(encoding="utf-8"))
            name = f"fallback/{run_id}.json"
            self.store.put(out, "monitor", name)
            return {"texts": rep.get("texts", 0), "open_flags": len(rep.get("open_flags", [])),
                    "posted": ",".join(rep.get("posted", [])) or "none", "where": self.store.where("monitor", name),
                    **({"fallback": f"not measured ({rep['error'][:200]})", "severity": "WARNING"}
                       if "error" in rep else {})}
        except (StepFailed, OSError, ValueError, urllib.error.URLError) as e:
            return {"fallback": f"not run ({scrub(str(e))[:200]})", "severity": "WARNING"}

    def push(self, run_id, report_path):
        if self.dry_run:
            return {}
        published = self.root / "published"
        latest = json.loads((published / "latest.json").read_text(encoding="utf-8"))
        if latest["run_id"] != run_id:
            raise StepFailed(f"published/latest.json is {latest['run_id']}, not the new run {run_id}", "")
        self.store.put(published / latest["file"], "block", latest["file"])
        out = {"block": self.store.where("block", latest["file"])}
        if self.history_path:   # this block's, then the name deploy.yml reads ([serve_history]), side by side
            folder = self.history_object.rsplit("/", 1)[0]
            self.store.put(self.history_path, self.history_bucket, f"{folder}/{run_id}.pkl")
            self.store.put(self.history_path, self.history_bucket, self.history_object)
            out["history"] = self.store.where(self.history_bucket, self.history_object)
        self.store.put(published / "latest.json", "block", "latest.json")
        name = f"reports/{run_id}.json"
        self.store.put(report_path, "monitor", name)
        return {**out, "report": self.store.where("monitor", name)}

    def run(self):
        started = time.monotonic()
        step = "pull"
        try:
            self.timed("pull", self.pull)
            step = "restore_cache"
            self.timed("restore_cache", self.restore_cache)
            step = "config"
            config = self.config()
            step = "anything_new"
            if not self.timed("anything_new", lambda: self.anything_new(config))["new"]:
                self.event("done", int((time.monotonic() - started) * 1000), outcome="nothing new")
                return 0
            step = "update"
            run_id = self.update(config)
            self.event("update", sum(self.timings.get(p, 0) for p in PARTS), run_id=run_id)
            step = "history_pkl"
            self.timed("history_pkl", lambda: self.history_pkl(run_id), run_id=run_id)
            step = "table"
            table = self.timed("table", lambda: self.table(run_id), run_id=run_id)
            step = "drift"
            report, path = self.drift(run_id, table)
            self.event("drift", 0, event="drift", run_id=run_id, retrain=report["retrain"],
                       n_new=report["n_new"], why=report["drift"]["why"])
            step = "push"
            self.timed("push", lambda: self.push(run_id, path), run_id=run_id)
            step = "fallback"
            self.timed("fallback", lambda: self.fallback(run_id), run_id=run_id)
            step = "save_cache"
            self.timed("save_cache", self.save_cache, run_id=run_id)
            step = "retrain"
            self.timed("retrain", lambda: self.retrain(report, run_id), run_id=run_id)
            self.event("done", int((time.monotonic() - started) * 1000), run_id=run_id, outcome="ok")
            return 0
        except (StepFailed, subprocess.CalledProcessError, OSError, SystemExit) as e:
            error = scrub(str(e))
            self.event(step, int((time.monotonic() - started) * 1000), severity="ERROR", outcome="failed",
                       error=error[:500])
            tail = getattr(e, "tail", "") or ""
            alert(f"refresh job failed at {step}: {error[:300]}", [scrub(x)[:300] for x in tail.splitlines()[-10:]])
            return 1


class StepFailed(Exception):
    def __init__(self, message, tail):
        super().__init__(message)
        self.tail = tail


def d2(run_id, at, drift):
    """S5's drift report as contract D2 (packets/README.md). Decay (S5's matured-window check) isn't in it yet,
    so retrain is the drift flag alone. When S5 writes D2 itself, this goes."""
    if drift is None:   # dry run
        return {"run_id": run_id, "at": at, "n_new": 0, "drift": {"psi": {}, "evidently_share": 0.0, "flag": False,
                "why": "dry run"}, "decay": None, "retrain": False}
    return {"run_id": run_id, "at": at, "n_new": drift["n_current"],
            "drift": {"psi": {c: f["psi"] for c, f in drift["features"].items()},
                      "evidently_share": drift["drifted_share"], "flag": drift["retrain"],
                      "why": "; ".join(drift["reasons"]) or "no threshold crossed", "window": drift.get("window")},
            "decay": None, "retrain": bool(drift["retrain"])}


def notify(kind, report, run_id, outcome=None):
    """S7's Slack messages, with D2's why: "drift" (found, retrain due) and "retrain" (triggered, and what came of
    it: outcome). Through meridian.alert as a warning; scrubbed like every alert; never raises, so Slack can't change
    the run. Returns the channels that took it."""
    why = (report.get("drift") or {}).get("why") or "no reason in the report"
    if kind == "drift":
        subject = "Meridian: drift found, retrain due"
        lines = [f"block {run_id}: {why}"]
        if report.get("n_new") is not None:
            lines.append(f"{report['n_new']} new commits measured")
    elif kind == "retrain":
        subject = f"Meridian: retrain triggered: {outcome or 'see the job log'}"
        lines = [f"why (D2): {why}", f"block {run_id}"]
    else:
        return []
    return send(scrub(subject)[:200], [scrub(x)[:300] for x in lines], event="warn")


def dispatch(repository, champion, reason, report_where, token=None, opener=None):
    """D3: repository_dispatch "champion-updated" with {version, md5, reason, report}, which deploy.yml receives.
    The token is MERIDIAN_GITHUB_DISPATCH (a fine-grained token, this repo only). Never raises: the outcome says."""
    token = token if token is not None else os.environ.get("MERIDIAN_GITHUB_DISPATCH", "")
    if not repository or not token:
        return "skipped: no repository or no MERIDIAN_GITHUB_DISPATCH"
    body = json.dumps({"event_type": D3_EVENT, "client_payload": {
        "version": champion["version"], "md5": champion["model_md5"], "reason": reason[:300],
        "report": report_where}}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{repository}/dispatches", body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=30) as r:
            return f"sent ({r.status})"
    except (OSError, ValueError) as e:
        return f"failed: {scrub(str(e))[:200]}"


# What a step's output may hold that must never reach Slack or the logs: an email (git's author lines), a token, a
# password in a URL, a signed URL's signature. Each is replaced by a label; the rest of the line stays, for debugging
SCRUB = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"(?i)\b(bearer\s+)[^\s\"',]+"), r"\1<redacted>"),
    (re.compile(r"(?i)\b(token|access_token|password|passwd|secret|api_key|key)([\"']?\s*[:=]\s*[\"']?)[^\s\"',&]+"),
     r"\1\2<redacted>"),
    (re.compile(r"\bya29\.[A-Za-z0-9_.-]+"), "<token>"),                           # a Google access token
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<token>"),                      # a GitHub token
    (re.compile(r"https://hooks\.slack\.com/\S+"), "<slack webhook>"),
    (re.compile(r"(?i)(https?://)[^\s/:@]+:[^\s/@]+@"), r"\1<user>:<redacted>@"),   # user:password@host
    (re.compile(r"(?i)([?&](x-goog-signature|signature|sig|token)=)[^&\s]+"), r"\1<redacted>"),
]


def scrub(text):
    """text with emails, tokens and credentials replaced. Never raises: a broken guard mustn't hide the failure."""
    try:
        for pattern, label in SCRUB:
            text = pattern.sub(label, text)
        return text
    except Exception:   # noqa: BLE001
        return "<output withheld: the guard failed>"


def alert(subject, lines):
    send(subject, lines, event="failure")


def send(subject, lines, event):
    """meridian.alert.send: the channels that took it. Never raises: an alert must never hide what it reports."""
    sys.path.insert(0, str(DATA_PIPELINE))
    try:
        from meridian import alert as meridian_alert
        return meridian_alert.send(subject, lines, event=event)
    except Exception as e:   # noqa: BLE001
        print(f"alert failed: {e}", file=sys.stderr)
        return []


def now():
    return datetime.now(timezone.utc).strftime(FMT)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="refresh/job.py", description=__doc__.split("\n")[0])
    ap.add_argument("--data-root", default=os.environ.get("MERIDIAN_DATA", "/work"),
                    help="the pipeline's data root: snapshots/, out/, published/ (default $MERIDIAN_DATA, else /work)")
    ap.add_argument("--store", default="gcs", help="'gcs' (deploy.toml's buckets) or 'local:<folder>'")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, run none, write nothing to the store")
    a = ap.parse_args(argv)
    cfg = deploy_config.load()
    return Job(Path(a.data_root).expanduser(), make_store(a.store, cfg), dry_run=a.dry_run,
               serve_history=cfg["serve_history"], retrain=cfg.get("retrain"),
               github=cfg.get("github", {}).get("repository")).run()


if __name__ == "__main__":
    sys.exit(main())
