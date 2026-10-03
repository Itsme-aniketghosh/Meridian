"""For a seeded sample of py2removal->py2removal block edges, find who added the link and when.

Reads each blocked bug's public mbox log and looks for control commands like
'block 123 by 456' or 'block 123 with 456'.
"""
import csv
import email
import email.utils
import mailbox
import random
import re
import tempfile
from collections import Counter

import psycopg2
import requests

SEED, N = 42, 20

conn = psycopg2.connect(host="udd-mirror.debian.net", dbname="udd",
                        user="udd-mirror", password="udd-mirror")
cur = conn.cursor()
cur.execute("select distinct id from bugs_usertags where tag = 'py2removal'")
ids = {r[0] for r in cur.fetchall()}

edges = [tuple(map(int, l.split("\t"))) for l in open("debian_edges.tsv").read().splitlines()[1:]]
pkg_edges = sorted(e for e in edges if e[0] in ids and e[1] in ids)
sample = random.Random(SEED).sample(pkg_edges, N)

BLOCK = re.compile(r"^\s*block\s+-?(\d+)\s+(?:by|with)\s+([\d\s]+)", re.I | re.M)


def body(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    out = []
    for p in parts:
        if p.get_content_type() == "text/plain":
            out.append((p.get_payload(decode=True) or b"").decode("utf-8", "replace"))
    return "\n".join(out)


rows = []
for blocked, blocker in sample:
    raw = requests.get(f"https://bugs.debian.org/cgi-bin/bugreport.cgi?bug={blocked};mbox=yes",
                       timeout=60).content
    with tempfile.NamedTemporaryFile(suffix=".mbox") as f:
        f.write(raw)
        f.flush()
        found = None
        for msg in mailbox.mbox(f.name):
            for m in BLOCK.finditer(body(msg)):
                if int(m.group(1)) == blocked and str(blocker) in m.group(2).split():
                    found = (email.utils.parseaddr(msg["From"] or "")[1], msg["Date"],
                             len(m.group(2).split()))
                    break
            if found:
                break
    who, when, n_in_cmd = found or ("not found in log", "", 0)
    rows.append(dict(blocked=blocked, blocker=blocker, added_by=who, added_at=when,
                     blockers_in_same_command=n_in_cmd))
    print(blocked, "<-", blocker, "|", who, "|", when, "| blockers in one command:", n_in_cmd)

with open("debian_who_linked_sample20.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)

print("\nadded_by:", Counter(r["added_by"] for r in rows).most_common())
