"""Tickets · step 1 · D4: ticket, ticket_event, ticket_link and commit_ticket from a snapshot. Run by meridian.history.

Everything is as of snapshot_at, for both trackers:
- Tickets created after it are not written, and neither are events after it.
- Jira status, resolution and resolved_at are rolled back to their value at snapshot_at: the first change after it
  holds that value on its "from" side. Other fields (type, components, labels, summary, assignee) are as of the pull.
- GitLab: closed at snapshot_at only if closedAt <= snapshot_at. The pull has no close/reopen or label history,
  so an issue reopened after snapshot_at counts as open, and labels (group::) are as of the pull.
- Bulk-closed = labelled bulk-closed, or the last resolution was one of 20+ by one person in one hour (Jira only:
  GitLab has no record of who closed). Flagged, never dropped.
- Links: one row per (waiting ticket, type, other). Added after snapshot_at -> left out. Removed before it -> kept,
  with removed_at. Added and removed times come from the Jira changelog and GitLab activity notes; a link with no
  event at all is as of the pull, added_at NULL.
- commit_ticket: keys from the subject; from the body only if the subject has none (the body also holds branch
  names like SPARK-123-fix). A key that isn't a ticket in the snapshot gives no row, and is counted as dead.
Same as week1/pair2/merged/build.py ticket_rows() and its commit_ticket, plus links over time and GitLab.
"""
import gzip
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from meridian.common import log


def ts(s):
    """Jira '2026-10-01T03:12:22.000+0000' and GitLab '2026-10-01T03:12:22Z' -> '2026-10-01T03:12:22Z'."""
    if s.endswith("+0000") or s.endswith("Z"):
        return s[:19] + "Z"
    raise ValueError(f"not UTC: {s!r}")


def jlist(xs):
    return json.dumps(sorted(xs), ensure_ascii=False, separators=(",", ":"))


def epoch(t):
    return int(datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


# ───────────── links over time ─────────────

# What a link is called from the side of the ticket the event is on.
REL = {("blocked_by", 0): "blocked_by", ("blocked_by", 1): "blocks", ("duplicates", 0): "duplicates",
       ("duplicates", 1): "duplicated_by", ("parent", 0): "parent", ("parent", 1): "child",
       ("relates", 0): "relates", ("relates", 1): "relates", ("other", 0): "other", ("other", 1): "other"}


class Links:
    """Add / remove events per (key, link_type, other_key), plus the links the pull says exist now."""

    def __init__(self):
        self.events = defaultdict(set)  # row -> {(at, +1 add / -1 remove)}
        self.now = set()

    def add(self, row, at, action):
        self.events[row].add((at, action))

    def rows(self, run_id, snapshot_at):
        out = []
        for row in sorted(set(self.events) | self.now):
            ev = sorted(self.events.get(row, ()))
            before = [e for e in ev if e[0] <= snapshot_at]
            after = [e for e in ev if e[0] > snapshot_at]
            if before:
                adds = [at for at, a in before if a > 0]
                added, removed = (adds[-1] if adds else None), (before[-1][0] if before[-1][1] < 0 else None)
            elif after:
                if after[0][1] > 0:  # first seen being added after the snapshot: not there at snapshot_at
                    continue
                added, removed = None, None
            else:
                added, removed = None, None  # in the pull, no event: as of the pull
            out.append({"run_id": run_id, "key": row[0], "link_type": row[1], "other_key": row[2],
                        "added_at": added, "removed_at": removed})
        return out


# ───────────── Jira ─────────────

BLOCKS, DUPS, RELATES, CHILD = {"Blocker"}, {"Duplicate"}, {"Reference", "Related"}, {"Child-Issue"}


def jira_link(a, kind, side, b):
    """(row, a_is_other): a Jira link from a's side, as one row keyed by the waiting ticket.
    side 'in' = a <inward> b ('is blocked by'), 'out' = a <outward> b ('blocks'), None = no direction."""
    if kind in BLOCKS and side:
        return ((a, "blocked_by", b), False) if side == "in" else ((b, "blocked_by", a), True)
    if kind in DUPS and side:
        return ((a, "duplicates", b), False) if side == "out" else ((b, "duplicates", a), True)
    if kind in CHILD and side:
        return ((a, "parent", b), False) if side == "in" else ((b, "parent", a), True)
    return ((a, "relates" if kind in RELATES else "other", b), False)


def read_jira(d):
    """The base pull's pages, then each delta-*/ in the order pulled (collect.py): a later pull's copy of a ticket
    replaces the earlier one in place, and a ticket new in a delta comes after the rest. A ticket missing from the
    newest delta's keys.json.gz (every key that still exists) was deleted, and is left out."""
    issues, live = {}, None
    for pull in [d, *sorted(d.glob("delta-*"))]:
        seen = set()
        for p in sorted(pull.glob("page-*.json.gz")):
            for it in json.loads(gzip.decompress(p.read_bytes()))["issues"]:
                if it["key"] not in seen:  # a ticket created mid-pull can shift paging and appear twice
                    seen.add(it["key"])
                    issues[it["key"]] = it
        if (pull / "keys.json.gz").exists():
            live = set(json.loads(gzip.decompress((pull / "keys.json.gz").read_bytes())))
    return [it for k, it in issues.items() if live is None or k in live]


def jira(issues, snapshot_at, rules, run_id, project):
    issues = [it for it in issues if ts(it["fields"]["created"]) <= snapshot_at]
    by_key = {it["key"]: it for it in issues}
    phrase = {}  # 'is blocked by' -> ('Blocker', 'in'). A type whose two names are the same has no direction
    for it in issues:
        for l in it["fields"].get("issuelinks", []):
            t = l["type"]
            same = t["inward"] == t["outward"]
            phrase.setdefault(t["inward"], (t["name"], None if same else "in"))
            phrase.setdefault(t["outward"], (t["name"], None if same else "out"))
    bulk = rules["bulk_close"]
    links, events, cut = Links(), [], 0
    at_snapshot, last_res, last_resolved = {}, {}, {}

    def link_item(key, s):
        """'This issue is blocked by SPARK-1' -> (row, key_is_other)."""
        body, _, other = s.rpartition(" ")
        kind, side = phrase.get(body.removeprefix("This issue "), ("?", None))
        return jira_link(key, kind, side, other)

    for it in issues:
        key, cl = it["key"], it.get("changelog") or {}
        cut += cl.get("total", 0) > len(cl.get("histories", []))  # Jira cut the changelog short: rollback may be wrong
        for h in sorted(cl.get("histories", []), key=lambda h: (h["created"], int(h["id"]))):
            at, who = ts(h["created"]), (h.get("author") or {}).get("name")
            for i in h["items"]:
                f = i["field"]
                if f == "Link":
                    for s, action in ((i.get("toString"), 1), (i.get("fromString"), -1)):
                        if s:
                            row, flip = link_item(key, s)
                            links.add(row, at, action)
                            if at <= snapshot_at:
                                v = f"{REL[(row[1], int(flip))]} {row[0] if flip else row[2]}"
                                events.append((key, at, "link", None if action > 0 else v,
                                               v if action > 0 else None, who))
                    continue
                if f not in ("status", "resolution", "labels"):
                    continue
                if at > snapshot_at:
                    if f != "labels":  # the first change after the snapshot: its "from" is the value at the snapshot
                        at_snapshot.setdefault((key, f), i.get("fromString"))
                    continue
                if f == "labels":
                    old, new = set((i.get("fromString") or "").split()), set((i.get("toString") or "").split())
                    events += [(key, at, "label", l, None, who) for l in sorted(old - new)]
                    events += [(key, at, "label", None, l, who) for l in sorted(new - old)]
                    continue
                events.append((key, at, f, i.get("fromString"), i.get("toString"), who))
                if f == "resolution" and i.get("toString"):
                    last_res[key] = (who, epoch(at))
                    last_resolved[key] = at
        f = it["fields"]
        for l in f.get("issuelinks", []):
            for field, side in (("outwardIssue", "out"), ("inwardIssue", "in")):
                if field in l:
                    t = l["type"]
                    links.now.add(jira_link(key, t["name"], None if t["inward"] == t["outward"] else side,
                                            l[field]["key"])[0])
        if (f.get("parent") or {}).get("key"):
            links.now.add((key, "parent", f["parent"]["key"]))

    batch = bulk_closed(last_res, bulk["per_person"], bulk["within_minutes"] * 60)
    tickets = []
    for it in issues:
        key, f = it["key"], it["fields"]
        par = (f.get("parent") or {}).get("key")
        ptype = by_key[par]["fields"]["issuetype"]["name"] if par in by_key else None
        labels = set(f.get("labels") or [])
        res = at_snapshot.get((key, "resolution"), (f.get("resolution") or {}).get("name"))
        resolved = f.get("resolutiondate") and ts(f["resolutiondate"])
        if res is None:
            resolved = None
        elif not resolved or resolved > snapshot_at:  # re-resolved, or reopened (date cleared), after the snapshot
            resolved = last_resolved.get(key)
        typ = f["issuetype"]["name"]
        tickets.append({
            "run_id": run_id, "key": key, "tracker": "jira", "type": typ,
            "effective_type": (ptype if f["issuetype"]["subtask"] and ptype and rules["subtask_takes_parent_type"]
                               else typ),
            "parent_key": par, "status": at_snapshot.get((key, "status"), f["status"]["name"]), "resolution": res,
            "created_at": ts(f["created"]), "resolved_at": resolved,
            "components": jlist(c["name"] for c in f.get("components") or []),
            "assignee": (f.get("assignee") or {}).get("name"), "reporter": (f.get("reporter") or {}).get("name"),
            "bulk_closed": int(bulk["label"] in labels or key in batch),
            "desc_empty": int(not (f.get("description") or "").strip()), "summary": f.get("summary") or "",
            "description": f.get("description") or ""})

    # A link to a ticket of this project that isn't in the snapshot (created later) is left out, and counted.
    # Other projects stay.
    keep = lambda r: all(k in by_key or not k.startswith(project + "-") for k in (r["key"], r["other_key"]))
    rows = links.rows(run_id, snapshot_at)
    kept = [r for r in rows if keep(r)]
    return tickets, numbered(events, run_id), kept, {"links_to_missing": len(rows) - len(kept), "changelog_cut": cut}


def bulk_closed(last_res, per_person, window):
    """Keys whose last resolution is one of per_person+ by one person within window seconds (sliding, not clock hours).
    last_res: {key: (who, epoch)}. who None (anonymous) is no one: never pooled."""
    by = defaultdict(list)
    for key, (who, t) in last_res.items():
        if who is not None:
            by[who].append((t, key))
    out = set()
    for xs in by.values():
        xs.sort()
        lo = done = 0
        for hi in range(len(xs)):
            while xs[hi][0] - xs[lo][0] >= window:
                lo += 1
            if hi - lo + 1 >= per_person:
                out.update(k for _, k in xs[max(lo, done):hi + 1])
                done = hi + 1
    return out


def numbered(events, run_id):
    """ticket_event rows, seq = order within (key, at), in the order the tracker recorded them."""
    seq, out = Counter(), []
    for key, at, field, frm, to, who in events:
        out.append({"run_id": run_id, "key": key, "at": at, "field": field, "from_value": frm, "to_value": to,
                    "author": who, "seq": seq[(key, at)]})
        seq[(key, at)] += 1
    return out


# ───────────── GitLab ─────────────

REF = re.compile(r"[\w./-]*[#&]\d+")
# (activity note, +1 add / -1 remove / 0 'removed the relation', link_type, 1 if this issue is the other side)
GL_NOTES = [(re.compile(p), a, t, o) for p, a, t, o in [
    (r"marked this \w+ as blocked by (.+)", 1, "blocked_by", 0),
    (r"marked this \w+ as blocking (.+)", 1, "blocked_by", 1),
    (r"marked (?:this \w+ )?as related to (.+)", 1, "relates", 0),
    (r"marked this \w+ as a duplicate of (.+)", 1, "duplicates", 0),
    (r"marked (.+) as a duplicate of this \w+", 1, "duplicates", 1),
    (r"added (.+) as parent (?:issue|item|epic)", 1, "parent", 0),
    (r"added to epic (.+)", 1, "parent", 0),
    (r"changed epic to (.+)", 1, "parent", 0),
    (r"removed parent (?:issue|item|epic) (.+)", -1, "parent", 0),
    (r"removed from epic (.+)", -1, "parent", 0),
    (r"added (.+) as child (?:task|item|issue)", 1, "parent", 1),
    (r"removed child (?:task|item|issue) (.+)", -1, "parent", 1),
    (r"removed the relation with (.+)", 0, None, 0),
]]
STATUS = re.compile(r"set status to \*\*(.+)\*\*")


def gl_ref(ref, project):
    """'#12' -> 'gitlab-org/gitlab#12', '&5' -> 'gitlab-org&5' (an epic, in the project's group),
    'gitaly#5' -> 'gitlab-org/gitaly#5' (same namespace, as blockedByIssues writes it), others as written."""
    path, sigil, n = re.fullmatch(r"([\w./-]*)([#&])(\d+)", ref).groups()
    if sigil == "#" and path and "/" not in path:
        path = project.rsplit("/", 1)[0] + "/" + path
    return f"{path or (project if sigil == '#' else project.split('/')[0])}{sigil}{n}"


def gl_parent(n):
    """The work-item parent, unless it's an Epic: a group epic, not an issue of the project (the epic field links
    it)."""
    p = n.get("parent")
    return p if p and p["workItemType"]["name"] != "Epic" else None


def read_gitlab(d):
    """issues.jsonl.gz, then each delta-*/ in the order pulled, as read_jira (the newest iids.json.gz for deletions).
    shortcut: a link from a kept issue to a deleted one stays until the kept issue changes; filter links if that
    shows."""
    issues, live = {}, None
    for pull in [d, *sorted(p.parent for p in d.glob("delta-*/issues.jsonl.gz"))]:  # a killed delta has none yet
        with gzip.open(pull / "issues.jsonl.gz", "rt", encoding="utf-8") as f:
            for n in (json.loads(l) for l in f if l.strip()):
                issues[n["iid"]] = n
        if (pull / "iids.json.gz").exists():
            live = set(json.loads(gzip.decompress((pull / "iids.json.gz").read_bytes())))
    return [n for i, n in issues.items() if live is None or i in live]


def gitlab(issues, snapshot_at, ds, rules, prefix, run_id):
    project, fmt = ds["project"], ds["key_format"]
    skip = tuple(ds.get("drop_titles_starting", []))
    kept = [n for n in issues if ts(n["createdAt"]) <= snapshot_at and not n["title"].startswith(skip)]
    dropped = len(issues) - len(kept)
    by_iid = {n["iid"]: n for n in kept}
    key = lambda iid: fmt.format(iid=iid)
    links, events, unmatched = Links(), [], 0
    relation_removed = []  # (at, a, b): the note doesn't say which link, so it's matched below

    for n in kept:
        a = key(n["iid"])
        for b in n["blockedByIssues"]["nodes"]:
            path = b["webUrl"].split("gitlab.com/", 1)[1].split("/-/", 1)[0]
            links.now.add((a, "blocked_by", f"{path}#{b['iid']}"))
        if gl_parent(n):
            links.now.add((a, "parent", key(n["parent"]["iid"])))
        if n.get("epic"):
            links.now.add((a, "parent", f"{n['epic']['group']['fullPath']}&{n['epic']['iid']}"))
        status = None
        for note in sorted(n.get("notes") or [], key=lambda x: x["createdAt"]):
            at, who, body = ts(note["createdAt"]), (note.get("author") or {}).get("username"), note["body"]
            if m := STATUS.fullmatch(body):
                if at <= snapshot_at:
                    events.append((a, at, "status", status, m.group(1), who))
                    status = m.group(1)
                continue
            for rx, action, kind, flip in GL_NOTES:
                if not (m := rx.fullmatch(body)):
                    continue
                if body.startswith("changed epic"):  # the old epic isn't named: close whichever epic link is open
                    for r in [r for r in links.events if r[0] == a and r[1] == "parent" and "&" in r[2]
                              and (ev := sorted(e for e in links.events[r] if e[0] < at)) and ev[-1][1] > 0]:
                        links.add(r, at, -1)
                for b in (gl_ref(r, project) for r in REF.findall(m.group(1))):
                    if action == 0:
                        relation_removed.append((at, a, b, who))
                        continue
                    row = (b, kind, a) if flip else (a, kind, b)
                    links.add(row, at, action)
                    if at <= snapshot_at:
                        v = f"{REL[(kind, flip)]} {b}"
                        events.append((a, at, "link", None if action > 0 else v, v if action > 0 else None, who))
                break

    for at, a, b, who in sorted(relation_removed):  # whichever issue link between the two was open just before
        pairs = [(x, k, y) for x, y in ((a, b), (b, a)) for k in ("blocked_by", "relates", "duplicates")]
        open_ = [r for r in pairs if (ev := sorted(e for e in links.events.get(r, ()) if e[0] < at)) and ev[-1][1] > 0]
        if not open_:
            unmatched += 1
        for r in open_:
            links.add(r, at, -1)
        if at <= snapshot_at:
            for r in open_ or [(a, "other", b)]:
                flip = r[0] != a
                v = f"{REL[(r[1], int(flip))]} {b}"
                events.append((a, at, "link", v, None, who))

    tickets = []
    for n in kept:
        closed = n["state"] == "closed" and n.get("closedAt") and ts(n["closedAt"]) <= snapshot_at
        par = gl_parent(n)
        ptype = ((by_iid[par["iid"]]["type"] if par["iid"] in by_iid
                  else par["workItemType"]["name"].upper().replace(" ", "_")) if par else None)
        labels = {l["title"] for l in n["labels"]["nodes"]}
        assignees = sorted(x["username"] for x in n["assignees"]["nodes"])
        tickets.append({
            "run_id": run_id, "key": key(n["iid"]), "tracker": "gitlab", "type": n["type"],
            "effective_type": (ptype if n["type"] == "TASK" and ptype and rules["subtask_takes_parent_type"]
                               else n["type"]),
            "parent_key": key(par["iid"]) if par else None, "status": "closed" if closed else "opened",
            "resolution": None, "created_at": ts(n["createdAt"]), "resolved_at": ts(n["closedAt"]) if closed else None,
            "components": jlist(l for l in labels if l.startswith(prefix)),
            "assignee": assignees[0] if assignees else None, "reporter": (n.get("author") or {}).get("username"),
            "bulk_closed": int(rules["bulk_close"]["label"] in labels),
            "desc_empty": int(not (n.get("description") or "").strip()), "summary": n["title"],
            "description": n.get("description") or ""})
    events.sort(key=lambda e: (e[0], e[1]))  # stable: keeps note order within the same second
    return (tickets, numbered(events, run_id), links.rows(run_id, snapshot_at),
            {"dropped_titles": dropped, "relation_removed_unmatched": unmatched})


# ───────────── commit_ticket ─────────────

def commit_tickets(messages, repo, key_rx, known, run_id):
    """messages: [(sha, subject, body)]. Returns rows and the number of dead keys."""
    rows, dead = [], 0
    for sha, subject, body in messages:
        keys, src = sorted(set(key_rx.findall(subject))), "subject"
        if not keys:
            keys, src = sorted(set(key_rx.findall(body))), "body"
        live = [k for k in keys if k in known]
        dead += len(keys) - len(live)
        rows += [{"run_id": run_id, "repo": repo, "sha": sha, "key": k, "source": src} for k in live]
    return rows, dead


# ───────────── all of D4 ─────────────

def source_key(name, ds):
    """The source collect pulled for a dataset, as named in the manifest ('jira/SPARK', 'gitlab'), or None."""
    tracker = ds.get("tracker", "none")
    if tracker == "jira":
        m = re.search(r"project\s*=\s*(\w+)", ds["jira"]["jql"])
        return f"jira/{m.group(1) if m else name.upper()}"
    return "gitlab" if tracker == "gitlab" else None


def build(snap, manifest, cfg, run_id, messages_of):
    """{table: rows} for ticket, ticket_event, ticket_link, commit_ticket. messages_of(repo) -> [(sha, subject,
    body)]."""
    snapshot_at, rules = manifest["snapshot_at"], cfg["tickets"]
    prefix = cfg.get("teams", {}).get("gitlab", {}).get("prefix", "group::")
    out = {"ticket": [], "ticket_event": [], "ticket_link": [], "commit_ticket": []}
    for name, ds in sorted(cfg["datasets"].items()):
        src = source_key(name, ds)
        if not src:
            continue
        if src not in manifest["sources"]:
            log(f"{src:45} not in this snapshot (the pull failed or the dataset was left out): no tickets")
            continue
        if src.startswith("jira/"):
            project = src.split("/", 1)[1]
            t, e, l, counts = jira(read_jira(snap / src), snapshot_at, rules, run_id, project)
            known = {r["key"] for r in t}
            key_rx = re.compile(ds.get("key_regex") or rules["key_regex_generic"])
            for repo in sorted(r["repo"] for r in ds.get("repos", []) if r["repo"] in manifest["repos"]):
                rows, dead = commit_tickets(messages_of(repo), repo, key_rx, known, run_id)
                out["commit_ticket"] += rows
                log(f"{repo:45} {len({r['sha'] for r in rows}):>7,} commits keyed  {len(rows):>7,} keys  "
                    f"{dead:>3} dead keys")
            extra = f"  ({counts['links_to_missing']} links to {project} tickets not in the snapshot left out, " \
                    f"{counts['changelog_cut']} changelogs cut short by Jira)"
        else:
            t, e, l, counts = gitlab(read_gitlab(snap / src), snapshot_at, ds, rules, prefix, run_id)
            extra = f"  ({counts['dropped_titles']:,} {', '.join(ds.get('drop_titles_starting', []))} left out, " \
                    f"{counts['relation_removed_unmatched']} relation removals with no link to match)"
        out["ticket"] += t
        out["ticket_event"] += e
        out["ticket_link"] += l
        log(f"{src:45} {len(t):>7,} tickets  {len(e):>9,} events  {len(l):>7,} links  "
            f"{sum(r['bulk_closed'] for r in t):>5,} bulk-closed{extra}")
    return out
