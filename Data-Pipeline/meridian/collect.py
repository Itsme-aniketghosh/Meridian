"""Collect · step 1 · D2: freeze one snapshot and write its `run` row. See CONTRACTS.md, "Snapshot".

    python -m meridian.collect --run-id new --config meridian.toml           # every dataset in meridian.toml
    python -m meridian.collect --run-id <run_id> --config meridian.toml      # again: must give the same run.
                                                                             # snapshots/<run_id>/ exists: replays its
                                                                             # SHAs and snapshot_at
    python -m meridian.collect --run-id new --datasets spark                 # just these
    python -m meridian.collect --run-id new --refresh jira/SPARK             # pull a source again instead of reusing it

The run_id comes out of the inputs, so it can't be chosen up front: 'new' makes whatever run the inputs give.
A run_id instead checks the inputs still give that run, and fails if anything changed. Either way the last
line printed is `run_id=<run_id>`, for the tasks after this one.

1. Repos: one permanent bare mirror per repo (settings.toml [collect] mirrors), kept between runs. A ref that names a
   branch is followed: the mirror is fetched and the branch resolved to where it is now. Any other ref (a SHA, sha^1)
   is pinned, and fetched only if the mirror doesn't have it yet.
2. snapshot_at: --snapshot-at if given. Else, if any repo follows a branch, the pull time: read here, after the
   fetches, the only clock in the pipeline. Else the run-wide or the one per-dataset snapshot_at in meridian.toml.
3. Tickets, in snapshots/_cache/<source>/: a base pull, then delta-<time>/ folders, each the tickets changed since
   the last pull started (the cursor, in cursor.json), each in full with its whole changelog or notes, plus every
   key or iid that still exists (keys.json.gz / iids.json.gz: a deleted ticket is one missing there). A run reads
   the base and the deltas up to the first pull that started after snapshot_at (rollback needs every change up to
   it); if none did, it pulls a new delta. Later pulls are left out, so the same snapshot_at reads the same files.
   Kill it any time and rerun: it resumes. A finished pull is never re-queried; --refresh drops the source's pulls.
4. Builds the manifest: refs -> full SHAs, sha256 per raw source over the pulls it reads (cursors included),
   config_hash. run_id = first 16 hex of sha256 over the manifest without run_id, keys sorted.
   Same inputs -> same run_id.
5. If snapshots/<run_id>/ already exists, stops there. Otherwise checks out each repo at its SHA (full history,
   objects borrowed from the mirror, which keeps a refs/runs/<run_id> so they're never pruned), links the raw
   files in, and moves the folder into place only once it's complete.
6. Writes the run row to out/<run_id>/run.jsonl for the writer (D3).

A repo whose ref can't be resolved (TBD, or not in the repo) and a source whose pull fails are left out,
and the run is 'partial' with the reason in run.notes. Nothing is dropped silently.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from meridian.common import (CONFIG, FIXED, atomic_write, data_root, git, handoff, load_config, load_infra, log,
                             parser, repo_checkout, write_jsonl)
from meridian.tickets import source_key


def canon(obj):
    """The one JSON form we hash: sorted keys, no spaces, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path, data):
    atomic_write(path, lambda tmp: tmp.write_bytes(data))


def gz(data):
    """The same body always gives the same bytes: mtime 0, and the header's OS byte fixed (Python 3.11 writes the
    machine's: 0 on Windows, 3 on Linux), so a pull hashes the same wherever it was made."""
    b = bytearray(gzip.compress(data, mtime=0))
    b[9] = 255  # "unknown", what gzip.GzipFile writes
    return bytes(b)


def load_meta(d):
    p = d / ".pull.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def read_lines(p):
    """Lines of a .work/ file a resumable pull appends to; none if it doesn't exist yet. Half a line (killed mid-write)
    is cut off the file too, so the next append starts a line of its own."""
    if not p.exists():
        return []
    b = p.read_bytes()
    b = b[:b.rfind(b"\n") + 1]
    os.truncate(p, len(b))
    return b.decode("utf-8").split("\n")[:-1]


def save_meta(d, meta):
    write_atomic(d / ".pull.json", (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode())


def data_files(d, used=None):
    """(path relative to d, path) of the data under d, sorted by path. Dotfiles (.pull.json, .work/) are not data,
    and a delta-*/ folder is only if it's in used (default: every one)."""
    keep = lambda rel: not any(part.startswith(".") for part in rel.parts) and (
        used is None or not rel.parts[0].startswith("delta-") or d / rel.parts[0] in used)
    return [(p.relative_to(d), p) for p in sorted(d.rglob("*")) if p.is_file() and keep(p.relative_to(d))]


def dir_hash(d, used=None):
    """sha256 over 'path NUL sha256(file)' lines of data_files(d, used)."""
    h = hashlib.sha256()
    for rel, p in data_files(d, used):
        h.update(f"{rel.as_posix()}\0{sha256(p.read_bytes())}\n".encode())
    return h.hexdigest()


# ───────────── http ─────────────

def curl(url, data=None):
    """curl, not urllib: python.org builds on macOS ship without CA certs (same as week1/pair2/jira/pull.py)."""
    net = load_infra("collect")
    tries = net.get("http_tries", 6)
    cmd = ["curl", "-s", "--max-time", str(net.get("http_timeout_s", 120)), "-w", "\n%{http_code}"]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    for attempt in range(tries):
        r = subprocess.run(cmd + [url], input=data, capture_output=True)
        body, _, code = r.stdout.rpartition(b"\n")
        code = int(code or 0)
        if r.returncode == 0 and code == 200:
            return body
        log(f"  retry {attempt}: http {code}, curl {r.returncode}, {url[:80]}")
        time.sleep(2 ** attempt * net.get("http_backoff_s", 1) * (net.get("http_429_factor", 10) if code == 429 else 1))
    raise RuntimeError(f"gave up after {tries} tries: {url[:120]}")


def gql(url, query, variables):
    net = load_infra("collect")
    for attempt in range(net.get("graphql_tries", 6)):
        d = json.loads(curl(url, json.dumps({"query": query, "variables": variables}).encode()))
        if "errors" not in d:
            return d["data"]
        log(f"  graphql retry {attempt}: {str(d['errors'])[:200]}")
        time.sleep(net.get("graphql_backoff_s", 5) * (attempt + 1))
    raise RuntimeError("graphql gave up")


# ───────────── repos ─────────────

def mirror(mirrors, repo, url):
    """One bare clone per repo, full history, branches and tags. Shared by every run, never deleted."""
    d = mirrors / (repo.replace("/", "__") + ".git")
    if not (d / "HEAD").exists():
        tmp = d.with_name(d.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        log(f"  cloning {repo}")
        git(".", "-c", "core.longpaths=true", "clone", "--bare", "--quiet", url, str(tmp))
        tmp.rename(d)
    return d


def borrow_relative(co, src):
    """Point checkout co at its mirror src by a relative path. `clone --shared` writes the mirror's absolute path into
    .git/objects/info/alternates, so a data root mounted elsewhere (Docker's /opt/meridian, another machine) has a
    checkout whose history git can't find: every git call in history and resolve failed there. Relative to the
    checkout's objects folder, the link holds wherever snapshots/ is mounted. Different drives (Windows): left as is."""
    alt = co / ".git" / "objects" / "info" / "alternates"
    if not alt.exists():
        return
    try:
        rel = os.path.relpath(os.path.abspath(src / "objects"), os.path.abspath(co / ".git" / "objects"))
    except ValueError:
        return
    if alt.read_text(encoding="utf-8").strip() != rel.replace(os.sep, "/"):
        alt.write_text(rel.replace(os.sep, "/") + "\n", encoding="utf-8", newline="\n")


def rev(d, ref):
    try:
        return git(d, "rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
    except subprocess.CalledProcessError:
        return None


def fetch(d):
    try:
        git(d, "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
        return True
    except subprocess.CalledProcessError:
        return False  # offline or gone: a pinned ref stays unresolved, a branch stays where it was


def resolve(d, ref):
    """(full SHA or None, follows, fetched). A ref naming a branch of the mirror is followed: fetched, then resolved
    to where the branch is now. Any other ref (a SHA, sha^1) is pinned, fetched only if the mirror doesn't have it."""
    head = f"refs/heads/{ref}"
    follows = rev(d, head) is not None
    fetched = follows and fetch(d)
    sha = rev(d, head if follows else ref)
    if sha is None:  # not in the mirror yet: a commit or a branch made since the last fetch
        fetched = fetch(d)
        follows = rev(d, head) is not None
        sha = rev(d, head if follows else ref)
    return sha, follows, fetched


# ───────────── Jira ─────────────

def pull_jira(dest, jira, stop_after=None, since=None):
    """Raw REST search pages, ORDER BY key as in the jql, saved as page-NNNNNN.json.gz.
    A page on disk is never fetched again, so a killed pull resumes where it stopped.
    since: only tickets updated since then (a delta). Jira reads that time in its own zone, to the minute: the
    cursor's overlap (settings.toml) covers it, and a ticket pulled twice is the same ticket.
    stop_after: stop after fetching that many pages (tests use it to fake a kill). Returns None then."""
    dest.mkdir(parents=True, exist_ok=True)
    meta = load_meta(dest)
    if meta.get("finished_at"):
        return meta
    meta.setdefault("started_at", now())
    save_meta(dest, meta)
    page, start, fetched, jql = jira.get("page", 100), 0, 0, jira["jql"]
    if since:
        m = re.fullmatch(r"(?is)(.*?)(\s+order\s+by\s.*)?", jql)
        jql = f'({m[1]}) AND updated >= "{since[:16].replace("T", " ")}"{m[2] or ""}'
    while "total" not in meta or start < meta["total"]:
        p = dest / f"page-{start:06d}.json.gz"
        if p.exists():
            meta.setdefault("total", json.loads(gzip.decompress(p.read_bytes()))["total"])
        else:
            q = urllib.parse.urlencode({"jql": jql, "startAt": start, "maxResults": page,
                                        "fields": jira.get("fields", "*all"), "expand": jira.get("expand", "")})
            body = curl(f"{jira['base']}?{q}")
            data = json.loads(body)
            meta.setdefault("total", data["total"])
            want = min(page, max(0, data["total"] - start))  # this page's total: tickets are filed mid-pull
            if len(data["issues"]) != want:  # Jira caps maxResults silently; stepping by `page` would skip tickets
                raise RuntimeError(f"Jira returned {len(data['issues'])} issues at startAt={start}, expected {want}. "
                                   "Lower `page`")
            write_atomic(p, gz(body))
            save_meta(dest, meta)
            fetched += 1
            if fetched % 20 == 0:
                log(f"  jira {start + page}/{meta['total']}")
            if stop_after and fetched >= stop_after:
                return None
        start += page
    if since and "keys" not in meta:
        meta["keys"] = list_jira_keys(dest, jira)
    meta["finished_at"] = now()
    save_meta(dest, meta)
    return meta


def list_jira_keys(dest, jira):
    """Every key the jql matches now, as keys.json.gz: a ticket the delta's `updated >=` can't return because it was
    deleted is one missing here. Paged by key, not startAt: a ticket deleted mid-listing would shift the offsets
    and skip a live key. Resumable by the last key in .work/keys.txt."""
    work = dest / ".work"
    work.mkdir(exist_ok=True)
    keys = read_lines(work / "keys.txt")
    m = re.fullmatch(r"(?is)(.*?)(\s+order\s+by\s.*)?", jira["jql"])
    with open(work / "keys.txt", "a", encoding="utf-8", newline="\n") as f:
        while True:
            after = f' AND key > "{keys[-1]}"' if keys else ""
            q = urllib.parse.urlencode({"jql": f"({m[1]}){after} ORDER BY key ASC", "startAt": 0,
                                        "maxResults": jira.get("keys_page", 1000), "fields": "key"})
            got = [it["key"] for it in json.loads(curl(f"{jira['base']}?{q}"))["issues"]]
            if not got:  # stop on an empty page, not a short one: Jira caps maxResults silently
                break
            f.write("".join(k + "\n" for k in got))
            f.flush()
            keys += got
    write_atomic(dest / "keys.json.gz", gz((canon(sorted(set(keys))) + "\n").encode()))
    return len(set(keys))


# ───────────── GitLab ─────────────

ISSUES_Q = """query($after:String){ project(fullPath:"%s"){
  issues(createdAfter:"%s", createdBefore:"%s"%s, first:100, after:$after, sort:CREATED_ASC){
    pageInfo{ hasNextPage endCursor }
    nodes{ iid title description state type createdAt updatedAt closedAt webUrl hasParent
      epic{ iid group{ fullPath } } movedTo{ iid } author{ username } assignees{ nodes{ username } } labels(first:100){ nodes{ title } }
      blockedByIssues{ nodes{ iid webUrl state closedAt labels(first:100){ nodes{ title } } } } } } } }"""  # noqa: E501
NOTE = "nodes{ body createdAt system author{ username } }"
NOTES_Q = """query($iids:[String!]){ project(fullPath:"%s"){ issues(iids:$iids, first:20){ nodes{ iid
  notes(first:100, filter:ONLY_ACTIVITY){ pageInfo{ hasNextPage endCursor } """ + NOTE + """ } } } } }"""
PARENTS_Q = """query($iids:[String!]){ project(fullPath:"%s"){ workItems(iids:$iids, first:20){ nodes{ iid
  widgets{ ... on WorkItemWidgetHierarchy{ parent{ iid workItemType{ name } } } } } } } }"""
NOTES_MORE_Q = """query($iid:String!, $after:String){ project(fullPath:"%s"){ issue(iid:$iid){
  notes(first:100, after:$after, filter:ONLY_ACTIVITY){ pageInfo{ hasNextPage endCursor } """ + NOTE + """ } } } }"""


def pull_gitlab(dest, ds, since=None):
    """Raw issues created in the window, with labels, current blocked-by links, epic, the work-item parent
    of every issue that has one (a task's issue), and every activity note (links added and removed, label changes),
    written as one issues.jsonl.gz sorted by iid. since: only issues updated since then (a delta), each in full.
    Notes are pulled only for issues whose title doesn't start with drop_titles_starting (the [Test] bot);
    those keep "notes": null. Resumable: issue pages by cursor, notes by iid."""
    work = dest / ".work"
    work.mkdir(parents=True, exist_ok=True)
    meta = load_meta(dest)
    if meta.get("finished_at"):
        return meta
    meta.setdefault("started_at", now())
    save_meta(dest, meta)
    url, proj, batch_n = ds["graphql"], ds["project"], load_infra("collect").get("gitlab_batch", 20)
    frm, to = ds["issues_created"]

    if not meta.get("issues_done"):
        cur = work / "cursor.txt"
        after = (cur.read_text(encoding="utf-8").strip() or None) if cur.exists() else None
        read_lines(work / "issues.jsonl")
        with open(work / "issues.jsonl", "a", encoding="utf-8", newline="\n") as f:
            while True:
                page = gql(url, ISSUES_Q % (proj, frm, to, f', updatedAfter:"{since}"' if since else ""),
                           {"after": after})["project"]["issues"]
                for n in page["nodes"]:
                    f.write(json.dumps(n, ensure_ascii=False) + "\n")
                f.flush()
                after = page["pageInfo"]["endCursor"]
                write_atomic(cur, (after or "").encode())
                if page["nodes"]:
                    log(f"  gitlab issues up to {page['nodes'][-1]['createdAt']}")
                if not page["pageInfo"]["hasNextPage"]:
                    break
        meta["issues_done"] = True
        save_meta(dest, meta)

    issues = {}
    for line in read_lines(work / "issues.jsonl"):
        n = json.loads(line)
        issues[n["iid"]] = n  # a page written twice after a kill: last copy wins
    parents_p = work / "parents.jsonl"
    done = {json.loads(l)["iid"] for l in read_lines(parents_p)}
    todo = sorted((i for i, n in issues.items() if n.get("hasParent") and i not in done), key=int)
    with open(parents_p, "a", encoding="utf-8", newline="\n") as f:
        for k in range(0, len(todo), batch_n):
            batch, got = todo[k:k + batch_n], {}
            for n in gql(url, PARENTS_Q % proj, {"iids": batch})["project"]["workItems"]["nodes"]:
                got[n["iid"]] = next((w["parent"] for w in n["widgets"] if w.get("parent")), None)
            for iid in batch:
                f.write(json.dumps({"iid": iid, "parent": got.get(iid)}) + "\n")
            f.flush()
        if todo:
            log(f"  gitlab parents {len(todo)}")
    parents = {}
    for line in read_lines(parents_p):
        n = json.loads(line)
        parents[n["iid"]] = n["parent"]

    skip = tuple(ds.get("drop_titles_starting", []))
    want = sorted((i for i, n in issues.items() if not n["title"].startswith(skip)), key=int)
    notes_p = work / "notes.jsonl"
    done = {json.loads(l)["iid"] for l in read_lines(notes_p)}
    todo = [i for i in want if i not in done]
    with open(notes_p, "a", encoding="utf-8", newline="\n") as f:
        for k in range(0, len(todo), batch_n):
            batch = todo[k:k + batch_n]
            got = {}
            for n in gql(url, NOTES_Q % proj, {"iids": batch})["project"]["issues"]["nodes"]:
                notes, info = n["notes"]["nodes"], n["notes"]["pageInfo"]
                while info["hasNextPage"]:
                    more = gql(url, NOTES_MORE_Q % proj,
                               {"iid": n["iid"], "after": info["endCursor"]})["project"]["issue"]["notes"]
                    notes, info = notes + more["nodes"], more["pageInfo"]
                got[n["iid"]] = notes
            for iid in batch:  # an issue the API no longer returns keeps notes: null, and is counted
                f.write(json.dumps({"iid": iid, "notes": got.get(iid)}, ensure_ascii=False) + "\n")
            f.flush()
            if (k // batch_n) % 25 == 0:
                log(f"  gitlab notes {k + len(batch)}/{len(todo)}")

    notes = {}
    for line in read_lines(notes_p):
        n = json.loads(line)
        notes[n["iid"]] = n["notes"]
    lines = []
    for iid in sorted(issues, key=int):
        lines.append(json.dumps({**issues[iid], "parent": parents.get(iid), "notes": notes.get(iid)}, sort_keys=True,
                                ensure_ascii=False))
    write_atomic(dest / "issues.jsonl.gz", gz(("\n".join(lines) + "\n").encode()))
    if since:
        meta["iids"] = list_gitlab_iids(dest, ds)
    meta.update(finished_at=now(), issues=len(issues), notes_skipped=len(issues) - len(want),
                notes_missing=sum(1 for i in want if notes.get(i) is None))
    save_meta(dest, meta)
    return meta


IIDS_Q = """query($after:String){ project(fullPath:"%s"){
  issues(createdAfter:"%s", createdBefore:"%s", first:100, after:$after, sort:CREATED_ASC){
    pageInfo{ hasNextPage endCursor } nodes{ iid } } } }"""


def list_gitlab_iids(dest, ds):
    """Every iid in the window now, as iids.json.gz: what a delta needs to notice a deleted issue (list_jira_keys).
    GitLab's cursor is keyset, so a deletion mid-listing can't skip one."""
    url, proj, (frm, to), after, iids = ds["graphql"], ds["project"], ds["issues_created"], None, set()
    while True:
        page = gql(url, IIDS_Q % (proj, frm, to), {"after": after})["project"]["issues"]
        iids |= {n["iid"] for n in page["nodes"]}
        after = page["pageInfo"]["endCursor"]
        if not page["pageInfo"]["hasNextPage"]:
            break
    write_atomic(dest / "iids.json.gz", gz((canon(sorted(iids, key=int)) + "\n").encode()))
    return len(iids)


# ───────────── tickets: the base pull and deltas ─────────────

def pull_source(d, pull, snapshot_at):
    """The pulls of one source a run reads: the base pull d, then each delta-*/ in the order pulled, up to the first
    that started after snapshot_at. If none did, pulls a new delta: what changed since the newest of them started,
    less settings.toml's overlap. pull(dest, since) does one pull or resumes it; since None is the base.
    Returns (the pulls read, the last one's meta)."""
    todo, used, meta = [d, *sorted(d.glob("delta-*"))], [], None
    while meta is None or meta["started_at"] <= snapshot_at:  # rollback to snapshot_at needs every change up to it
        if not todo:
            start = now()
            if start <= snapshot_at:
                raise SystemExit(f"snapshot_at {snapshot_at} is in the future: a pull must start after it")
            back = timedelta(minutes=load_infra("collect").get("delta_overlap_minutes", 1440))
            since = (datetime.strptime(meta["started_at"], "%Y-%m-%dT%H:%M:%SZ") - back).strftime("%Y-%m-%dT%H:%M:%SZ")
            name = f"delta-{start.replace('-', '').replace(':', '')}"
            p, tmp = d / name, d / f".{name}"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            write_atomic(tmp / "cursor.json", (canon({"since": since}) + "\n").encode())
            save_meta(tmp, {"started_at": start})
            tmp.rename(p)  # a delta folder always has its cursor
            todo.append(p)
        p = todo.pop(0)
        meta = pull(p, json.loads((p / "cursor.json").read_text(encoding="utf-8"))["since"] if p != d else None)
        used.append(p)
    return used, meta


# ───────────── snapshot ─────────────

def image_digest():
    """No container or lockfile yet (step 1), so: the tool versions plus this script."""
    return sha256(canon({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                         "git": git(".", "--version").strip(),
                         "collect": sha256(Path(__file__).read_bytes())}).encode())


def snapshot(config=CONFIG, datasets=None, root=None, refresh=(), snapshot_at=None, pins=None, expect=None):
    """pins: {repo: SHA} to use instead of resolving each ref (replaying a run: see main). expect: the run_id the
    inputs must give, checked before anything is checked out or written."""
    config, root = Path(config), Path(root or data_root())
    cfg = load_config(config)
    names = datasets or list(cfg["datasets"])
    if unknown := [n for n in names if n not in cfg["datasets"]]:
        raise SystemExit(f"not in {config.name}: {unknown}")
    keys = [k for n, ds in cfg["datasets"].items() if (k := source_key(n, ds))]
    if same := sorted({k for k in keys if keys.count(k) > 1}):  # one cache folder, and tickets would read it twice
        raise SystemExit(f"two datasets pull the same source: {same}")
    snaps = root / "snapshots"
    cache, mirrors = snaps / "_cache", root / load_infra("collect").get("mirrors", "snapshots/_cache/repos")
    repos, sources, src_dirs, gaps, pulls, follows = {}, {}, {}, [], [], False

    for name in names:
        log(f"{name}")
        for r in cfg["datasets"][name].get("repos", []):
            repo, ref = r["repo"], r["ref"]
            if ref.startswith("TBD"):
                gaps.append(f"{repo}: ref not set yet ({ref})")
                continue
            d = mirror(mirrors, repo, r.get("url", f"https://github.com/{repo}.git"))
            if pins and repo in pins:  # replaying a run: its SHA, wherever the branch is now
                sha = rev(d, pins[repo]) or (fetch(d) and rev(d, pins[repo]))
            else:
                sha, branch, fetched = resolve(d, ref)
                follows |= branch
                if branch and not fetched:
                    gaps.append(f"{repo}: fetch failed, {ref} is where the last fetch left it")
            if not sha:
                gaps.append(f"{repo}: ref {ref} not found")
            elif repos.setdefault(repo, sha) != sha:
                raise SystemExit(f"{repo} is pinned at two SHAs: {repos[repo]} and {sha}")

    if not snapshot_at and follows:
        snapshot_at = now()  # the pull time: after the fetches, before the ticket pulls
        while now() <= snapshot_at:  # a ticket pull must start after snapshot_at (pull_source): under a second
            time.sleep(0.1)
    snapshot_at = snapshot_at or cfg["run"].get("snapshot_at")  # a run-wide one wins (proposed, not in v1 yet)
    declared = {cfg["datasets"][n]["snapshot_at"] for n in names if "snapshot_at" in cfg["datasets"][n]}
    if not snapshot_at:
        if len(declared) != 1:
            raise SystemExit(f"need one snapshot_at, the config gives {sorted(declared) or 'none'}: pass --snapshot-at")
        snapshot_at = declared.pop()
    fmt = FIXED[("run", "time_format")]
    try:
        ok = datetime.strptime(snapshot_at, fmt).strftime(fmt) == snapshot_at
    except (TypeError, ValueError):
        ok = False
    if not ok:  # cutoffs are compared as text: "2026-10-01" sorts before every time that day
        raise SystemExit(f"snapshot_at {snapshot_at!r} is not {fmt}")

    for name in names:
        ds = cfg["datasets"][name]
        tracker = ds.get("tracker", "none")
        if tracker == "none":
            continue
        key = source_key(name, ds)
        if tracker == "jira":
            pull = lambda d, since: pull_jira(d, ds["jira"], since=since)
        elif tracker == "gitlab":
            pull = lambda d, since: pull_gitlab(d, ds, since)
        else:
            raise SystemExit(f"{name}: unknown tracker {tracker!r}")
        d = cache / key
        if key in refresh:
            shutil.rmtree(d, ignore_errors=True)
        try:
            used, meta = pull_source(d, pull, snapshot_at)
        except RuntimeError as e:  # tracker down: code-only run, flagged partial (04-architecture, Failures)
            gaps.append(f"{key}: pull failed, rerun to resume ({e})")
            continue
        sources[key], src_dirs[key] = dir_hash(d, used), (d, used)
        pulls.append(f"{key} pulled {load_meta(d)['started_at']}..{meta['finished_at']}"
                     + (f", base pull + {len(used) - 1} delta(s)" if len(used) > 1 else ""))

    manifest = {"snapshot_at": snapshot_at, "repos": repos, "sources": sources,
                "config_hash": sha256(canon(cfg).encode())}  # parsed, so comments and spacing don't count
    run_id = sha256(canon(manifest).encode())[:16]
    if expect and run_id != expect:
        raise SystemExit(f"asked for run {expect}, but the inputs now give {run_id}: config, a ref or a source changed")
    manifest = {"run_id": run_id, **manifest}

    dest = snaps / run_id
    if (dest / "manifest.json").exists():
        log(f"snapshots/{run_id} already exists: same inputs, same run")
        for repo in sorted(repos):   # a snapshot from before relative links, or moved: its checkouts must work here
            name = repo.replace("/", "__")
            borrow_relative(dest / "repos" / name, mirrors / f"{name}.git")
    else:
        stage = snaps / f".staging-{run_id}"
        # every killed collect's half checkout, not only this run's (2.5 GB each).
        # shortcut: assumes one collect staging per snapshots/ at a time (max_active_runs = 1); lock it if that changes
        for old in snaps.glob(".staging-*"):
            shutil.rmtree(old, ignore_errors=True)
        for repo, sha in sorted(repos.items()):
            co, src = repo_checkout(stage, repo), mirrors / (repo.replace("/", "__") + ".git")
            log(f"  checkout {repo} @ {sha[:10]}")
            # the checkout borrows the mirror's objects: gc keeps them
            git(src, "update-ref", f"refs/runs/{run_id}", sha)
            git(".", "-c", "core.longpaths=true", "-c", "core.symlinks=false", "clone", "--shared",
                "--no-checkout", "--quiet",
                str(src), str(co))
            borrow_relative(co, src)   # stage and dest are siblings, so the relative link survives the rename below
            git(co, "config", "core.longpaths", "true")
            git(co, "config", "core.symlinks", "false")  # a symlink is its target text on every OS, never followed
            git(co, "checkout", "--quiet", "--detach", sha)
        for key, (d, used) in sorted(src_dirs.items()):
            for rel, p in data_files(d, used):
                (stage / key / rel).parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(p, stage / key / rel)  # hard link: no second copy on disk
                except OSError:
                    shutil.copy2(p, stage / key / rel)
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8", newline="\n")
        stage.rename(dest)

    notes = sorted(gaps) + sorted(pulls)
    if sources:
        notes.append("ticket status, resolution and resolved_at are as of snapshot_at; "
                     "other ticket fields are as of the pull")
    row = {"run_id": run_id, "snapshot_at": snapshot_at, "repo_shas": canon(repos), "source_hashes": canon(sources),
           "config_hash": manifest["config_hash"], "image_digest": image_digest(), "model_versions": "{}",
           "status": "partial" if gaps else "complete", "notes": " | ".join(notes)}
    out = handoff(cfg, run_id, root)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "run.jsonl", [row])
    return row


def main(argv=None):
    ap = parser(__doc__, "'new' for whatever run the inputs give, or a run_id the inputs must still give")
    ap.add_argument("--datasets", help="comma-separated, default all")
    ap.add_argument("--refresh", default="",
                    help="comma-separated sources to pull again in full, deltas dropped, e.g. jira/SPARK,gitlab")
    ap.add_argument("--snapshot-at",
                    help="wins over the config and the pull time: when the chosen datasets don't declare "
                         "exactly one, or to make a branch-following run again from the same pulls")
    a = ap.parse_args(argv)
    snapshot_at, pins = a.snapshot_at, None
    made = data_root() / "snapshots" / a.run_id / "manifest.json"
    if a.run_id != "new" and made.exists() and not snapshot_at:
        # replay: a run asked for by id whose snapshot exists is made again from its own SHAs and snapshot_at, so a
        # branch that moved on, or the clock, can't turn it into another run (the weekly rebuild, a forced rerun).
        # The rest of the inputs (sources, config) must still give the same run_id, which is the check
        m = json.loads(made.read_text(encoding="utf-8"))
        snapshot_at, pins = m["snapshot_at"], m["repos"]
        log(f"replaying {a.run_id}: snapshot_at {snapshot_at}, {len(pins)} repos at their recorded SHAs")
    row = snapshot(a.config, a.datasets.split(",") if a.datasets else None, None,
                   tuple(filter(None, a.refresh.split(","))), snapshot_at, pins,
                   None if a.run_id == "new" else a.run_id)
    log(f"status {row['status']}")
    for n in filter(None, row["notes"].split(" | ")):
        log(f"  {n}")
    log(f"run_id={row['run_id']}")
    return row


if __name__ == "__main__":
    main()
