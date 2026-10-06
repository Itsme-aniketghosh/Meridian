"""#7 issue side (+ waiting-labels on PRs). Needs issues_*.jsonl / prs_*.jsonl from stall_fetch.py. python3 stall_issues.py"""
import collections, datetime as dt
from stall_common import *
own=owners(manifests()); NOW=P("2026-10-02T00:00:00Z")
iss=[i for i in load("issues.jsonl") if not is_bot(i["author"])]
print("issues fetched",len(load("issues.jsonl")),"human-authored",len(iss),"timeline >100 items",sum(i["timelineItems"]["totalCount"]>100 for i in iss))
MAINT={"MEMBER","OWNER","COLLABORATOR"}
r1=[];r2=[];nores=0
for i in iss:
    au=i["author"]["login"].lower(); c=P(i["createdAt"]); co=set().union(*[own.get(x,set()) for x in integ(i)]) if integ(i) else set()
    cm=[n for n in i["timelineItems"]["nodes"] if n["__typename"]=="IssueComment" and not is_bot(n["author"]) and n["author"]["login"].lower()!=au]
    a=[P(n["createdAt"]) for n in cm if n["authorAssociation"] in MAINT or n["author"]["login"].lower() in co]
    b=[P(n["createdAt"]) for n in cm if n["author"]["login"].lower() in co]
    if a: r1.append(days(c,min(a)))
    else: nores+=1
    if b: r2.append(days(c,min(b)))
wl=sum(bool(integ(i)) for i in iss)
print(f"first maintainer-or-codeowner comment: N={len(r1)}/{len(iss)} got one, median {med(r1)} d, p75 {pct(r1,.75)}, p90 {pct(r1,.9)}; never {nores} ({100*nores/len(iss):.0f}%)")
print(f"first codeowner-of-labelled-integration comment: N={len(r2)} (of {wl} issues with an integration label), median {med(r2)} d, p90 {pct(r2,.9)}")
def label_spells(items,name):
    sp=[];n=0;open_=0
    for i in items:
        tl=sorted([n_ for n_ in i["timelineItems"]["nodes"] if n_["__typename"] in("LabeledEvent","UnlabeledEvent","ClosedEvent")],key=lambda x:x["createdAt"])
        s=None;hit=False
        for e in tl:
            if e["__typename"]=="LabeledEvent" and e["label"] and e["label"]["name"]==name and s is None: s=P(e["createdAt"]);hit=True
            elif s and ((e["__typename"]=="UnlabeledEvent" and e["label"] and e["label"]["name"]==name) or e["__typename"]=="ClosedEvent"):
                sp.append(days(s,P(e["createdAt"])));s=None
        if s: sp.append(days(s,NOW)); open_+=1
        n+=hit
    return n,sp,open_
prs=[p for p in load("prs.jsonl") if not is_bot(p["author"])]
print("\nwaiting labels (spell = labelled -> unlabelled or closed; still-open spells counted to 2026-10-02):")
for nm in ("waiting-for-reply","waiting-for-upstream","problem in dependency","needs-more-information","waiting-for-test-hardware","awaiting-frontend","stale"):
    for lab,items in (("issues",iss),("PRs",prs)):
        n,sp,o=label_spells(items,nm)
        if n: print(f"  {nm:24s} {lab:6s}: {n:4d} items ({100*n/len(items):.1f}%), spells N={len(sp)} median {med(sp)} d, p90 {pct(sp,.9)}, still open {o}")
# stale bot on issues
st_=[];un=0;botclosed=0;noresp=[]
for i in iss:
    tl=sorted([n for n in i["timelineItems"]["nodes"] if n.get("createdAt")],key=lambda x:x["createdAt"])
    if not any(n["__typename"]=="LabeledEvent" and n["label"] and n["label"]["name"]=="stale" for n in tl): continue
    st_.append(i)
    if any(n["__typename"]=="UnlabeledEvent" and n["label"] and n["label"]["name"]=="stale" for n in tl): un+=1
    if any(n["__typename"]=="ClosedEvent" and n["actor"] and n["actor"]["login"] in ("github-actions","issue-triage-workflows") for n in tl):
        botclosed+=1; au=i["author"]["login"].lower(); co=set().union(*[own.get(x,set()) for x in integ(i)]) if integ(i) else set()
        noresp.append(not any(n["__typename"]=="IssueComment" and not is_bot(n["author"]) and n["author"]["login"].lower()!=au and (n["authorAssociation"] in MAINT or n["author"]["login"].lower() in co) for n in tl))
print(f"\nissues ever marked stale: {len(st_)}/{len(iss)} = {100*len(st_)/len(iss):.1f}%; un-staled (label removed after activity) {un}; closed by stale bot {botclosed} = {100*botclosed/len(iss):.1f}% of all issues; of those, never a maintainer/codeowner comment {sum(noresp)} ({100*sum(noresp)/max(1,botclosed):.0f}%)")
cl=[i for i in iss if i["state"]=="CLOSED"]
print(f"issue state: closed {len(cl)}, open {len(iss)-len(cl)}; stateReason {dict(collections.Counter(i.get('stateReason') for i in cl))}")
