"""Stall analysis over every issue in the window: labels_all.jsonl + issues.jsonl."""
import json, collections, statistics as st, random, sys
from datetime import datetime as D, timezone
P=lambda s:D.fromisoformat(s.replace("Z","+00:00"))
SNAP=P("2026-10-02T00:00:00Z")
iss={json.loads(l)["iid"]:json.loads(l) for l in open("issues.jsonl")}
lab={json.loads(l)["iid"]:json.loads(l) for l in open("labels_all.jsonl")}
import os
TESTBOT="project_278964_bot_87c17d71a842955abfceaf361a49f249"   # files the "[Test] spec/..." flaky-test issues
if os.environ.get("HUMAN"):
    au={json.loads(l)["iid"]:json.loads(l)["author"] for l in open("authors.jsonl")}
    iss={i:v for i,v in iss.items() if au.get(i)!=TESTBOT}
    print("HUMAN=1: dropped test-bot issues")
BOTS=("gitlab-bot","cogbot","gl-infra","triage","bot")
isbot=lambda u:bool(u) and any(b in u.lower() for b in BOTS)
def labels(i): return [l["title"] for l in iss[i]["labels"]["nodes"]]
def one(ls,p):
    v=[l.split("::",1)[1] for l in ls if l.startswith(p)]; return v[0] if v else "(none)"
def q(xs,p): xs=sorted(xs); return xs[min(len(xs)-1,int(p*len(xs)))] if xs else None
N=len(iss); have=[i for i in iss if i in lab and lab[i]["events"] is not None]
print(f"issues {N}; label history pulled {len(have)}; missing/404 {N-len(have)}")
ever_wf=set(); blocked=set(); spells=[]; open_sp=[]; adders=collections.Counter(); bulk=collections.Counter(); first_dev={}
for i in have:
    ev=sorted(lab[i]["events"],key=lambda e:e["t"])
    if any((e["label"] or "").startswith("workflow::") for e in ev): ever_wf.add(i)
    t0=None; who=None
    for k,e in enumerate(ev):
        if e["label"]=="workflow::dev" or (e["label"]=="workflow::in dev" and e["action"]=="add"): first_dev.setdefault(i,P(e["t"]))
        if e["label"]!="workflow::blocked": continue
        if e["action"]=="add" and t0 is None:
            t0=P(e["t"]); who=e["user"]; blocked.add(i); adders["bot" if isbot(who) else "person"]+=1; bulk[(who,e["t"][:10])]+=1
        elif e["action"]=="remove" and t0 is not None:
            end=P(e["t"]); nxt=[x["label"] for x in ev if x["t"]==e["t"] and x["action"]=="add" and (x["label"] or "").startswith("workflow::")]
            ca=iss[i]["closedAt"]; at_close=bool(ca) and abs((P(ca)-end).total_seconds())<86400
            spells.append(dict(iid=i,start=t0,end=end,days=(end-t0).total_seconds()/86400,next=nxt[0] if nxt else "(none)",at_close=at_close,who=who))
            t0=None
    if t0 is not None: open_sp.append(dict(iid=i,start=t0,days=(SNAP-t0).total_seconds()/86400,state=iss[i]["state"],who=who))
days=[s["days"] for s in spells]
print(f"\n== Blocked")
print(f"ever any workflow:: label {len(ever_wf)} ({len(ever_wf)/len(have):.1%})")
print(f"ever workflow::blocked    {len(blocked)} ({len(blocked)/len(have):.1%})")
print(f"spells closed {len(spells)}, still blocked {len(open_sp)} (of them issue closed: {sum(s['state']=='closed' for s in open_sp)})")
print(f"closed spell days: median {st.median(days):.1f}, p25 {q(days,.25):.1f}, p75 {q(days,.75):.1f}, p90 {q(days,.9):.1f}")
print(f"  <1 day {sum(d<1 for d in days)}, 1-14 {sum(1<=d<14 for d in days)}, 14-90 {sum(14<=d<90 for d in days)}, 90+ {sum(d>=90 for d in days)}")
print(f"  ended at issue close (±1 day) {sum(s['at_close'] for s in spells)}")
print("  next workflow label after unblock:",collections.Counter(s["next"] for s in spells).most_common(8))
print(f"label added by: {dict(adders)}")
big=[(k,v) for k,v in bulk.items() if v>=10]; print(f"bulk adds (same user+day, >=10): {len(big)} days, {sum(v for k,v in big)} issues", sorted(big,key=lambda x:-x[1])[:5])
print("\n== By team (group::), top 15 by issues")
g=collections.Counter(one(labels(i),"group::") for i in have); gb=collections.Counter(one(labels(i),"group::") for i in blocked)
for k,v in g.most_common(15): print(f"  {k:<32} {v:>5}  blocked {gb[k]:>3} ({gb[k]/v:.1%})")
print(f"  groups with >=1 blocked issue: {len(gb)} of {len(g)}")
print("\n== By type (type::)")
t=collections.Counter(one(labels(i),"type::") for i in have); tb=collections.Counter(one(labels(i),"type::") for i in blocked)
for k,v in t.most_common(): print(f"  {k:<14} {v:>5}  blocked {tb[k]:>3} ({tb[k]/v:.1%})")
print("\n== Wait to start (created -> first workflow::in dev)")
w=[(first_dev[i]-P(iss[i]["createdAt"])).total_seconds()/86400 for i in first_dev]
print(f"  N {len(w)}: median {st.median(w):.1f} d, p75 {q(w,.75):.0f}, 14+ d {sum(x>=14 for x in w)} ({sum(x>=14 for x in w)/len(w):.0%}), 90+ d {sum(x>=90 for x in w)}")
print("\n== Milestone slips (missed:* labels)")
missed=lambda i:[l for l in labels(i) if l.startswith("missed:")]
nb=[i for i in have if i not in blocked]
mb=sum(bool(missed(i)) for i in blocked); mn=sum(bool(missed(i)) for i in nb)
print(f"  any missed:* now: all {sum(bool(missed(i)) for i in have)}; blocked {mb}/{len(blocked)} ({mb/len(blocked):.0%}) vs never-blocked {mn}/{len(nb)} ({mn/len(nb):.0%})")
print(f"  2+ misses: blocked {sum(len(missed(i))>=2 for i in blocked)}, never-blocked {sum(len(missed(i))>=2 for i in nb)}")
print("\n== Cross-team, for blocked issues (blocked-by links still present)")
c=collections.Counter()
for i in blocked:
    gi={l for l in labels(i) if l.startswith("group::")}
    for b in iss[i]["blockedByIssues"]["nodes"]:
        h={l["title"] for l in b["labels"]["nodes"] if l["title"].startswith("group::")}
        c["no group" if not gi or not h else "same" if gi&h else "cross"]+=1
print(f"  blocked issues with a blocked-by link now: {sum(bool(iss[i]['blockedByIssues']['nodes']) for i in blocked)} of {len(blocked)}; edges {dict(c)}")
print("\n== Open 90+ days (NEXT-STEPS stall definition)")
op=[i for i in iss if iss[i]["state"]=="opened"]
print(f"  open at snapshot {len(op)} of {N}; all are 15+ months old. Of them ever blocked {sum(i in blocked for i in op)}, never in dev {sum(i not in first_dev for i in op)}")
if not os.environ.get("HUMAN"): json.dump({"spells":[{**s,"start":s["start"].isoformat(),"end":s["end"].isoformat()} for s in spells],
           "open":[{**s,"start":s["start"].isoformat()} for s in open_sp]},open("spells_all.json","w"))
