import json, statistics as st, collections
from datetime import datetime as D
P=lambda s:D.fromisoformat(s.replace("Z","+00:00"))
iss={json.loads(l)["iid"]:json.loads(l) for l in open("issues.jsonl")}
rows=[json.loads(l) for l in open("labels.jsonl")]; N=len(rows)
def grp(ls): return {l for l in ls if l.startswith("group::")}
ever_wf=ever_blk=0; spells=[]; open_spells=0; waits=[]; cross=same=nogrp=0; link_types=collections.Counter()
for r in rows:
    ev=sorted(r["events"],key=lambda e:e["t"])
    if any((e["label"] or "").startswith("workflow::") for e in ev): ever_wf+=1
    t0=None
    for e in ev:
        if e["label"]=="workflow::blocked":
            if e["action"]=="add": t0=t0 or P(e["t"])
            elif t0: spells.append((P(e["t"])-t0).days); t0=None
    if t0: open_spells+=1
    if any(e["label"]=="workflow::blocked" for e in ev): ever_blk+=1
    # wait from creation to first "in dev"
    i=iss[r["iid"]]; dev=[P(e["t"]) for e in ev if e["action"]=="add" and e["label"]=="workflow::in dev"]
    if dev: waits.append((min(dev)-P(i["createdAt"])).days)
    g=grp(l["title"] for l in i["labels"]["nodes"])
    for l in r["links"]:
        link_types[l["type"]]+=1
        if l["type"] in ("blocks","is_blocked_by"):
            h=grp(l["labels"])
            if not g or not h: nogrp+=1
            elif g&h: same+=1
            else: cross+=1
print(f"N={N}")
print(f"ever any workflow:: label   {ever_wf}")
print(f"ever workflow::blocked      {ever_blk}")
if spells: print(f"closed blocked spells {len(spells)}: median {st.median(spells)}d, p75 {st.quantiles(spells,n=4)[2]:.0f}d, >=14d {sum(s>=14 for s in spells)}")
print(f"still blocked (never unblocked) {open_spells}")
if waits: print(f"created -> first in dev: N={len(waits)} median {st.median(waits)}d, >=14d {sum(w>=14 for w in waits)}")
print("current links by type", dict(link_types))
print(f"block links: cross-team {cross}, same-team {same}, missing group {nogrp}")
