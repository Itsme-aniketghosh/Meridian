"""Collect · step 1 · D2: freeze one snapshot and write its `run` row. See CONTRACTS.md, "Snapshot".

    python -m meridian.collect --run-id new --config meridian.toml               # every dataset in meridian.toml
    python -m meridian.collect --run-id <run_id> --config meridian.toml          # again: must give the same run
    python -m meridian.collect --run-id new --datasets spark                     # just these
    python -m meridian.collect --run-id new --refresh jira/SPARK                 # pull a source again instead of reusing it

The run_id comes out of the inputs, so it can't be chosen up front: 'new' makes whatever run the inputs give.
A run_id instead checks the inputs still give that run, and fails if anything changed. Either way the last
line printed is `run_id=<run_id>`, for the tasks after this one.

1. Fills a cache in snapshots/_cache/: one bare clone per repo, raw Jira pages, raw GitLab issues.
   Kill it any time and rerun: it resumes. A finished source is reused as is, never re-queried, unless --refresh.
2. Builds the manifest: pinned refs -> full SHAs, sha256 per raw source, config_hash.
   run_id = first 16 hex of sha256 over the manifest without run_id, keys sorted. Same inputs -> same run_id.
3. If snapshots/<run_id>/ already exists, stops there. Otherwise checks out each repo at its SHA (full history),
   links the raw files in, and moves the folder into place only once it's complete.
4. Writes the run row to out/<run_id>/run.jsonl for the writer (D3).

A repo whose ref can't be resolved (TBD, or not in the repo) and a source whose pull fails are left out,
and the run is 'partial' with the reason in run.notes. Nothing is dropped silently.
"""
import gzip, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from meridian.common import CONFIG, data_root, handoff, load_config, log, parser

GITLAB_GRAPHQL = "https://gitlab.com/api/graphql"


def canon(obj):
    """The one JSON form we hash: sorted keys, no spaces, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def gz(data):
    return gzip.compress(data, mtime=0)  # mtime 0: the same body always gives the same bytes


def load_meta(d):
    p = d / ".pull.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_meta(d, meta):
    write_atomic(d / ".pull.json", (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode())


def dir_hash(d):
    """sha256 over 'path NUL sha256(file)' lines, sorted by path. Dotfiles (.pull.json, .work/) are not data."""
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if p.is_file() and not any(part.startswith(".") for part in rel.parts):
            h.update(f"{rel.as_posix()}\0{sha256(p.read_bytes())}\n".encode())
    return h.hexdigest()


# ───────────── http ─────────────

def curl(url, data=None, tries=6):
    """curl, not urllib: python.org builds on macOS ship without CA certs (same as week1/pair2/jira/pull.py)."""
    cmd = ["curl", "-s", "--max-time", "120", "-w", "\n%{http_code}"]
    if data is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    for attempt in range(tries):
        r = subprocess.run(cmd + [url], input=data, capture_output=True)
        body, _, code = r.stdout.rpartition(b"\n")
        code = int(code or 0)
        if r.returncode == 0 and code == 200:
            return body
        log(f"  retry {attempt}: http {code}, curl {r.returncode}, {url[:80]}")
        time.sleep(2 ** attempt * (10 if code == 429 else 1))
    raise RuntimeError(f"gave up after {tries} tries: {url[:120]}")


def gql(url, query, variables):
    for attempt in range(6):
        d = json.loads(curl(url, json.dumps({"query": query, "variables": variables}).encode()))
        if "errors" not in d:
            return d["data"]
        log(f"  graphql retry {attempt}: {str(d['errors'])[:200]}")
        time.sleep(5 * (attempt + 1))
    raise RuntimeError("graphql gave up")


# ───────────── repos ─────────────

def git(*args):
    return subprocess.run(["git", "-c", "core.longpaths=true", *args], check=True, capture_output=True, text=True).stdout


def repo_cache(cache, repo, url):
    """One bare clone per repo, full history, branches and tags. Shared by every run."""
    d = cache / "repos" / (repo.replace("/", "__") + ".git")
    if not (d / "HEAD").exists():
        tmp = d.with_name(d.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        log(f"  cloning {repo}")
        git("clone", "--bare", "--quiet", url, str(tmp))
        tmp.rename(d)
    return d


def resolve(d, ref):
    """Full SHA of ref, fetching once if the cache doesn't have it yet. None if it can't be resolved."""
    for attempt in (0, 1):
        r = subprocess.run(["git", "-C", str(d), "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
        if attempt == 0:
            subprocess.run(["git", "-C", str(d), "fetch", "--quiet", "origin",
                            "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"], capture_output=True)
    return None


# ───────────── Jira ─────────────

def pull_jira(dest, jira, stop_after=None):
    """Raw REST search pages, ORDER BY key as in the jql, saved as page-NNNNNN.json.gz.
    A page on disk is never fetched again, so a killed pull resumes where it stopped.
    stop_after: stop after fetching that many pages (tests use it to fake a kill). Returns None then."""
    dest.mkdir(parents=True, exist_ok=True)
    meta = load_meta(dest)
    if meta.get("finished_at"):
        return meta
    meta.setdefault("started_at", now())
    save_meta(dest, meta)
    page, start, fetched = jira.get("page", 100), 0, 0
    while "total" not in meta or start < meta["total"]:
        p = dest / f"page-{start:06d}.json.gz"
        if p.exists():
            meta.setdefault("total", json.loads(gzip.decompress(p.read_bytes()))["total"])
        else:
            q = urllib.parse.urlencode({"jql": jira["jql"], "startAt": start, "maxResults": page,
                                        "fields": jira.get("fields", "*all"), "expand": jira.get("expand", "")})
            body = curl(f"{jira['base']}?{q}")
            data = json.loads(body)
            meta.setdefault("total", data["total"])
            want = min(page, meta["total"] - start)
            if len(data["issues"]) != want:  # Jira caps maxResults silently; stepping by `page` would skip tickets
                raise RuntimeError(f"Jira returned {len(data['issues'])} issues at startAt={start}, expected {want}. Lower `page`")
            write_atomic(p, gz(body))
            save_meta(dest, meta)
            fetched += 1
            if fetched % 20 == 0:
                log(f"  jira {start + page}/{meta['total']}")
            if stop_after and fetched >= stop_after:
                return None
        start += page
    meta["finished_at"] = now()
    save_meta(dest, meta)
    return meta


# ───────────── GitLab ─────────────

ISSUES_Q = """query($after:String){ project(fullPath:"%s"){
  issues(createdAfter:"%s", createdBefore:"%s", first:100, after:$after, sort:CREATED_ASC){
    pageInfo{ hasNextPage endCursor }
    nodes{ iid title description state type createdAt updatedAt closedAt webUrl hasParent
      epic{ iid group{ fullPath } } movedTo{ iid } author{ username } assignees{ nodes{ username } } labels(first:100){ nodes{ title } }
      blockedByIssues{ nodes{ iid webUrl state closedAt labels(first:100){ nodes{ title } } } } } } } }"""
NOTE = "nodes{ body createdAt system author{ username } }"
NOTES_Q = """query($iids:[String!]){ project(fullPath:"%s"){ issues(iids:$iids, first:20){ nodes{ iid
  notes(first:100, filter:ONLY_ACTIVITY){ pageInfo{ hasNextPage endCursor } """ + NOTE + """ } } } } }"""
PARENTS_Q = """query($iids:[String!]){ project(fullPath:"%s"){ workItems(iids:$iids, first:20){ nodes{ iid
  widgets{ ... on WorkItemWidgetHierarchy{ parent{ iid workItemType{ name } } } } } } } }"""
NOTES_MORE_Q = """query($iid:String!, $after:String){ project(fullPath:"%s"){ issue(iid:$iid){
  notes(first:100, after:$after, filter:ONLY_ACTIVITY){ pageInfo{ hasNextPage endCursor } """ + NOTE + """ } } } }"""


def pull_gitlab(dest, ds):
    """Raw issues created in the window, with labels, current blocked-by links, epic, the work-item parent
    of every issue that has one (a task's issue), and every activity note (links added and removed, label changes),
    written as one issues.jsonl.gz sorted by iid.
    Notes are pulled only for issues whose title doesn't start with drop_titles_starting (the [Test] bot);
    those keep "notes": null. Resumable: issue pages by cursor, notes by iid."""
    work = dest / ".work"
    work.mkdir(parents=True, exist_ok=True)
    meta = load_meta(dest)
    if meta.get("finished_at"):
        return meta
    meta.setdefault("started_at", now())
    save_meta(dest, meta)
    url, proj = ds.get("graphql", GITLAB_GRAPHQL), ds["project"]
    frm, to = ds["issues_created"]

    if not meta.get("issues_done"):
        cur = work / "cursor.txt"
        after = (cur.read_text().strip() or None) if cur.exists() else None
        with open(work / "issues.jsonl", "a") as f:
            while True:
                page = gql(url, ISSUES_Q % (proj, frm, to), {"after": after})["project"]["issues"]
                for n in page["nodes"]:
                    f.write(json.dumps(n, ensure_ascii=False) + "\n")
                f.flush()
                after = page["pageInfo"]["endCursor"]
                cur.write_text(after or "")
                if page["nodes"]:
                    log(f"  gitlab issues up to {page['nodes'][-1]['createdAt']}")
                if not page["pageInfo"]["hasNextPage"]:
                    break
        meta["issues_done"] = True
        save_meta(dest, meta)

    issues = {}
    for line in open(work / "issues.jsonl"):
        n = json.loads(line)
        issues[n["iid"]] = n  # a page written twice after a kill: last copy wins
    parents_p = work / "parents.jsonl"
    done = {json.loads(l)["iid"] for l in open(parents_p)} if parents_p.exists() else set()
    todo = sorted((i for i, n in issues.items() if n.get("hasParent") and i not in done), key=int)
    with open(parents_p, "a") as f:
        for k in range(0, len(todo), 20):
            batch, got = todo[k:k + 20], {}
            for n in gql(url, PARENTS_Q % proj, {"iids": batch})["project"]["workItems"]["nodes"]:
                got[n["iid"]] = next((w["parent"] for w in n["widgets"] if w.get("parent")), None)
            for iid in batch:
                f.write(json.dumps({"iid": iid, "parent": got.get(iid)}) + "\n")
            f.flush()
        if todo:
            log(f"  gitlab parents {len(todo)}")
    parents = {}
    for line in open(parents_p):
        n = json.loads(line)
        parents[n["iid"]] = n["parent"]

    skip = tuple(ds.get("drop_titles_starting", []))
    want = sorted((i for i, n in issues.items() if not n["title"].startswith(skip)), key=int)
    notes_p = work / "notes.jsonl"
    done = {json.loads(l)["iid"] for l in open(notes_p)} if notes_p.exists() else set()
    todo = [i for i in want if i not in done]
    with open(notes_p, "a") as f:
        for k in range(0, len(todo), 20):
            batch = todo[k:k + 20]
            got = {}
            for n in gql(url, NOTES_Q % proj, {"iids": batch})["project"]["issues"]["nodes"]:
                notes, info = n["notes"]["nodes"], n["notes"]["pageInfo"]
                while info["hasNextPage"]:
                    more = gql(url, NOTES_MORE_Q % proj, {"iid": n["iid"], "after": info["endCursor"]})["project"]["issue"]["notes"]
                    notes, info = notes + more["nodes"], more["pageInfo"]
                got[n["iid"]] = notes
            for iid in batch:  # an issue the API no longer returns keeps notes: null, and is counted
                f.write(json.dumps({"iid": iid, "notes": got.get(iid)}, ensure_ascii=False) + "\n")
            f.flush()
            if (k // 20) % 25 == 0:
                log(f"  gitlab notes {k + len(batch)}/{len(todo)}")

    notes = {}
    for line in open(notes_p):
        n = json.loads(line)
        notes[n["iid"]] = n["notes"]
    lines = []
    for iid in sorted(issues, key=int):
        lines.append(json.dumps({**issues[iid], "parent": parents.get(iid), "notes": notes.get(iid)}, sort_keys=True, ensure_ascii=False))
    write_atomic(dest / "issues.jsonl.gz", gz(("\n".join(lines) + "\n").encode()))
    meta.update(finished_at=now(), issues=len(issues), notes_skipped=len(issues) - len(want),
                notes_missing=sum(1 for i in want if notes.get(i) is None))
    save_meta(dest, meta)
    return meta


# ───────────── snapshot ─────────────

def image_digest():
    """No container or lockfile yet (step 1), so: the tool versions plus this script."""
    return sha256(canon({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                         "git": git("--version").strip(), "collect": sha256(Path(__file__).read_bytes())}).encode())


def snapshot(config=CONFIG, datasets=None, root=None, refresh=(), snapshot_at=None):
    config, root = Path(config), Path(root or data_root())
    cfg = load_config(config)
    names = datasets or list(cfg["datasets"])
    if unknown := [n for n in names if n not in cfg["datasets"]]:
        raise SystemExit(f"not in {config.name}: {unknown}")
    snapshot_at = snapshot_at or cfg["run"].get("snapshot_at")  # a run-wide one wins (proposed, not in v1 yet)
    declared = {cfg["datasets"][n]["snapshot_at"] for n in names if "snapshot_at" in cfg["datasets"][n]}
    if not snapshot_at:
        if len(declared) != 1:
            raise SystemExit(f"need one snapshot_at, the config gives {sorted(declared) or 'none'}: pass --snapshot-at")
        snapshot_at = declared.pop()

    snaps = root / "snapshots"
    cache = snaps / "_cache"
    repos, sources, src_dirs, gaps, pulls = {}, {}, {}, [], []

    for name in names:
        ds = cfg["datasets"][name]
        log(f"{name}")
        for r in ds.get("repos", []):
            repo, ref = r["repo"], r["ref"]
            if ref.startswith("TBD"):
                gaps.append(f"{repo}: ref not set yet ({ref})")
                continue
            sha = resolve(repo_cache(cache, repo, r.get("url", f"https://github.com/{repo}.git")), ref)
            if not sha:
                gaps.append(f"{repo}: ref {ref} not found")
            elif repos.setdefault(repo, sha) != sha:
                raise SystemExit(f"{repo} is pinned at two SHAs: {repos[repo]} and {sha}")

        tracker = ds.get("tracker", "none")
        if tracker == "none":
            continue
        if tracker == "jira":
            m = re.search(r"project\s*=\s*(\w+)", ds["jira"]["jql"])
            key = f"jira/{m.group(1) if m else name.upper()}"
            pull = lambda d: pull_jira(d, ds["jira"])
        elif tracker == "gitlab":
            key = "gitlab"
            pull = lambda d: pull_gitlab(d, ds)
        else:
            raise SystemExit(f"{name}: unknown tracker {tracker!r}")
        d = cache / key
        if key in refresh:
            shutil.rmtree(d, ignore_errors=True)
        try:
            meta = pull(d)
        except RuntimeError as e:  # tracker down: code-only run, flagged partial (04-architecture, Failures)
            gaps.append(f"{key}: pull failed, rerun to resume ({e})")
            continue
        if meta["started_at"] <= snapshot_at:  # rollback to snapshot_at needs every change up to it
            raise SystemExit(f"{key} was pulled at {meta['started_at']}, before snapshot_at {snapshot_at}. Use --refresh {key}")
        sources[key], src_dirs[key] = dir_hash(d), d
        pulls.append(f"{key} pulled {meta['started_at']}..{meta['finished_at']}")

    manifest = {"snapshot_at": snapshot_at, "repos": repos, "sources": sources,
                "config_hash": sha256(canon(cfg).encode())}  # parsed, so comments and spacing don't count
    run_id = sha256(canon(manifest).encode())[:16]
    manifest = {"run_id": run_id, **manifest}

    dest = snaps / run_id
    if (dest / "manifest.json").exists():
        log(f"snapshots/{run_id} already exists: same inputs, same run")
    else:
        stage = snaps / f".staging-{run_id}"
        shutil.rmtree(stage, ignore_errors=True)
        for repo, sha in sorted(repos.items()):
            co = stage / "repos" / repo.replace("/", "__")
            log(f"  checkout {repo} @ {sha[:10]}")
            git("clone", "--shared", "--no-checkout", "--quiet", str(cache / "repos" / (repo.replace("/", "__") + ".git")), str(co))
            git("-C", str(co), "config", "core.longpaths", "true")
            git("-C", str(co), "checkout", "--quiet", "--detach", sha)
        for key, d in sorted(src_dirs.items()):
            for p in sorted(d.rglob("*")):
                rel = p.relative_to(d)
                if p.is_file() and not any(part.startswith(".") for part in rel.parts):
                    (stage / key / rel).parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(p, stage / key / rel)  # hard link: no second copy on disk
                    except OSError:
                        shutil.copy2(p, stage / key / rel)
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        stage.rename(dest)

    notes = sorted(gaps) + sorted(pulls)
    if sources:
        notes.append("ticket status, resolution and resolved_at are as of snapshot_at; other ticket fields are as of the pull")
    row = {"run_id": run_id, "snapshot_at": snapshot_at, "repo_shas": canon(repos), "source_hashes": canon(sources),
           "config_hash": manifest["config_hash"], "image_digest": image_digest(), "model_versions": "{}",
           "status": "partial" if gaps else "complete", "notes": " | ".join(notes)}
    out = handoff(cfg, run_id, root)
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(out / "run.jsonl", (json.dumps(row, ensure_ascii=False) + "\n").encode())
    return row


def main(argv=None):
    ap = parser(__doc__, "'new' for whatever run the inputs give, or a run_id the inputs must still give")
    ap.add_argument("--datasets", help="comma-separated, default all")
    ap.add_argument("--refresh", default="", help="comma-separated sources to pull again, e.g. jira/SPARK,gitlab")
    ap.add_argument("--snapshot-at", help="only when the chosen datasets don't declare exactly one")
    a = ap.parse_args(argv)
    row = snapshot(a.config, a.datasets.split(",") if a.datasets else None, None,
                   tuple(filter(None, a.refresh.split(","))), a.snapshot_at)
    log(f"status {row['status']}")
    for n in filter(None, row["notes"].split(" | ")):
        log(f"  {n}")
    if a.run_id != "new" and row["run_id"] != a.run_id:
        raise SystemExit(f"asked for run {a.run_id}, but the inputs now give {row['run_id']}: config, a ref or a source changed")
    log(f"run_id={row['run_id']}")
    return row


if __name__ == "__main__":
    main()
