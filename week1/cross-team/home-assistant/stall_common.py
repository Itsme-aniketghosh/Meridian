"""Shared helpers for stall_*.py. Manifests are read at the mid-window commit (dev @ 2025-04-01) so owners match the time."""
import json, subprocess, os, statistics as st, datetime as dt
SCR="data"
REPO=f"{SCR}/repo"; OUT=f"{SCR}/stall"; HERE=os.path.dirname(os.path.abspath(__file__))
MID="6a012498a5d6d815571abbafb5a83076a1b0267d"   # git rev-list -1 --before=2025-04-01 dev
def sh(*a): return subprocess.run(a,capture_output=True,text=True,cwd=REPO).stdout
def manifests(c=MID):
    cache=f"{OUT}/manifests_{c[:8]}.json"
    if os.path.exists(cache): return json.load(open(cache))
    man={}
    for p in sh("git","ls-tree","-r","--name-only",c,"homeassistant/components").splitlines():
        if p.endswith("/manifest.json") and p.count("/")==3:
            try: man[p.split("/")[2]]=json.loads(sh("git","show",f"{c}:{p}"))
            except Exception: pass
    json.dump(man,open(cache,"w")); return man
def owners(man): return {k:{o.lstrip("@").lower() for o in v.get("codeowners",[])} for k,v in man.items()}
P=lambda s: dt.datetime.fromisoformat(s.replace("Z","+00:00")) if s else None
days=lambda a,b:(b-a).total_seconds()/86400
import glob
def load(name):  # name like "prs.jsonl" -> concatenates prs_*.jsonl, dedup by number
    seen={}
    for f in sorted(glob.glob(f"{OUT}/{name.replace('.jsonl','_*.jsonl')}"))+glob.glob(f"{OUT}/{name}"):
        for l in open(f):
            if l.strip(): d=json.loads(l); seen[d["number"]]=d
    return [seen[k] for k in sorted(seen)]
BOTS={"github-actions","issue-triage-workflows","copilot-pull-request-reviewer","coderabbitai","sourcery-ai","home-assistant","dependabot","renovate","copilot","codecov","pre-commit-ci"}
def is_bot(a): return (not a) or a.get("__typename")=="Bot" or a.get("login","").endswith("[bot]") or a.get("login","").lower() in BOTS
def integ(node): return sorted({l["name"].split(": ",1)[1] for l in node["labels"]["nodes"] if l["name"].startswith("integration: ")})
def med(x): return round(st.median(x),1) if x else None
def pct(x,q):
    x=sorted(x); return round(x[min(len(x)-1,int(q*len(x)))],1) if x else None
