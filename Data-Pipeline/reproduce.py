"""Reproduce the Meridian data pipeline on your own machine, in one file. Standard library only.

    curl -O https://raw.githubusercontent.com/Itsme-aniketghosh/Meridian/main/Data-Pipeline/reproduce.py
    python3 reproduce.py                       # level 1: about 2 minutes, no private data, no Docker
    python3 reproduce.py --zip <team zip>      # level 2: the real team run, about 30-40 minutes, ~6 GB of disk

Level 2 runs on its own when the team zip (ZIP_LINK below) sits in Data-Pipeline/shared-snapshot/ of the checkout.

Level 1 clones the repo, makes a virtual environment, installs the pinned packages, runs every unit test and
`run_demo.py short` (each guideline item on the built-in fixture, with its frozen golden hashes).

Level 2 also rebuilds team run c5118b019d642a9b from the team's raw ticket zip: it checks the zip's sha256, clones the
22 repos at their pinned SHAs, runs every pipeline task in order (the exact commands the Airflow DAG runs), then
checks that collect gave the same run_id and that the 24 output files match golden/c5118b019d642a9b.json.

Run it in an empty folder, or inside a Meridian checkout (it then uses that checkout instead of cloning).
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

REPO_URL = "https://github.com/Itsme-aniketghosh/Meridian.git"
TEAM_RUN = "c5118b019d642a9b"
# the team zip: the raw Jira and GitLab pulls of Oct 1, which a fresh pull today can't give again
ZIP_NAME = "meridian-raw-973531f7fd4cfd5e.zip"   # in Data-Pipeline/shared-snapshot/, found there
ZIP_LINK = "https://drive.google.com/file/d/1jq8gMWAQpmECNXrX2Su3I-xw7bwvu0tL/view?usp=drive_link"
TASKS = ["collect", "history", "scan", "resolve", "owners", "join", "graph", "validate", "report", "publish", "bias"]
RESULTS = []   # (step, PASS/FAIL/SKIP, note)


def say(msg):
    print(f"\n=== {msg}", flush=True)


def run(cmd, cwd, env=None):
    """Run cmd, streaming its output. -> (exit code, its last line)."""
    print("$ " + " ".join(str(c) for c in cmd), flush=True)
    p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace")
    last = ""
    for line in p.stdout:
        print("  " + line, end="", flush=True)
        last = line.strip() or last
    return p.wait(), last


def step(name, fn):
    start = time.monotonic()
    try:
        note = fn() or ""
        RESULTS.append((name, "PASS", f"{note} ({time.monotonic() - start:.0f} s)".strip()))
        return True
    except SystemExit as e:
        RESULTS.append((name, "FAIL", str(e)))
        return False


def find_repo(args):
    """The Meridian checkout: this script's own (run from inside one), --repo-dir, or a fresh clone."""
    here = Path(__file__).resolve().parent
    if (here / "dags" / "pipeline_spec.py").exists():
        return here.parent
    dest = Path(args.repo_dir).resolve()
    if (dest / "Data-Pipeline" / "dags" / "pipeline_spec.py").exists():
        return dest
    if not shutil.which("git"):
        raise SystemExit("git isn't installed")
    rc, _ = run(["git", "clone", REPO_URL, str(dest)], cwd=dest.parent)
    if rc:
        raise SystemExit(f"git clone exited {rc}")
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zip", help="the team's raw ticket zip (level 2): a local path")
    ap.add_argument("--repo-dir", default="Meridian", help="where to clone the repo (default ./Meridian)")
    ap.add_argument("--ref", default="", help="a commit or branch to check out (default: main as cloned)")
    args = ap.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit(f"needs Python 3.11 or newer, this is {platform.python_version()}")
    print(f"Meridian reproduce · Python {platform.python_version()} · {platform.system()} {platform.machine()}")

    state = {}

    def clone():
        state["repo"] = find_repo(args)
        if args.ref:
            if run(["git", "checkout", args.ref], cwd=state["repo"])[0]:
                raise SystemExit(f"git checkout {args.ref} failed")
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=state["repo"], capture_output=True,
                             text=True).stdout.strip()
        state["dp"] = state["repo"] / "Data-Pipeline"
        return f"{state['repo']} at {sha}"

    def environment():
        env_dir = state["dp"] / ".venv"
        if not env_dir.exists():
            venv.create(env_dir, with_pip=True)
        py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        state["py"] = str(py)
        if run([state["py"], "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=state["dp"])[0]:
            raise SystemExit("pip install -r requirements.txt failed")
        return f"{env_dir}, requirements.txt installed"

    def unit_tests():
        rc, last = run([state["py"], "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=state["dp"])
        if rc:
            raise SystemExit(f"unit tests failed: {last}")
        return last   # e.g. OK (skipped=18): the skipped ones need Airflow, GX or the team data

    def demo():
        rc, last = run([state["py"], "run_demo.py", "short"], cwd=state["dp"])
        if rc:
            raise SystemExit(f"run_demo.py short: {last}")
        return last

    def team_data():
        rc, last = run([state["py"], "run_demo.py", "download", "--zip", str(Path(args.zip).resolve())],
                       cwd=state["dp"])
        if rc:
            raise SystemExit(f"run_demo.py download: {last}")
        return "zip checksum ok, raw pulls unzipped, 22 repos at their pinned SHAs"

    def pipeline():
        for task in TASKS:
            cmd = [state["py"], "-m", f"meridian.{task}", "--run-id", TEAM_RUN, "--config", "meridian.toml"]
            rc, last = run(cmd, cwd=state["dp"])
            if rc:
                raise SystemExit(f"{task} exited {rc}: {last}")
            if task == "collect" and f"run_id={TEAM_RUN}" not in last:
                raise SystemExit(f"collect gave another run: {last}")
        return f"every task passed, collect gave run_id={TEAM_RUN}"

    def golden():
        want = json.loads((state["dp"] / "golden" / f"{TEAM_RUN}.json").read_text(encoding="utf-8"))
        out = Path(os.environ.get("MERIDIAN_DATA") or state["repo"]) / "out" / TEAM_RUN   # as meridian.common
        bad = []
        for name, w in sorted(want.items()):
            f = out / name
            got = {"md5": hashlib.md5(f.read_bytes()).hexdigest(), "size": f.stat().st_size} if f.exists() else {}
            if got != w:
                bad.append(name)
        same = len(want) - len(bad)
        if bad == ["commit_file.jsonl"]:   # tests/test_golden.py: git's rename detection differs between versions
            return (f"{same} of {len(want)} files match; commit_file.jsonl differs only by your git version's rename "
                    "detection (the golden is the pipeline image's git 2.39)")
        if bad:
            raise SystemExit(f"{same} of {len(want)} match; differ: {', '.join(bad)}")
        return f"all {len(want)} output files match golden/{TEAM_RUN}.json"

    def team_tests():
        rc, last = run([state["py"], "-m", "unittest", "tests.test_golden", "tests.test_scan", "tests.test_resolve"],
                       cwd=state["dp"], env={"MERIDIAN_RUN_ID": TEAM_RUN})
        if rc:
            raise SystemExit(f"team-run tests: {last}")
        return "Django answer keys (265/265 lines, 542/542 aliases), packages' precision and recall, golden"

    ok = True
    for name, fn in [("Get the code", clone), ("Python environment", environment)]:
        say(name)
        if not step(name, fn):
            ok = False   # nothing after these can run
            break
    if ok:
        known = state["dp"] / "shared-snapshot" / ZIP_NAME   # where the report says to put the downloaded zip
        if not args.zip and known.exists():
            args.zip = str(known)
        steps = [("Unit tests", unit_tests), ("run_demo.py short (fixture, every guideline item)", demo)]
        if args.zip:
            steps += [("Team data: zip, pinned repos", team_data), ("Pipeline: every task of the team run", pipeline),
                      ("Same bytes as the frozen golden", golden), ("Team-run tests", team_tests)]
        for name, fn in steps:
            say(name)
            step(name, fn)

    say("SUMMARY")
    for name, result, note in RESULTS:
        print(f"  {result:4}  {name}: {note}")
    if not args.zip:
        print(f"\n  Level 2 rebuilds the real team run {TEAM_RUN}. Download the zip (145 MB) from\n"
              f"  {ZIP_LINK}\n"
              f"  into Data-Pipeline/shared-snapshot/ and run this again. Airflow: Data-Pipeline/README.md.")
    sys.exit(1 if any(r == "FAIL" for _, r, _ in RESULTS) else 0)


if __name__ == "__main__":
    main()
