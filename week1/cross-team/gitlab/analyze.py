import json, collections
iss=[json.loads(l) for l in open("issues.jsonl")]
seen={};[seen.setdefault(i["iid"],i) for i in iss]; iss=list(seen.values())
N=len(iss)
def labs(i): return [l["title"] for l in i["labels"]["nodes"]]
def grp(ls): return [l for l in ls if l.startswith("group::")]
by=[i for i in iss if i["blockedByCount"]>0]; bl=[i for i in iss if i["blockingCount"]>0]
any_=[i for i in iss if i["blockedByCount"]>0 or i["blockingCount"]>0]
print(f"issues {N}  (closed {sum(i['state']=='closed' for i in iss)})")
print(f"has group:: label {sum(bool(grp(labs(i))) for i in iss)}")
print(f"any block link {len(any_)} ({len(any_)/N:.1%}); blocked-by {len(by)}; blocking {len(bl)}")
print(f"currently labelled workflow::blocked {sum('workflow::blocked' in labs(i) for i in iss)}")
# cross-team edges: blocked issue vs blocker groups
c=collections.Counter()
for i in by:
    g=set(grp(labs(i)))
    for b in i["blockedByIssues"]["nodes"]:
        h=set(grp([l["title"] for l in b["labels"]["nodes"]]))
        c["missing group" if not g or not h else ("same" if g&h else "cross")]+=1
print("blocked-by edges", sum(c.values()), dict(c))
bugs=[i for i in iss if "type::bug" in labs(i)]
print(f"type::bug {len(bugs)}, with block link {sum(1 for i in bugs if i in any_)}")
wf=collections.Counter(l for i in iss for l in labs(i) if l.startswith("workflow::"))
print(f"has workflow:: label now {sum(any(l.startswith('workflow::') for l in labs(i)) for i in iss)}")
print(wf.most_common(8))
