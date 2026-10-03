"""Step 1 numbers for OpenStack's Ocata goal 'Remove Copies of Incubated Oslo Code'.

Reads Gerrit (review.opendev.org, public REST, read-only) for topic
goal-remove-incubated-oslo-code. Splits code changes from governance paperwork, then
measures time to merge and evidence of waiting (Depends-On, requirements edits, comments).
"""
import csv
import json
import re
import statistics
from collections import Counter
from datetime import datetime

import requests

GERRIT = "https://review.opendev.org"
TOPIC = "goal-remove-incubated-oslo-code"
PAPERWORK = {"openstack/governance"}

WAIT = re.compile(r"\bwait(ing)? (for|on|until)\b|\bonce .{0,40}(released|merged|lands|is in)"
                  r"|\bblocked (by|on)\b|\bdepends on\b|\bneeds? .{0,30}release\b"
                  r"|\bafter .{0,30}(release|is released)\b", re.I)
DEPENDS_ON = re.compile(r"^Depends-On:\s*(\S+)", re.I | re.M)
OSLO_LIB = re.compile(r"^\+\s*(oslo[.\-][a-z0-9]+)", re.I | re.M)


def get(path):
    r = requests.get(GERRIT + path, timeout=60)
    r.raise_for_status()
    return json.loads(r.text[4:])  # strip Gerrit's )]}' prefix


def ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S") if s else None


changes = get(f"/changes/?q=topic:{TOPIC}&n=500"
              "&o=CURRENT_REVISION&o=CURRENT_COMMIT&o=CURRENT_FILES&o=MESSAGES&o=DETAILED_ACCOUNTS")
print(f"changes in topic: {len(changes)}")

rows = []
for c in changes:
    rev = c["revisions"][c["current_revision"]]
    msg = rev["commit"]["message"]
    files = list(rev.get("files", {}))
    req_files = [f for f in files if f.endswith("requirements.txt")]
    oslo_added = []
    if req_files and c["project"] not in PAPERWORK:
        for f in req_files:
            diff = get(f"/changes/{c['_number']}/revisions/current/files/"
                       f"{requests.utils.quote(f, safe='')}/diff")
            added = "\n".join("+" + l for blk in diff["content"] for l in blk.get("b", []))
            oslo_added += OSLO_LIB.findall(added)
    humans = [m for m in c.get("messages", [])
              if "zuul" not in (m.get("author", {}).get("name", "") or "").lower()
              and "jenkins" not in (m.get("author", {}).get("name", "") or "").lower()]
    hits = [h.group(0) for m in humans for h in WAIT.finditer(m["message"])]
    hits += [h.group(0) for h in WAIT.finditer(msg)]
    created, merged = ts(c["created"]), ts(c.get("submitted"))
    rows.append(dict(
        number=c["_number"], project=c["project"], owner=c["owner"].get("name", ""),
        status=c["status"],
        kind="paperwork" if c["project"] in PAPERWORK else "code",
        created=c["created"][:10], merged=(c.get("submitted") or "")[:10],
        days_open=(merged - created).days if merged else "",
        patchsets=rev["_number"], depends_on="; ".join(DEPENDS_ON.findall(msg)),
        oslo_libs_added=", ".join(sorted(set(oslo_added))),
        wait_hits=len(hits), wait_examples="; ".join(hits[:3]),
        subject=c["subject"], url=f"{GERRIT}/c/{c['_number']}"))

code = [r for r in rows if r["kind"] == "code"]
code_merged = [r for r in code if r["status"] == "MERGED"]
print(f"  governance paperwork: {len(rows) - len(code)}")
print(f"  code changes: {len(code)} ({len(code_merged)} merged, "
      f"{sum(r['status'] == 'ABANDONED' for r in code)} abandoned)")
print(f"  repos with code changes: {len({r['project'] for r in code})}")
gov = [r for r in rows if r["kind"] == "paperwork"]
teams = {re.sub(r"(?i).*(for|from)\s+", "", r["subject"]).strip() for r in gov}
print(f"  paperwork changes naming a team: {len(gov)} (distinct subjects ~{len(teams)})")

days = sorted(r["days_open"] for r in code_merged)
print(f"code: days created -> merged: median {statistics.median(days)}, "
      f"p90 {days[int(len(days) * .9)]}, max {days[-1]}; same-day merges {sum(d == 0 for d in days)}")
dates = sorted(r["created"] for r in code)
print(f"code changes created {dates[0]} .. {dates[-1]}")
print(f"code changes with Depends-On: {sum(bool(r['depends_on']) for r in code)}")
print(f"code changes adding an oslo lib to requirements: {sum(bool(r['oslo_libs_added']) for r in code)}")
print(f"code changes with waiting language: {sum(r['wait_hits'] > 0 for r in code)}")

# Order check: was each added library already released before the consumer change?
libs = {l for r in code for l in r["oslo_libs_added"].split(", ") if l}
for lib in sorted(libs):
    rel = requests.get(f"https://pypi.org/pypi/{lib}/json", timeout=60).json()["releases"]
    first = min(f["upload_time"][:10] for files in rel.values() for f in files)
    used = min(r["created"] for r in code if lib in r["oslo_libs_added"].split(", "))
    print(f"  {lib}: first PyPI release {first}, first consumer change here {used}")
by_owner = Counter(r["owner"] for r in code)
top3 = by_owner.most_common(3)
print(f"code change authors: {len(by_owner)}; top 3 wrote {sum(n for _, n in top3)} of {len(code)}")
for name, n in top3:
    print(f"  {name}: {n} changes in {len({r['project'] for r in code if r['owner'] == name})} repos")
print("slowest code changes:")
for r in sorted(code_merged, key=lambda r: -r["days_open"])[:8]:
    print(f"  {r['days_open']:>4}d  {r['project']:<40} {r['subject'][:60]}")
print("waiting-language hits:")
for r in code:
    if r["wait_hits"] or r["depends_on"]:
        print(f"  {r['url']}  {r['project']}  dep={r['depends_on'] or '-'}  | {r['wait_examples'][:100]}")

with open("openstack_changes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: (r["kind"], r["project"], r["number"])))
