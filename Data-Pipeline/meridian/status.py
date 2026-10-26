"""Status · how stale the published block is: the "Update graph" button's line, and meridian_update's first task.

    python -m meridian.status --config meridian.toml [--json]

Prints "data 2 days old · 14 new commits · run <run_id>": the published run (latest.json, see meridian.publish), the age
of its snapshot_at, and, per repo in meridian.toml, the commits between its published SHA and the tip of its ref in
the repo's local mirror ([status] mirrors: collect's bare clones). A repo that follows a branch has its mirror fetched
first ([status] fetch), so a push made since the last collect counts: without it the "Update graph" click and the daily
run saw nothing until stale_after_hours passed. A pinned repo is never fetched. A fetch that fails (offline) leaves the
mirror as the last collect did. A repo whose mirror or ref isn't there is unknown, and never counts as new by its commits.

Exit 0 = something new: nothing published yet, a new commit, a repo in meridian.toml the published run doesn't have
(no mirror needed), a meridian.toml whose config_hash isn't the published run's, or a publish older than [status]
stale_after_hours (ticket changes can't be seen without a pull). Exit 99 = nothing new. Anything else is an error.

    python -m meridian.status --update        # the "Update graph" click: trigger meridian_update

--update triggers meridian_update through Airflow's REST API ([dags.update] api, the login from the environment
variables it names, else Data-Pipeline/.env), unless a run is already queued: that one starts after the current run and
takes every push up to its start, so a second click adds nothing. One run at a time (max_active_runs = 1), and its
anything_new step skips the run when nothing changed.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from meridian import publish
from meridian.collect import canon, fetch, rev, sha256
from meridian.common import CONFIG, HERE, data_root, git, load_config, load_settings, log

NOTHING_NEW = 99
TIME = "%Y-%m-%dT%H:%M:%SZ"


def refresh(mirror, ref):
    """Fetch the mirror if ref is one of its branches (a followed repo). True if it fetched."""
    return (mirror / "HEAD").exists() and rev(mirror, f"refs/heads/{ref}") is not None and fetch(mirror)


def new_commits(mirror, published_sha, ref):
    """Commits on ref's tip that published_sha doesn't have (all of them for a repo never published). None = unknown."""
    try:
        tip = git(mirror, "rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
        return int(git(mirror, "rev-list", "--count", f"{published_sha}..{tip}" if published_sha else tip))
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None


def status(config=CONFIG, root=None, settings=None, now=None):
    cfg, s = load_config(config), load_settings("status", settings)
    root, now = Path(root or data_root()), now or datetime.now(timezone.utc)
    pub = publish.latest(root, settings)
    shas, mirrors = pub.get("repo_shas", {}), root / s.get("mirrors", "snapshots/_cache/repos")
    listed = [r for ds in cfg.get("datasets", {}).values() for r in ds.get("repos", [])]
    mirror = lambda r: mirrors / (r["repo"].replace("/", "__") + ".git")
    if s.get("fetch", True):
        for r in listed:
            refresh(mirror(r), r["ref"])
    repos = {r["repo"]: new_commits(mirror(r), shas.get(r["repo"]), r["ref"]) for r in listed}
    since = lambda key: now - datetime.strptime(pub[key], TIME).replace(tzinfo=timezone.utc)
    commits = sum(n for n in repos.values() if n)
    stale = not pub or since("published_at").total_seconds() > s.get("stale_after_hours", 24) * 3600
    new_repos = sorted(r["repo"] for r in listed if r["repo"] not in shas)
    config_changed = bool(pub) and pub.get("config_hash") != sha256(canon(cfg).encode())   # collect's config_hash
    return {"run_id": pub.get("run_id"), "snapshot_at": pub.get("snapshot_at"), "published_at": pub.get("published_at"),
            "age_days": since("snapshot_at").days if pub else None, "new_commits": commits, "repos": repos,
            "new_repos": new_repos, "config_changed": config_changed, "stale": stale,
            "new": stale or commits > 0 or bool(new_repos) or config_changed}


def line(st):
    if not st["run_id"]:
        return "nothing published yet"
    unknown = sum(n is None for n in st["repos"].values())
    return (f"data {st['age_days']} day{'s' * (st['age_days'] != 1)} old · {st['new_commits']} new commits · "
            f"run {st['run_id']}" + (f" · {unknown} repos unknown" if unknown else "")
            + (f" · {len(st['new_repos'])} new repos" if st["new_repos"] else "")
            + (" · config changed" if st["config_changed"] else ""))


def trigger(settings=None, env_file=HERE / ".env"):
    """Queue a meridian_update run, unless one is queued already. Returns what happened, for the button."""
    d = load_settings("dags", settings)["update"]
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    env = {**dict(s.strip().split("=", 1) for s in lines if "=" in s and not s.lstrip().startswith("#")), **os.environ}
    login = base64.b64encode(f"{env.get(d['api_user_env'], '')}:{env.get(d['api_password_env'], '')}".encode()).decode()

    def call(path, body=None):
        req = urllib.request.Request(f"{d['api']}/dags/{d['dag_id']}/dagRuns{path}", None if body is None else json.dumps(body).encode(),
                                     {"Authorization": f"Basic {login}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    if queued := call("?state=queued")["dag_runs"]:
        return f"already queued: {queued[0]['dag_run_id']}"
    return f"queued: {call('', {'conf': {}})['dag_run_id']}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, default=CONFIG, help="meridian.toml")
    ap.add_argument("--json", action="store_true", help="the whole status as JSON")
    ap.add_argument("--update", action="store_true", help="trigger meridian_update (the Update graph button)")
    a = ap.parse_args(argv)
    if a.update:
        log(trigger())
        return 0
    st = status(a.config)
    log(json.dumps(st, indent=1, sort_keys=True) if a.json else line(st))
    return 0 if st["new"] else NOTHING_NEW


if __name__ == "__main__":
    sys.exit(main())
