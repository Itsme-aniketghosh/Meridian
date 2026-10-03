"""Are Debian py2removal block edges cross-team? And do sampled bugs show a real wait?

Team = the source package's Maintainer at its last upload before 2019-10-21 (when the
bulk block links were added). Debian Python Team packages share one team address.
"""
import csv
import mailbox
import random
import re
import tempfile
from collections import Counter

import psycopg2
import requests

CUTOFF = "2019-10-21"
SEED, N = 42, 20
PY_TEAM = re.compile(r"python-(modules|apps)-team|team\+python@|debian-python@", re.I)

conn = psycopg2.connect(host="udd-mirror.debian.net", dbname="udd",
                        user="udd-mirror", password="udd-mirror")
conn.set_client_encoding("UTF8")
cur = conn.cursor()

cur.execute("""
    with tagged as (select distinct id from bugs_usertags where tag = 'py2removal')
    select id, source from bugs join tagged using (id)
    union select id, source from archived_bugs join tagged using (id)
""")
src = dict(cur.fetchall())

cur.execute("""
    select distinct on (source) source, lower(maintainer_email)
    from upload_history where date < %s
    order by source, date desc
""", (CUTOFF,))
maint = dict(cur.fetchall())

edges = [tuple(map(int, l.split("\t"))) for l in open("debian_edges.tsv").read().splitlines()[1:]]
pkg_edges = sorted(e for e in edges if e[0] in src and e[1] in src)


def team(bug):
    m = maint.get(src[bug])
    if m is None:
        return None
    return "debian-python-team" if PY_TEAM.search(m) else m


kinds = Counter()
for a, b in pkg_edges:
    ta, tb = team(a), team(b)
    if src[a] == src[b]:
        kinds["same package"] += 1
    elif ta is None or tb is None:
        kinds["maintainer unknown"] += 1
    elif ta == tb == "debian-python-team":
        kinds["both Debian Python Team"] += 1
    elif ta == tb:
        kinds["same maintainer"] += 1
    else:
        kinds["different maintainers (cross-team)"] += 1
print(f"package->package edges: {len(pkg_edges)}")
for k, v in kinds.most_common():
    print(f"  {k}: {v} ({v/len(pkg_edges):.0%})")

teams = Counter(team(b) for b in src if team(b))
print(f"py2removal packages maintained by Debian Python Team: "
      f"{teams['debian-python-team']} of {sum(teams.values())}")
print(f"distinct maintainers (teams): {len(teams)}")

# Check 3 signal: waiting language in the 20 sampled blocked bugs (keyword hit, not verified).
WAIT = re.compile(r"\bwait(ing)? (for|on)\b|\bonce .{0,40}(lands|is fixed|migrat|ported|uploaded)"
                  r"|\bblocked (by|on)\b|\bdepends on .{0,40}(first|being)", re.I)
sample = random.Random(SEED).sample(pkg_edges, N)
rows = []
for a, b in sample:
    raw = requests.get(f"https://bugs.debian.org/cgi-bin/bugreport.cgi?bug={a};mbox=yes",
                       timeout=60).content
    hits = []
    with tempfile.NamedTemporaryFile(suffix=".mbox") as f:
        f.write(raw)
        f.flush()
        for msg in mailbox.mbox(f.name):
            if "control@bugs.debian.org" in (msg["To"] or "") + (msg["Cc"] or ""):
                continue
            for p in (msg.walk() if msg.is_multipart() else [msg]):
                if p.get_content_type() != "text/plain":
                    continue
                text = (p.get_payload(decode=True) or b"").decode("utf-8", "replace")
                text = "\n".join(l for l in text.splitlines() if not l.startswith(">"))
                hits += [m.group(0) for m in WAIT.finditer(text)]
    rows.append(dict(blocked_bug=a, blocked_src=src[a], blocker_bug=b, blocker_src=src[b],
                     blocked_team=team(a), blocker_team=team(b),
                     wait_keyword_hits=len(hits), example="; ".join(hits[:3])))
    print(a, src[a], "<-", b, src[b], "| hits:", len(hits), "|", "; ".join(hits[:2]))

with open("debian_sample20_wait_check.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)
print(f"\nsampled bugs with any waiting language: {sum(r['wait_keyword_hits'] > 0 for r in rows)} of {N}")
