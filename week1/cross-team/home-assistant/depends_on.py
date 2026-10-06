"""PRs whose body says 'depends on #N' (2025-H1). Resolve the target, compare integrations and owners, compute the wait.
Needs `gh` logged in. Run from the clone: python3 depends_on.py <snapshot commit>  -> depends_on.json"""
import json, re, subprocess, sys, time, statistics as st, collections
from datetime import datetime as D
C=sys.argv[1]; P=lambda s:D.fromisoformat(s.replace("Z","+00:00"))
def gh(*a): return json.loads(subprocess.run(["gh","api",*a],capture_output=True,text=True).stdout)
def sh(*a): return subprocess.run(a,capture_output=True,text=True).stdout
own={}
for p in sh("git","ls-tree","-r","--name-only",C,"homeassistant/components").splitlines():
    if p.endswith("/manifest.json") and p.count("/")==3:
        try: own[p.split("/")[2]]=set(json.loads(sh("git","show",f"{C}:{p}")).get("codeowners",[]))
        except Exception: pass
items=[]
for page in (1,2):
    r=gh("-X","GET","search/issues","-f",'q=repo:home-assistant/core is:pr "depends on #" created:2025-01-01..2025-06-30',"-f","per_page=100","-f",f"page={page}")
    items+=r["items"]; time.sleep(2)
integ=lambda labels:{l["name"].split(": ",1)[1] for l in labels if l["name"].startswith("integration: ")}
rows=[]
for it in items:
    m=re.findall(r"[Dd]epends on (?:https://github\.com/home-assistant/core/pull/|#)(\d+)",it.get("body") or "")
    if not m: continue
    t=gh(f"repos/home-assistant/core/issues/{m[0]}"); time.sleep(0.3)
    if "pull_request" not in t: kind="target is an issue"
    a,b=integ(it["labels"]),integ(t.get("labels",[]))
    oa=set().union(*[own.get(x,set()) for x in a]) if a else set(); ob=set().union(*[own.get(x,set()) for x in b]) if b else set()
    rel="no integration label" if not a or not b else "same integration" if a&b else ("different owners" if oa and ob and not oa&ob else "shared owner")
    merged=(t.get("pull_request") or {}).get("merged_at")
    wait=(P(merged)-P(it["created_at"])).total_seconds()/86400 if merged else None
    rows.append(dict(pr=it["number"],target=int(m[0]),pr_integrations=sorted(a),target_integrations=sorted(b),relation=rel,
                     target_is_pr="pull_request" in t,target_merged=bool(merged),wait_days=wait,pr_state=it["state"],pr_author=it["user"]["login"],target_author=t["user"]["login"]))
json.dump(rows,open("depends_on.json","w"),indent=1)
print("search hits",len(items),"parsed",len(rows))
print("relation",dict(collections.Counter(r["relation"] for r in rows)))
print("target is PR",sum(r["target_is_pr"] for r in rows),"merged",sum(r["target_merged"] for r in rows))
print("same author on both",sum(r["pr_author"]==r["target_author"] for r in rows))
for k in ("different owners","same integration","shared owner","no integration label"):
    w=[r["wait_days"] for r in rows if r["relation"]==k and r["wait_days"] is not None]
    if w: print(f"  wait {k}: N={len(w)} median {st.median(w):.1f} d, >7 d {sum(x>7 for x in w)}, negative (already merged) {sum(x<0 for x in w)}")
for r in [r for r in rows if r["relation"]=="different owners"][:8]: print("  ex", r["pr"],r["pr_integrations"],"->",r["target"],r["target_integrations"],r["wait_days"] and round(r["wait_days"],1))
