"""#5 blocking. Needs prs_*/issues_*.jsonl (stall_fetch.py). Fetches comment bodies for candidates (cached), PyPI JSON for libs.
python3 stall_block.py  -> prints numbers, writes stall_block_library_60.csv and stall_block_inrepo.csv"""
import re, json, os, subprocess, time, collections, random, csv, urllib.request
from stall_common import *
man=manifests(); own=owners(man)
prs={p["number"]:p for p in load("prs.jsonl")}; iss={i["number"]:i for i in load("issues.jsonl")}
allit={**prs,**iss}
human=lambda x: not is_bot(x["author"])
def gql(q):
    for t in range(5):
        r=subprocess.run(["gh","api","graphql","-f",f"query={q}"],capture_output=True,text=True)
        try:
            j=json.loads(r.stdout)
            if j.get("data"): return j["data"]
        except Exception: pass
        time.sleep(15*(t+1))
CC=f"{OUT}/comments_cache.json"; cache=json.load(open(CC)) if os.path.exists(CC) else {}
def comments(nums):
    need=[n for n in nums if str(n) not in cache]
    for k in range(0,len(need),20):
        b=need[k:k+20]
        q="{repository(owner:\"home-assistant\",name:\"core\"){"+" ".join(f"n{n}:issueOrPullRequest(number:{n}){{... on PullRequest{{number state mergedAt closedAt createdAt author{{login}} labels(first:30){{nodes{{name}}}} comments(first:60){{nodes{{author{{login}} createdAt body}}}} reviews(first:40){{nodes{{author{{login}} state submittedAt body}}}}}} ... on Issue{{number state closedAt createdAt author{{login}} labels(first:30){{nodes{{name}}}} comments(first:60){{nodes{{author{{login}} createdAt body}}}}}}}}" for n in b)+"}}"
        d=gql(q) or {"repository":{}}
        for n in b:
            v=d["repository"].get(f"n{n}"); cache[str(n)]=v
        json.dump(cache,open(CC,"w")); time.sleep(1)
    return {n:cache.get(str(n)) for n in nums}
def owners_of(item): s=integ(item); return set(s),set().union(*[own.get(x,set()) for x in s]) if s else set()
# ---------------- A) in-repo "depends on / blocked by" ----------------
PAT=re.compile(r"(depends on|dependent on|blocked by|blocked on|requires|needs|after|waiting (?:for|on)|on top of|builds? (?:up)?on|follow[- ]up (?:to|of|on)|prerequisite)\s*:?\s*(?:PR\s*)?(?:#|https://github\.com/home-assistant/core/(?:pull|issues)/)(\d{5,6})(?:\s+(?:is|has been|gets|to be|be) merged)?",re.I)
cand=[]
for n,x in allit.items():
    if not human(x): continue
    for m in PAT.finditer(x.get("body") or ""):
        t=int(m.group(2))
        if t!=n: cand.append((n,t,m.group(0)[:80]))
cand=list({(a,b):(a,b,c) for a,b,c in cand}.values())
print("A) in-repo blocker phrases in bodies of human PRs/issues in window:",len(cand),"pairs from",len({a for a,_,_ in cand}),"items")
print("   phrase verbs:",collections.Counter(re.match(r"\w+(?: \w+)?",c).group(0).lower() for _,_,c in cand).most_common(10))
cm=comments(sorted({a for a,_,_ in cand}|{b for _,b,_ in cand}))
WAITW=re.compile(r"(wait|merged first|once .{0,40}merged|after .{0,30}merged|is merged|blocked|depends|rebase|draft until|on hold|has been merged|now merged|ready now|unblock)",re.I)
rows=[]
for a,b,phr in cand:
    A_,B_=cm.get(a),cm.get(b)
    if not A_ or not B_: rows.append(dict(item=a,target=b,phrase=phr,real="target not PR/issue")); continue
    ta=A_.get("mergedAt") or A_.get("closedAt"); tb=B_.get("mergedAt") or B_.get("closedAt")
    order= bool(tb) and P(tb)>P(A_["createdAt"]) and (ta is None or P(ta)>=P(tb))
    texts=[c["body"] for c in (A_["comments"]["nodes"]+A_.get("reviews",{"nodes":[]})["nodes"]) if c.get("body") and c["author"] and not c["author"]["login"].endswith("bot") and c["author"]["login"] not in BOTS]
    ev=[t for t in texts if WAITW.search(t) and (str(b) in t or re.search(r"\b(wait|merged|blocked|depends|rebase|unblock)",t,re.I))]
    ia,oa=owners_of(A_); ib,ob=owners_of(B_)
    au,bu=A_["author"]["login"].lower() if A_["author"] else "",B_["author"]["login"].lower() if B_["author"] else ""
    rel="same author" if au==bu else "same integration" if ia&ib else "shared owner" if oa&ob else "different owners" if (oa and ob) else "no owner/label info"
    if rel in("same integration",) and au!=bu and not (oa&{au,bu}) : rel="same integration, different people"
    wait=days(P(A_["createdAt"]),P(tb)) if tb and order else None
    real="yes" if order and ev else "order only" if order else "no"
    rows.append(dict(item=a,target=b,phrase=phr,relation=rel,merge_order=order,comment_evidence=(ev[0][:160].replace("\n"," ") if ev else ""),real=real,wait_days=round(wait,1) if wait else "",item_integrations=" ".join(sorted(ia)),target_integrations=" ".join(sorted(ib)),item_author=au,target_author=bu))
FU=re.compile(r"^(follow|on top|build)",re.I)
for r in rows: r["kind"]="follow-up/builds-on" if FU.match(r["phrase"]) else "blocker phrase"
print("   of which follow-up/builds-on (not a blocker):",sum(r["kind"]!="blocker phrase" for r in rows),"; blocker phrases:",sum(r["kind"]=="blocker phrase" for r in rows))
print("   real-wait test (blocker phrases only):",dict(collections.Counter(r["real"] for r in rows if r["kind"]=="blocker phrase")))
R=[r for r in rows if r["real"]=="yes" and r["kind"]=="blocker phrase"]
print("   real waits (merge order + comment) N=",len(R),"by relation",dict(collections.Counter(r["relation"] for r in R)),"median wait",med([r["wait_days"] for r in R if r["wait_days"]!=""]),"d")
print("   order-only by relation",dict(collections.Counter(r["relation"] for r in rows if r["real"]=="order only" and r["kind"]=="blocker phrase")))
with open(f"{HERE}/stall_block_inrepo.csv","w") as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0].keys()) if rows else ["item"],extrasaction="ignore"); w.writeheader(); [w.writerow(r) for r in rows if "relation" in r]
# GitHub native relation
d=gql('{search(type:ISSUE,query:"repo:home-assistant/core is:blocked",first:1){issueCount}}'); print("   native GitHub blockedBy relation (search is:blocked, whole repo):",d["search"]["issueCount"])
# ---------------- B) through libraries ----------------
norm=lambda s:re.sub(r"[-_.]+","-",s.lower())
req=collections.defaultdict(set)
for k,v in man.items():
    for r in v.get("requirements",[]):
        m=re.match(r"([A-Za-z0-9_.\-]+)",r)
        if m: req[norm(m.group(1))].add(k)
dep=[p for p in prs.values() if human(p) and ({l["name"] for l in p["labels"]["nodes"]}&{"dependency","dependency-bump","dependencies"})]
print("\nB) human PRs labelled dependency/dependency-bump:",len(dep),"(bot-authored:",sum(1 for p in prs.values() if not human(p) and {l['name'] for l in p['labels']['nodes']}&{'dependency','dependency-bump','dependencies'}),")")
def libs_of(p):
    out=[]
    for m in re.finditer(r"\b(?:bump|update|upgrade)s?\s+`?([A-Za-z0-9_.\-]+)`?\s+(?:to|from|lib|library|dependency|requirement|version|==|>=|\d)",p.get("title","")+" "+(p.get("body") or "")[:0],re.I):
        if norm(m.group(1)) in req: out.append(norm(m.group(1)))
    return out
# titles are not in prs.jsonl; get them cheaply with the comment fetch below
cmd=comments([p["number"] for p in dep])
TQ={}
need=[p["number"] for p in dep]
TC=f"{OUT}/titles_cache.json"; TQ=json.load(open(TC)) if os.path.exists(TC) else {}
for k in range(0,len(need),50):
    b=[n for n in need[k:k+50] if str(n) not in TQ]
    if not b: continue
    d=gql("{repository(owner:\"home-assistant\",name:\"core\"){"+" ".join(f"n{n}:pullRequest(number:{n}){{title}}" for n in b)+"}}")
    for n in b: TQ[str(n)]=(d["repository"].get(f"n{n}") or {}).get("title","")
    json.dump(TQ,open(TC,"w")); time.sleep(1)
UP=re.compile(r"(wait(?:ing)? (?:for|on) (?:the |a |an )?(?:upstream|librar|lib\b|new (?:release|version)|release|pypi|maintainer|vendor|the fix|(?:https://)?github\.com)|once (?:the |a )?(?:new )?(?:upstream|librar|lib\b|release|version|package|fix).{0,60}(?:released|published|merged|available|out)|(?:upstream|library) (?:pr|fix|release|issue|bug|maintainer)|not (?:yet )?(?:been )?released|new release (?:of|is|was|has)|requires? (?:a )?new (?:version|release)|pending (?:upstream|release)|blocked (?:by|on) (?:the )?(?:upstream|librar)|released (?:yet|now)|when (?:it|the library|upstream) (?:is|gets) released|cut a (?:new )?release|publish(?:ed)? (?:a )?new (?:version|release))",re.I)
XREPO=re.compile(r"https://github\.com/(?!home-assistant/(?:core|home-assistant\.io|frontend)\b)([\w.-]+)/([\w.-]+)/(pull|issues|releases|compare|commit)")
lrows=[]
for p in dep:
    c=cmd.get(p["number"]) or {}
    texts=[("body",p.get("body") or "")]+[("comment by "+(x["author"] or {}).get("login",""),x["body"]) for x in c.get("comments",{"nodes":[]})["nodes"]+c.get("reviews",{"nodes":[]})["nodes"] if x.get("body") and x["author"] and not x["author"]["login"].endswith("bot") and x["author"]["login"] not in BOTS]
    ups=[(w,UP.search(t)) for w,t in texts if UP.search(t)]
    labs={l["name"] for l in p["labels"]["nodes"]}
    title=TQ.get(str(p["number"]),"")
    libs=[]
    for m in re.finditer(r"([A-Za-z0-9][A-Za-z0-9_.\-]*)",title):
        if norm(m.group(1)) in req and norm(m.group(1)) not in libs: libs.append(norm(m.group(1)))
    waited=bool(ups) or "waiting-for-upstream" in labs
    ev=""
    if ups:
        w,m=ups[0]; t=dict(texts)[w] if w in dict(texts) else ""; s=max(0,m.start()-80); ev=f"[{w}] ..."+t[s:m.end()+80].replace("\n"," ").replace("\r","")+"..."
    elif "waiting-for-upstream" in labs: ev="label waiting-for-upstream"
    end=p.get("mergedAt") or p.get("closedAt")
    lrows.append(dict(pr=p["number"],title=title,libs=" ".join(libs),integrations=" ".join(sorted(set(integ(p))|{i for l in libs for i in req[l]})),author=p["author"]["login"].lower(),
        merged=bool(p.get("mergedAt")),open_days=round(days(P(p["createdAt"]),P(end)),1) if end else "",waited=waited,xrepo_link=bool(XREPO.search(" ".join(t for _,t in texts))),evidence=ev[:300]))
W=[r for r in lrows if r["waited"]]
print("   with a library lib-name in title matched to manifest requirements:",sum(bool(r["libs"]) for r in lrows),"; body/comment/label shows an upstream wait:",len(W),f"({100*len(W)/max(1,len(lrows)):.1f}%)","; links another repo's PR/issue/release:",sum(r["xrepo_link"] for r in lrows))
PC=f"{OUT}/pypi_cache.json"; PY=json.load(open(PC)) if os.path.exists(PC) else {}
def gh_repo(lib):
    if lib not in PY:
        try:
            j=json.load(urllib.request.urlopen(f"https://pypi.org/pypi/{lib}/json",timeout=20))["info"]
            urls=list((j.get("project_urls") or {}).values())+[j.get("home_page") or ""]
            m=next((re.search(r"github\.com/([\w.-]+)/([\w.-]+)",u) for u in urls if u and re.search(r"github\.com/([\w.-]+)/([\w.-]+)",u)),None)
            PY[lib]=[m.group(1).lower(),re.sub(r"\.git$","",m.group(2)).lower()] if m else [None,None]
        except Exception as e: PY[lib]=[None,None]
        json.dump(PY,open(PC,"w")); time.sleep(0.2)
    return PY[lib]
CT=f"{OUT}/contrib_cache.json"; CTR=json.load(open(CT)) if os.path.exists(CT) else {}
def contributors(o,r):
    k=f"{o}/{r}"
    if k not in CTR:
        x=subprocess.run(["gh","api",f"repos/{o}/{r}/contributors?per_page=5"],capture_output=True,text=True).stdout
        try: CTR[k]=[c["login"].lower() for c in json.loads(x)]
        except Exception: CTR[k]=[]
        json.dump(CTR,open(CT,"w")); time.sleep(0.3)
    return CTR[k]
def owner_class(r,deep):
    if not r["libs"]: return "no lib matched","",""
    lib=r["libs"].split()[0]; o,rep=gh_repo(lib); co=set().union(*[own.get(i,set()) for i in r["integrations"].split()]) if r["integrations"] else set()
    if not o: return "unknown (no GitHub URL on PyPI)","",""
    top=contributors(o,rep) if deep else []
    if o in co or o==r["author"]: return "same person (codeowner owns lib repo)",f"{o}/{rep}",",".join(top[:3])
    if set(top[:3])&(co|{r["author"]}): return "same person (codeowner is top-3 contributor)",f"{o}/{rep}",",".join(top[:3])
    if o in("home-assistant-libs","home-assistant","esphome","openhomefoundation","zigpy","music-assistant","python-zwave-js","ohf-voice"): return "HA/OHF org library",f"{o}/{rep}",",".join(top[:3])
    return "different maintainer (cross-team)",f"{o}/{rep}",",".join(top[:3])
for r in lrows: r["owner_class"],r["lib_repo"],r["lib_top_contributors"]=owner_class(r,False)
print("   ALL human dependency PRs, lib owner vs codeowners (owner-login only):",dict(collections.Counter(r["owner_class"] for r in lrows)))
print("   dependency PRs whose text shows an upstream wait (regex), by owner class:",dict(collections.Counter(owner_class(r,True)[0] for r in W)))
random.seed(1); S=random.sample(sorted([r for r in lrows if r["libs"]],key=lambda r:r["pr"]),60)
for r in S: r["owner_class"],r["lib_repo"],r["lib_top_contributors"]=owner_class(r,True); r["checked_by"]="script (regex on body/comments + PyPI/GitHub owner), not a human"
cnt=collections.Counter(r["owner_class"] for r in S)
print(f"   seeded sample of 60 human dependency PRs with a matched lib (owner + top-3 contributors):",dict(cnt))
for k in cnt: 
    x=[r["open_days"] for r in S if r["owner_class"]==k and r["open_days"]!=""]; print(f"     {k}: median open {med(x)} d (N={len(x)})")
print("   median open (created->merge/close) waited:",med([r["open_days"] for r in W if r["open_days"]!=""]),"vs not waited:",med([r["open_days"] for r in lrows if not r["waited"] and r["open_days"]!=""]))
with open(f"{HERE}/stall_block_library_60.csv","w") as f:
    w=csv.DictWriter(f,fieldnames=["pr","title","libs","lib_repo","integrations","author","lib_top_contributors","owner_class","merged","open_days","xrepo_link","evidence","checked_by"],extrasaction="ignore"); w.writeheader(); [w.writerow(r) for r in S]
# non-dependency PRs waiting for a library
lab=[p for p in prs.values() if human(p) and "waiting-for-upstream" in {l["name"] for l in p["labels"]["nodes"]}|{n["label"]["name"] for n in p["timelineItems"]["nodes"] if n["__typename"]=="LabeledEvent" and n["label"]}]
print("   any human PR ever labelled waiting-for-upstream:",len(lab))
# ---------------- C) shared-client edges ----------------
plat={"sensor","binary_sensor","light","switch","climate","cover","fan","lock","media_player","camera","button","number","select","text","vacuum","water_heater","alarm_control_panel","device_tracker","event","update","weather","siren","humidifier","lawn_mower","valve","image","date","time","datetime","notify","tts","stt","conversation","todo","calendar","scene","remote","assist_satellite","ai_task","wake_word","infrared"}
edges=set((a,b) for a,m in man.items() for b in m.get("dependencies",[])+m.get("after_dependencies",[]) if b in man)
for line in sh("git","grep","-E","^\\s*(from|import) homeassistant\\.components\\.[a-z0-9_]+",MID,"--","homeassistant/components").splitlines():
    parts=line.split(":",2); a=parts[1].split("/")[2]; b=re.search(r"homeassistant\.components\.([a-z0-9_]+)",parts[2]).group(1)
    if a!=b and b in man: edges.add((a,b))
X=[(a,b) for a,b in edges if b not in plat and own.get(a) and own.get(b) and not own[a]&own[b]]
print(f"\nC) integration->shared-integration edges (manifest deps + imports, non-platform target, both owned, owners disjoint): {len(X)} edges, {len({a for a,_ in X})} dependent integrations, {len({b for _,b in X})} shared targets")
labs=collections.defaultdict(set)
for p in prs.values():
    if human(p) and p.get("mergedAt"):
        for i in integ(p): labs[i].add(p["number"])
co=[(a,b,labs[a]&labs[b]) for a,b in X if labs[a]&labs[b]]
tb=[(a,b) for a,b in X if labs[b]]
print(f"   edges whose shared target had a merged human PR in window: {len(tb)} ({len({a for a,_ in tb})} dependent integrations)")
print(f"   edges with a merged PR that touched BOTH sides (labels A and B): {len(co)} edges, {len({n for *_,s in co for n in s})} PRs; e.g. {[(a,b,sorted(s)[:2]) for a,b,s in co[:6]]}")
print("   top shared targets by #dependent integrations with disjoint owners:",collections.Counter(b for _,b in X).most_common(8))

# ---------------- D) feature PRs that waited on a library bump (the real lib chain) ----------------
SQ=['"until" "is merged"','"once" "is merged"','"after" "is merged"','"merged first"','"waiting for" release','"new release"','"blocked by"','"depends on"','"needs" "bump"']
SC=f"{OUT}/search_cache.json"; SR=json.load(open(SC)) if os.path.exists(SC) else {}
for q in SQ:
    if q in SR: continue
    nums=[];cur=None
    for _ in range(3):
        d=gql('{search(type:ISSUE,first:100,%squery:"repo:home-assistant/core is:pr created:2025-01-01..2025-06-30 %s"){issueCount pageInfo{hasNextPage endCursor} nodes{... on PullRequest{number}}}}'%(f'after:"{cur}",' if cur else "",q.replace('"','\\"')))
        nums+=[n["number"] for n in d["search"]["nodes"] if n]; time.sleep(3)
        if not d["search"]["pageInfo"]["hasNextPage"]: break
        cur=d["search"]["pageInfo"]["endCursor"]
    SR[q]=nums; json.dump(SR,open(SC,"w"))
hits=sorted({n for v in SR.values() for n in v if n in prs and human(prs[n])})
print("\nD) text search (body+comments) for wait phrases, PRs in window:",len(hits),{q:len(v) for q,v in SR.items()})
cm2=comments(hits)
REF=re.compile(r"(?:#|home-assistant/core/pull/)(\d{5,6})")
WT=re.compile(r"(until|once|after|wait\w*|blocked|depends|needs?|requires?|merged first|first)",re.I)
deps_set={p["number"] for p in dep}
bump=[]
for n in hits:
    c=cm2.get(n) or {}
    texts=[("body",prs[n].get("body") or "")]+[((x["author"] or {}).get("login",""),x["body"]) for x in c.get("comments",{"nodes":[]})["nodes"]+c.get("reviews",{"nodes":[]})["nodes"] if x.get("body")]
    for who,t in texts:
        for m in REF.finditer(t):
            tgt=int(m.group(1)); ctx=t[max(0,m.start()-60):m.end()+40]
            if tgt in deps_set and WT.search(ctx) and tgt!=n:
                bump.append((n,tgt,who,ctx.replace("\n"," "))); break
seen={};[seen.setdefault((a,b),(a,b,w,x)) for a,b,w,x in bump]; bump=list(seen.values())
print("   feature PR -> dependency-bump PR waits found:",len(bump))
brows=[]
for a,b,who,ctx in bump:
    L=next((r for r in lrows if r["pr"]==b),None)
    if not L: continue
    oc,repo,top=owner_class(L,True)
    A_=prs[a]; B_=prs[b]; au=A_["author"]["login"].lower(); bu=B_["author"]["login"].lower()
    ta=A_.get("mergedAt") or A_.get("closedAt"); tb=B_.get("mergedAt")
    w=round(days(P(A_["createdAt"]),P(tb)),1) if tb and P(tb)>P(A_["createdAt"]) else 0.0
    feat_owner=au in set().union(*[own.get(i,set()) for i in integ(A_)]) if integ(A_) else False
    brows.append(dict(feature_pr=a,bump_pr=b,lib=L["libs"],lib_repo=repo,lib_top3=top,lib_owner_class=oc,feature_author=au,bump_author=bu,same_author=au==bu,feature_author_is_codeowner=feat_owner,wait_days=w,said_by=who,context=ctx[:200]))
print("   by lib owner class:",dict(collections.Counter(r["lib_owner_class"] for r in brows)),"; same author on both PRs:",sum(r["same_author"] for r in brows))
for k in sorted({r["lib_owner_class"] for r in brows}):
    x=[r["wait_days"] for r in brows if r["lib_owner_class"]==k]; print(f"     {k}: N={len(x)} median wait (feature created -> bump merged) {med(x)} d, >7 d: {sum(v>7 for v in x)}")
with open(f"{HERE}/stall_block_libwaits.csv","w") as f:
    w=csv.DictWriter(f,fieldnames=list(brows[0].keys()) if brows else ["feature_pr"]); w.writeheader(); [w.writerow(r) for r in brows]
