import json, re, statistics as st
from datetime import datetime as D
P=lambda s:D.fromisoformat(s.replace("Z","+00:00"))
iss={json.loads(l)["iid"]:json.loads(l) for l in open("issues.jsonl")}
rows=[json.loads(l) for l in open("notes.jsonl")]
N=len(rows); trunc=sum(r["notes"]["pageInfo"]["hasNextPage"] for r in rows)
c=dict(rel_ever=0,rel_now=0,status_blocked=0,label_blocked_note=0); durs=[]; bodies=set()
for r in rows:
    ns=sorted(r["notes"]["nodes"],key=lambda n:n["createdAt"]); b=[n["body"] for n in ns]
    if any(re.search(r"marked this (item|issue|task) as (blocked by|blocking)",x) for x in b): c["rel_ever"]+=1
    i=iss[r["iid"]]
    if i["blockedByCount"] or i["blockingCount"]: c["rel_now"]+=1
    if any("status to **Blocked**" in x for x in b): c["status_blocked"]+=1
    if any("workflow::blocked" in x for x in b): c["label_blocked_note"]+=1
    t0=None
    for n in ns:
        if "status to **Blocked**" in n["body"]: t0=t0 or P(n["createdAt"])
        elif t0 and "set status to" in n["body"]: durs.append((P(n["createdAt"])-t0).total_seconds()/86400); t0=None
    for x in b:
        if "status" in x or "block" in x: bodies.add(re.sub(r"#\d+|@\S+","#",x)[:70])
print("sample",N,"notes truncated at 100:",trunc); print(c)
if durs: print(f"blocked spells {len(durs)}: median {st.median(durs):.1f}d, >14d {sum(d>14 for d in durs)}")
print("\n".join(sorted(bodies)[:40]))
