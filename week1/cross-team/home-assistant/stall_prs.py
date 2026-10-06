"""#7 PR side. Needs prs.jsonl from stall_fetch.py pr. python3 stall_prs.py -> prints numbers, writes stall/stalled_prs.json"""
import json, collections, random
from stall_common import *
own=owners(manifests())
prs=[p for p in load("prs.jsonl") if not is_bot(p["author"])]
allp=load("prs.jsonl"); print("PRs fetched",len(allp),"human-authored",len(prs),"timeline truncated (>100 items)",sum(p["timelineItems"]["totalCount"]>100 for p in prs))
def ev_time(n):
    t=n["__typename"]
    if t=="PullRequestReview": return n["submittedAt"]
    if t=="PullRequestCommit": return n["commit"]["committedDate"]
    return n.get("createdAt")
rows=[]
for p in prs:
    au=p["author"]["login"].lower(); c=P(p["createdAt"]); end=P(p["mergedAt"] or p["closedAt"])
    tl=p["timelineItems"]["nodes"]
    revs=[n for n in tl if n["__typename"]=="PullRequestReview" and n["author"] and n["author"]["login"].lower()!=au and not n["author"]["login"].endswith("[bot]") and n["author"]["login"].lower() not in BOTS]
    fr=min((P(n["submittedAt"]) for n in revs if n["submittedAt"]),default=None)
    fa=min((P(n["submittedAt"]) for n in revs if n["state"]=="APPROVED" and n["submittedAt"]),default=None)
    hum=[n for n in tl if n["__typename"] in("IssueComment",) and n["author"] and not is_bot(n["author"]) and n["author"]["login"].lower()!=au]
    fresp=min([P(n["createdAt"]) for n in hum]+([fr] if fr else []),default=None)
    # activity = everything except bot comments/labels (stale bot labels are not activity)
    act=[]
    for n in tl:
        if n["__typename"] in("IssueComment","LabeledEvent","UnlabeledEvent","ClosedEvent","MergedEvent","PullRequestReview"):
            a=n.get("author") or n.get("actor")
            if a and (a["login"].endswith("[bot]") or a["login"].lower() in BOTS): continue
        t=ev_time(n)
        if t: act.append(P(t))
    act=sorted([c]+[t for t in act if t>=c]+([end] if end else []))
    gaps=[days(a,b) for a,b in zip(act,act[1:])]
    # draft intervals
    dr=[]; start=None
    evs=sorted([(P(n["createdAt"]),n["__typename"]) for n in tl if n["__typename"] in("ReadyForReviewEvent","ConvertToDraftEvent")])
    if evs and evs[0][1]=="ReadyForReviewEvent" or (not evs and p["isDraft"]): start=c
    for t,k in evs:
        if k=="ConvertToDraftEvent" and start is None: start=t
        elif k=="ReadyForReviewEvent" and start is not None: dr.append(days(start,t)); start=None
    if start is not None and p["isDraft"] and end: dr.append(days(start,end))
    labels={l["name"] for l in p["labels"]["nodes"]}
    stale_close=("stale" in labels or any(n["__typename"]=="LabeledEvent" and n["label"]["name"]=="stale" for n in tl)) and any(n["__typename"]=="ClosedEvent" and n["actor"] and n["actor"]["login"] in ("github-actions","issue-triage-workflows") for n in tl)
    ints=integ(p); co=any(au in own.get(i,set()) for i in ints)
    first_rev_gap=days(c,fr) if fr else (days(c,end) if end else None)
    rows.append(dict(number=p["number"],author=au,state=p["state"],created=p["createdAt"],ended=p["mergedAt"] or p["closedAt"],merged=bool(p["mergedAt"]),
        t_review=days(c,fr) if fr else None,t_approve=days(c,fa) if fa else None,t_merge=days(c,end) if p["mergedAt"] else None,t_resp=days(c,fresp) if fresp else None,
        no_review_14=(first_rev_gap or 0)>=14 and (fr is None or days(c,fr)>=14),max_gap=max(gaps) if gaps else 0,first_rev_gap=first_rev_gap,
        draft_days=sum(dr) if dr else None,was_draft=bool(dr),stale_closed=stale_close,stale_labelled="stale" in labels or any(n["__typename"]=="LabeledEvent" and n["label"]["name"]=="stale" for n in tl),
        integrations=ints,author_codeowner=co,labels=sorted(labels),author_assoc=p["authorAssociation"]))
done=[r for r in rows if r["state"] in("MERGED","CLOSED")]
m=[r for r in done if r["merged"]]; cl=[r for r in done if not r["merged"]]
print(f"\nclosed-or-merged human PRs N={len(done)}: merged {len(m)}, closed unmerged {len(cl)}; still open {len(rows)-len(done)}")
for k,lab in (("t_review","first review"),("t_approve","first approval"),("t_merge","merge"),("t_resp","first human non-author response (review or comment)")):
    x=[r[k] for r in done if r[k] is not None]
    print(f"  created->{lab}: N={len(x)} median {med(x)} d, p75 {pct(x,.75)}, p90 {pct(x,.9)}")
print(f"  sat >=14 d with no review: {sum(r['no_review_14'] for r in done)}/{len(done)} = {100*sum(r['no_review_14'] for r in done)/len(done):.1f}%")
print(f"  merged with zero reviews from others: {sum(r['t_review'] is None for r in m)}/{len(m)}")
d=[r["draft_days"] for r in done if r["was_draft"]]
print(f"  drafts: {len(d)}/{len(done)} were draft at some point, median draft time {med(d)} d, p90 {pct(d,.9)}")
sc=[r for r in done if r["stale_closed"]]; sl=[r for r in done if r["stale_labelled"]]
print(f"  stale-labelled {len(sl)}; closed by stale bot {len(sc)} = {100*len(sc)/len(cl):.1f}% of closed-unmerged ({len(cl)}); stale-labelled but later merged {sum(r['merged'] for r in sl)}")
st_=[r for r in done if r["max_gap"]>=30]
print(f"\nSTALLED (idle >=30 d at some point, incl. created->first review): {len(st_)}/{len(done)} = {100*len(st_)/len(done):.1f}%; merged {sum(r['merged'] for r in st_)}, closed {sum(not r['merged'] for r in st_)}, stale-bot closed {sum(r['stale_closed'] for r in st_)}")
print(f"  idle gap among stalled: median {med([r['max_gap'] for r in st_])} d")
for lab,f in (("author is codeowner",lambda r:r["author_codeowner"]),("author not codeowner (has integration label)",lambda r:not r["author_codeowner"] and r["integrations"]),("no integration label",lambda r:not r["integrations"])):
    g=[r for r in done if f(r)]; s=[r for r in g if r["max_gap"]>=30]
    print(f"  {lab}: stalled {len(s)}/{len(g)} = {100*len(s)/max(1,len(g)):.1f}%, merged share {100*sum(r['merged'] for r in g)/max(1,len(g)):.0f}%")
ci=collections.Counter(i for r in st_ for i in r["integrations"]); ai=collections.Counter(i for r in done for i in r["integrations"])
print("  top integrations by stalled count (stalled/all):",[(i,n,ai[i]) for i,n in ci.most_common(12)])
print("  integrations with >=1 stalled PR",len(ci),"of",len(ai))
json.dump(rows,open(f"{OUT}/pr_rows.json","w"))
random.seed(1); samp=random.sample(sorted(st_,key=lambda r:r["number"]),40)
json.dump(samp,open(f"{OUT}/stall_sample40.json","w"),indent=0); print("sample:",[r["number"] for r in samp])
