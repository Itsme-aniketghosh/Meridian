"""Step 1 numbers for Mozilla bug 922464 ([meta] Centralize URI parsing and make it threadsafe).

Reads bugzilla.mozilla.org's public REST API (read-only). For the meta bug's 43 children
(depends_on) and the 7 bugs it blocks (downstream work that waited on it), measures time
to resolve, teams (product::component), and comments that say someone waited.
"""
import csv
import re
import statistics
from collections import Counter
from datetime import datetime

import requests

BZ = "https://bugzilla.mozilla.org/rest"
META = 922464
FIELDS = ("id,summary,product,component,status,resolution,creation_time,cf_last_resolved,"
          "assigned_to,depends_on,blocks,keywords")
WAIT = re.compile(r"\bwait(ing)? (for|on|until)\b|\bonce .{0,60}(lands?|landed|is fixed|is done|"
                  r"are fixed|is finished|is in)\b|\bblocked (by|on)\b|\bbefore we can\b"
                  r"|\bdepends on .{0,40}(first|landing)\b|\bafter .{0,40}lands\b", re.I)
STALL_DAYS = 90


session = requests.Session()
session.mount("https://", requests.adapters.HTTPAdapter(max_retries=requests.adapters.Retry(
    total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))))


def get(path, **params):
    r = session.get(f"{BZ}/{path}", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S") if s else None


meta = get(f"bug/{META}", include_fields=FIELDS)["bugs"][0]
team = lambda b: f"{b['product']}::{b['component']}"
print(f"meta: {meta['summary']}")
print(f"  team {team(meta)}, filed {meta['creation_time'][:10]}, resolved {meta['cf_last_resolved'][:10]}")
print(f"  depends on {len(meta['depends_on'])} bugs, blocks {len(meta['blocks'])} bugs")

ids = {i: "child" for i in meta["depends_on"]}
ids.update({i: "downstream" for i in meta["blocks"]})
bugs = get("bug", id=",".join(map(str, ids)), include_fields=FIELDS)["bugs"]
print(f"  fetched {len(bugs)} of {len(ids)} (private bugs are not returned)")

rows = []
for b in bugs:
    comments = get(f"bug/{b['id']}/comment")["bugs"][str(b["id"])]["comments"]
    texts = [c["text"] for c in comments
             if not c["creator"].endswith("@bots.tld") and "phabricator" not in c["text"][:80].lower()]
    texts = ["\n".join(l for l in t.splitlines() if not l.startswith(">")) for t in texts]
    hits = [m.group(0) for t in texts for m in WAIT.finditer(t)]
    history = get(f"bug/{b['id']}/history")["bugs"][0]["history"]
    reopened = sum(1 for h in history for ch in h["changes"]
                   if ch["field_name"] == "status" and ch["added"] == "REOPENED")
    created, resolved = ts(b["creation_time"]), ts(b.get("cf_last_resolved"))
    end = resolved if b["status"] in ("RESOLVED", "VERIFIED", "CLOSED") else None
    rows.append(dict(
        bug=b["id"], role=ids[b["id"]], team=team(b), status=b["status"],
        resolution=b["resolution"], created=b["creation_time"][:10],
        resolved=(end.date().isoformat() if end else ""),
        days_open=((end - created).days if end else ""),
        assignee=b["assigned_to"], reopened=reopened, comments=len(comments),
        own_depends_on=len(b["depends_on"]), wait_hits=len(hits),
        wait_examples=" | ".join(h[:80] for h in hits[:3]),
        summary=b["summary"], url=f"https://bugzilla.mozilla.org/show_bug.cgi?id={b['id']}"))

for role in ("child", "downstream"):
    rs = [r for r in rows if r["role"] == role]
    done = [r for r in rs if r["days_open"] != ""]
    days = sorted(r["days_open"] for r in done)
    print(f"\n{role}: {len(rs)} bugs")
    print(f"  status: {Counter(r['status'] + ('/' + r['resolution'] if r['resolution'] else '') for r in rs).most_common()}")
    if days:
        print(f"  days filed -> resolved: median {statistics.median(days)}, "
              f"p90 {days[int(len(days) * .9)]}, max {days[-1]}")
        print(f"  open {STALL_DAYS}+ days before resolving: {sum(d >= STALL_DAYS for d in days)} of {len(days)}")
    print(f"  still open: {len(rs) - len(done)}")
    teams = Counter(r["team"] for r in rs)
    print(f"  teams: {len(teams)}; outside meta's team ({team(meta)}): "
          f"{sum(n for t, n in teams.items() if t != team(meta))}")
    for t, n in teams.most_common():
        print(f"    {n:>3}  {t}")
    print(f"  distinct assignees: {len({r['assignee'] for r in rs})}; top: "
          f"{Counter(r['assignee'] for r in rs).most_common(2)}")
    print(f"  with waiting language: {sum(r['wait_hits'] > 0 for r in rs)} of {len(rs)}")
    print(f"  reopened at least once: {sum(r['reopened'] > 0 for r in rs)}")

print("\nwaiting-language hits:")
for r in sorted(rows, key=lambda r: (r["role"], -r["wait_hits"])):
    if r["wait_hits"]:
        print(f"  [{r['role']}] {r['bug']} {r['team']} ({r['days_open']}d): {r['wait_examples'][:160]}")

print("\nslowest children:")
for r in sorted([r for r in rows if r["role"] == "child" and r["days_open"] != ""],
                key=lambda r: -r["days_open"])[:8]:
    print(f"  {r['days_open']:>5}d  {r['bug']}  {r['team']:<35} {r['summary'][:60]}")

with open("mozilla_bugs.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: (r["role"], r["bug"])))
