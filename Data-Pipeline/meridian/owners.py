"""Owners · step 1 · D3 + D4: person, person_alias, team_map. See CONTRACTS.md, "Chaitali · start kit".

    python -m meridian.owners --run-id <run_id> --config meridian.toml          # -> out/<run_id>/person*.jsonl, team_map.jsonl
    python -m meridian.owners --run-id <run_id> --top 20                        # also print the 20 biggest groups, to hand-check

Running it again for the same run replaces its own files with the same rows.

D4, team per Spark person: see person_teams(). The window is window_days before run.snapshot_at, never the clock.

D5, file_owner: one row per file at the pinned SHA, by the dataset's owner_mode.
- author (Spark): the team with the biggest share of the file's commits in the window, following renames, no merges,
  no bots, no .git-blame-ignore-revs. Below min_share, or no team, -> unowned. No commits in the window -> basis 'none'.
- path (Django), repo (packages): the [teams] rule decides, share = 1. Each prefix or repo used also goes in team_map.
- top_person_id is the person with most commits in the window. last_commit_at is the file's last counted commit, any time.
- The window ends at snapshot_at for author, and at the pinned commit's authored_at for path and repo: Django and the
  packages are pinned years before snapshot_at, where a snapshot_at window holds no commits.
The log ends with week 1's test, one window back: does the owner predict the next author better than the last toucher?

Identities, as [identity] in meridian.toml says:
- Every author email in git_commit, plus Co-authored-by emails (kept in the data, never counted for ownership).
- Union-find over emails. Two emails join when .mailmap maps one to the other, they share a normalised name,
  or they share an email local part of local_part_min_length+ characters. Same as week1/pair2/merged/build.py
  merge_people(), plus .mailmap.
- A local part seen under more than local_part_max_names names (chris@, contact@) never joins: across 18 repos it
  chained 112 strangers into one person.
- Generic names and bots never join on name or local part. Only .mailmap, which a human wrote, can join them.
- person_id = h(smallest email in the group). It stays while that email stays the smallest: an added email that sorts
  first, or two groups merging, gives a new person_id.
- --top also lists every group joined on a shared local part whose names differ: the risky merges, 2-4 emails each.

git_commit has no author names, so names, Co-authored-by and .mailmap are read from the snapshot checkout at the
pinned SHA (git's %aN / %aE apply that commit's .mailmap). The author emails must match git_commit exactly.
"""
import hashlib, json, re, subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

from meridian.common import CONFIG, data_root, git, handoff, load_config, log, parser, read_jsonl, write_jsonl

COAUTH = re.compile(r"^Co-authored-by:\s*(.*?)\s*<([^>]+)>", re.M | re.I)
FMT = "%x1e%H%x1f%an%x1f%ae%x1f%aN%x1f%aE%x1f%b"


def h(*fields):
    return hashlib.sha256("\x1f".join(fields).encode()).hexdigest()[:16]


def norm_name(name):
    return re.sub(r"[\s.\-_]", "", name.lower())


def local_part(email):
    return re.sub(r"^\d+\+", "", email.split("@")[0])  # github noreply: 12345+login


def read_people(co, sha):
    """[(raw_email, mapped_email, mapped_name, coauthors)] per commit reachable from sha, .mailmap as of sha."""
    out = git(co, "-c", f"mailmap.blob={sha}:.mailmap", "log", f"--format={FMT}", sha)
    rows = []
    for rec in out.split("\x1e")[1:]:
        _, _, ae, mapped_name, mapped_email, body = rec.split("\x1f", 5)
        rows.append((ae.lower(), mapped_email.lower(), mapped_name, [(n, e.strip().lower()) for n, e in COAUTH.findall(body)]))
    return rows


def merge(records, ident):
    """records: (raw_email, mapped_email, mapped_name, coauthors). -> person rows, person_alias rows, stats."""
    generic_names = {norm_name(n) for n in ident["generic_names"]}
    bot = [re.compile(p, re.I) for p in ident["bot_patterns"]]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # deterministic root

    names = defaultdict(Counter)   # email -> mapped names seen on it
    mapped = {}                    # raw email -> mailmap email, when .mailmap moves it
    for raw, to, name, coauthors in records:
        names[raw][name] += 1
        if to and to != raw:
            mapped[raw] = to
        for n, e in coauthors:
            names[e][n] += 1
    for e, to in mapped.items():   # co-authors get the mailmap their email has as an author
        union("E:" + e, "E:" + to)

    by_local = defaultdict(set)    # local part -> names seen on it
    for e, ns in names.items():
        by_local[local_part(e)].add(norm_name(ns.most_common(1)[0][0]))
    shared = {l for l, ns in by_local.items() if len(ns) > ident["local_part_max_names"]}

    generic, bots, via_local = set(), set(), Counter()
    for e, ns in names.items():
        node = "E:" + e
        find(node)
        n = norm_name(ns.most_common(1)[0][0])
        if any(p.search(e) or p.search(name) for p in bot for name in ns):
            bots.add(e)
            continue
        if local_part(e) in generic_names or n in generic_names:
            generic.add(e)
            continue
        if n:
            union(node, "N:" + n)
        if len(local_part(e)) >= ident["local_part_min_length"] and local_part(e) not in shared:
            union(node, "L:" + local_part(e))
            via_local[local_part(e)] += 1
    joined = {find("L:" + l) for l, k in via_local.items() if k > 1}

    groups = defaultdict(list)
    for e in names:
        groups[find("E:" + e)].append(e)
    targets = set(mapped.values())
    person, alias, risky = [], [], set()
    for root, emails in groups.items():
        emails.sort()
        pid = h(emails[0])
        total = sum((names[e] for e in emails), Counter())
        person.append({"person_id": pid, "name": min(total, key=lambda n: (-total[n], n)),
                       "is_bot": int(any(e in bots for e in emails))})
        for e in emails:
            source = "mailmap" if e in mapped or e in targets else "name_email" if len(emails) > 1 else "single"
            alias.append({"email": e, "person_id": pid, "name": min(names[e], key=lambda n: (-names[e][n], n)),
                          "generic": int(e in generic), "source": source})
        if root in joined and len({norm_name(a["name"]) for a in alias[-len(emails):]}) > 1:
            risky.add(pid)
    return person, alias, {"generic": generic, "bots": bots, "shared": shared, "risky": risky}


def person_teams(commits, keys, components, pid, since, until, unassigned):
    """{person_id: team} for everyone who authored one of these commits ([teams.spark] rule = person_component).

    commits: git_commit rows of the dataset's repos. keys: sha -> ticket keys. components: ticket key -> components.
    Only non-merge commits authored in (since, until] count. A ticket with n components gives each 1/n, summed over
    the person's keyed commits. Tie -> the tied component on their most recent commit. Nothing -> unassigned.
    """
    score, history, people = defaultdict(Counter), defaultdict(list), set()
    for c in sorted(commits, key=lambda c: (c["authored_at"], c["seq"], c["sha"])):
        p = pid[c["author_email"]]
        people.add(p)
        if c["is_merge"] or not since < c["authored_at"] <= until:
            continue
        got = Counter()
        for k in keys.get(c["sha"], ()):
            cs = components.get(k, [])
            for x in cs:
                got[x] += Fraction(1, len(cs))  # exact: 1/3 + 1/3 + 1/3 ties with 1
        if got:
            score[p].update(got)
            history[p].append(set(got))
    team = {}
    for p in people:
        if not score[p]:
            team[p] = unassigned
            continue
        best = max(score[p].values())
        tied = {x for x, s in score[p].items() if s == best}
        for got in reversed(history[p]):  # newest first
            if tied & got:
                tied &= got
                break
        team[p] = min(tied)
    return team


def touches(commits, files, skip):
    """{path: [commit, ...] oldest first}, for one repo. A file's history follows its renames, so commits on an old
    name count for the name it has at the pinned SHA. Merges never count; skip (.git-blame-ignore-revs) only follows renames."""
    by_sha = defaultdict(list)
    for f in files:
        by_sha[f["sha"]].append(f)
    alias, out = {}, defaultdict(list)
    for c in sorted(commits, key=lambda c: (c["seq"], c["authored_at"], c["sha"]), reverse=True):  # newest first
        if c["is_merge"]:
            continue
        for f in by_sha.get(c["sha"], ()):
            cur = alias.get(f["path"], f["path"])
            if f["old_path"]:
                alias[f["old_path"]] = cur
            if c["sha"] not in skip:
                out[cur].append(c)
    for v in out.values():
        v.reverse()
    return out


def top_share(ts, key, can_win=lambda k: True):
    """(winner, share) of ts = [(authored_at, person_id)] oldest first, by key(person). Tie -> most recent."""
    n = Counter(key(p) for _, p in ts)
    best = max((v for k, v in n.items() if can_win(k)), default=0)
    if not best:
        return None, 0.0
    winner = next(key(p) for _, p in reversed(ts) if can_win(key(p)) and n[key(p)] == best)
    return winner, round(best / len(ts), 4)


def team_owner(ts, team_of, unassigned, min_share):
    """(owner_team, share): the team with the biggest share of ts. unassigned counts in the total but never owns."""
    team, share = top_share(ts, team_of, lambda t: t != unassigned)
    return (team if team and share >= min_share else "unowned"), share


def path_team(path, prefixes):
    """(prefix as matched, team) by the first [teams.django] prefix that matches. {app} / {pkg} match one folder."""
    for rule in prefixes:
        parts = re.split(r"\{(\w+)\}", rule["prefix"])
        rx = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>[^/]+)" for i, p in enumerate(parts))
        if m := re.match(rx, path):
            return m.group(0), rule["team"].format(**m.groupdict())
    raise ValueError(f"no [teams] prefix matches {path!r}: the last prefix should be ''")


def head_files(co, sha):
    """Every file (blob) at the pinned SHA."""
    entries = (e.split("\t", 1) for e in git(co, "ls-tree", "-r", "-z", sha).split("\0") if e)
    return sorted(path for meta, path in entries if meta.split()[1] == "blob")


def ignore_revs(co, sha, name):
    try:
        text = git(co, "show", f"{sha}:{name}")
    except subprocess.CalledProcessError:  # the repo has none
        return set()
    return {l.split()[0] for l in text.splitlines() if l.strip() and not l.startswith("#")}


def last_toucher_test(hist, team_of, unassigned, min_share, since, cut, until, src, test):
    """Week 1's test, at the cut: does the owner predict who touches the file next, better than whoever touched it last?

    hist: {path: [(authored_at, person_id)] oldest first}. Owners come from (since, cut], the next author from (cut, until].
    Scored on source files (src and not test, the [checks] regexes) with a commit before and after the cut. Team level:
    owner team vs the last toucher's team, against the next author's team (a next author with no team counts as a miss
    for both). Person level: the top person, if their share passes min_share, vs the last toucher.
    """
    team = {"n": 0, "owner": 0, "last": 0}
    person = {"n": 0, "owner": 0, "last": 0}
    for path, ts in hist.items():
        if not src.search(path) or test.search(path):
            continue
        before = [x for x in ts if x[0] <= cut]
        after = [x for x in ts if cut < x[0] <= until]
        if not before or not after:
            continue
        nxt, last = after[0][1], before[-1][1]
        window = [x for x in before if since < x[0]]
        owner_team, _ = team_owner(window, team_of, unassigned, min_share)
        if owner_team != "unowned":
            team["n"] += 1
            team["owner"] += owner_team == team_of(nxt)
            team["last"] += team_of(last) == team_of(nxt) != unassigned
        top, share = top_share(window, lambda p: p)
        if top and share >= min_share:
            person["n"] += 1
            person["owner"] += top == nxt
            person["last"] += last == nxt
    return team, person


def main(run_id, config=CONFIG, root=None, top=0):
    cfg, root = load_config(config), Path(root or data_root())
    snap = root / "snapshots" / run_id
    if not (snap / "manifest.json").exists():
        raise SystemExit(f"no snapshot {snap}: run meridian.collect first")
    manifest = json.loads((snap / "manifest.json").read_text())
    out = handoff(cfg, run_id, root)
    if not (out / "git_commit.jsonl").exists():
        raise SystemExit(f"no {out / 'git_commit.jsonl'}: run meridian.history first")
    commits = read_jsonl(out / "git_commit.jsonl")
    authors = defaultdict(set)
    for c in commits:
        authors[c["repo"]].add(c["author_email"])

    records, by_repo = [], {}
    for repo, sha in sorted(manifest["repos"].items()):
        rows = read_people(snap / "repos" / repo.replace("/", "__"), sha)
        if (seen := {r[0] for r in rows}) != authors[repo]:
            raise SystemExit(f"{repo}: author emails differ from git_commit ({len(seen ^ authors[repo])} emails)")
        records += rows
        by_repo[repo] = seen

    person, alias, stats = merge(records, cfg["identity"])
    pid = {a["email"]: a["person_id"] for a in alias}
    for repo, emails in by_repo.items():
        log(f"{repo:45} {len(emails):>6,} author emails -> {len({pid[e] for e in emails}):>6,} people")
    log(f"{'all repos, with co-authors':45} {len(alias):>6,} emails        -> {len(person):>6,} people · "
        f"{len(stats['generic'])} generic and {len(stats['bots'])} bot emails, never merged on name or local part · "
        f"{sum(a['source'] == 'mailmap' for a in alias)} emails joined by .mailmap · "
        f"{len(stats['shared'])} local parts shared by 3+ names, never merged on")

    # D4: team_map for datasets whose team is the person's Jira component (Spark)
    snapshot_at = read_jsonl(out / "run.jsonl")[0]["snapshot_at"]
    fmt, days = cfg["run"]["time_format"], timedelta(days=cfg["ownership"]["window_days"])
    back = lambda t: (datetime.strptime(t, fmt).replace(tzinfo=timezone.utc) - days).strftime(fmt)
    since = back(snapshot_at)
    keys = defaultdict(list)
    for r in read_jsonl(out / "commit_ticket.jsonl"):
        keys[r["sha"]].append(r["key"])
    components = {t["key"]: json.loads(t["components"]) for t in read_jsonl(out / "ticket.jsonl")}
    repo_ds = {r["repo"]: ds for ds, d in cfg["datasets"].items() for r in d.get("repos", [])}
    by_repo_commits = defaultdict(list)
    for c in commits:
        by_repo_commits[c["repo"]].append(c)
    ds_commits = lambda ds: [c for repo, cs in by_repo_commits.items() if repo_ds.get(repo) == ds for c in cs]
    team_map, teams = [], {}
    for ds, rule in sorted(cfg["teams"].items()):
        if rule["rule"] != "person_component":
            continue
        team = teams[ds] = person_teams(ds_commits(ds), keys, components, pid, since, snapshot_at, rule["unassigned"])
        team_map += [{"dataset": ds, "subject_kind": "person", "subject": p, "team": t, "rule": rule["rule"]} for p, t in team.items()]
        n = Counter(team.values())
        active = len(team) - n[rule["unassigned"]]
        log(f"{ds:45} {len(team):>6,} people -> {active:,} with a team ({len(n) - (rule['unassigned'] in n)} teams), "
            f"{n[rule['unassigned']]:,} {rule['unassigned']} (no keyed commit with a component since {since[:10]})")
        log(f"{'':45} biggest teams: " + ", ".join(f"{t} {k}" for t, k in n.most_common(7) if t != rule["unassigned"]))

    # D5: file_owner for every file at the pinned SHA, plus team_map rows for the path and repo rules
    own = cfg["ownership"]
    src, test = (re.compile(cfg["checks"][k]) for k in ("source_files", "test_files"))
    bots = {p["person_id"] for p in person if p["is_bot"]}
    pinned_at = {(c["repo"], c["sha"]): c["authored_at"] for c in commits}
    files = defaultdict(list)
    for f in read_jsonl(out / "commit_file.jsonl"):
        files[f["repo"]].append(f)
    file_owner, rule_teams = [], {}
    for repo, sha in sorted(manifest["repos"].items()):
        if (ds := repo_ds.get(repo)) is None:
            raise SystemExit(f"{repo}: in the manifest but in no [datasets] of {config}")
        mode, rule = cfg["datasets"][ds].get("owner_mode"), cfg["teams"].get(ds, {})
        if mode not in ("author", "path", "repo"):
            raise SystemExit(f"{repo}: owner_mode {mode!r} in [datasets.{ds}] has no file owner rule")
        until = snapshot_at if mode == "author" else pinned_at[repo, sha]
        start = back(until)
        co = snap / "repos" / repo.replace("/", "__")
        hist = {path: [(c["authored_at"], pid[c["author_email"]]) for c in cs if pid[c["author_email"]] not in bots]
                for path, cs in touches(by_repo_commits[repo], files[repo], ignore_revs(co, sha, own["ignore_revs_file"])).items()}
        unassigned = rule.get("unassigned", "unassigned")
        team_of = lambda p: teams[ds].get(p, unassigned)
        n = Counter()
        for path in head_files(co, sha):
            ts = hist.get(path, [])
            window = [x for x in ts if start < x[0] <= until]
            top_person, _ = top_share(window, lambda p: p)
            if mode == "author":
                owner, share = team_owner(window, team_of, unassigned, own["min_share"])
                basis = "author" if window else "none"
            elif mode == "path":
                subject, owner = path_team(path, rule["prefixes"])
                share, basis = 1.0, "path"
                rule_teams[(ds, "path_prefix", subject)] = (owner, rule["rule"])
            else:
                owner, share, basis = repo, 1.0, "repo"
                rule_teams[(ds, "repo", repo)] = (owner, rule["rule"])
            n["unowned" if owner == "unowned" else "owned"] += 1
            n["active"] += bool(window)
            file_owner.append({"repo": repo, "path": path, "owner_team": owner, "share": share, "top_person_id": top_person,
                               "n_commits": len(window), "last_commit_at": max((x[0] for x in ts), default=None), "basis": basis})
        log(f"{repo:45} {n['owned'] + n['unowned']:>6,} files · {n['active']:>6,} touched {start[:10]}..{until[:10]} · "
            f"{n['unowned']:,} unowned · basis {basis}")
        if mode == "author":  # week 1's test: owner vs last toucher, one window back
            cut = since
            team_cut = person_teams(ds_commits(ds), keys, components, pid, back(cut), cut, unassigned)
            t, p = last_toucher_test(hist, lambda q: team_cut.get(q, unassigned), unassigned, own["min_share"], back(cut), cut,
                                     snapshot_at, src, test)
            pct = lambda d, k: f"{d[k] / d['n']:.1%}" if d["n"] else "n/a"
            log(f"{'':45} next author after {cut[:10]}, on owned source files: team owner {pct(t, 'owner')} vs "
                f"last toucher's team {pct(t, 'last')} (N={t['n']:,}) · person owner {pct(p, 'owner')} vs last toucher {pct(p, 'last')} (N={p['n']:,})")
    team_map += [{"dataset": ds, "subject_kind": kind, "subject": s, "team": t, "rule": r} for (ds, kind, s), (t, r) in rule_teams.items()]

    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "person.jsonl", [{"run_id": run_id, **r} for r in person], ("person_id",))
    write_jsonl(out / "person_alias.jsonl", [{"run_id": run_id, **r} for r in alias], ("email",))
    write_jsonl(out / "team_map.jsonl", [{"run_id": run_id, **r} for r in team_map], ("dataset", "subject_kind", "subject"))
    write_jsonl(out / "file_owner.jsonl", [{"run_id": run_id, **r} for r in file_owner], ("repo", "path"))

    if top:  # to hand-check, never in the Airflow log: these are real names
        members = defaultdict(list)
        for a in alias:
            members[a["person_id"]].append(a)
        name = {p["person_id"]: p["name"] for p in person}
        biggest = [p for p, _ in sorted(members.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:top]]
        for title, ps in ((f"the {top} biggest groups", biggest),
                          ("groups joined on a shared local part, with different names", sorted(stats["risky"]))):
            log(f"\n=== {title}: {len(ps)}")
            for p in ps:
                log(f"\n{name[p]} · {p} · {len(members[p])} emails")
                for a in members[p]:
                    log(f"    {a['email']:50} {a['name']:30} {a['source']}")
    return person, alias, team_map, file_owner


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--top", type=int, default=0, help="print the N biggest groups to hand-check (real names: not for shared logs)")
    a = ap.parse_args()
    main(a.run_id, a.config, top=a.top)
