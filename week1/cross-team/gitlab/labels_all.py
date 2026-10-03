"""Label history for every issue in issues.jsonl. 8 threads, resumable. Token from ~/.gitlab_token, never printed.
Writes labels_all.jsonl: {iid, events:[{t, action, label, user}]}"""
import json, os, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
T=open(os.path.expanduser("~/.gitlab_token")).read().strip()
B="https://gitlab.com/api/v4/projects/278964/issues/"
def get(u):
    out=[]; p=1
    while True:
        for a in range(6):
            try:
                r=urllib.request.urlopen(urllib.request.Request(f"{u}?per_page=100&page={p}",headers={"PRIVATE-TOKEN":T}),timeout=60)
                d=json.load(r); break
            except urllib.error.HTTPError as e:
                if e.code==404: return None
                time.sleep(10*(a+1) if e.code==429 else 3*(a+1))
            except Exception: time.sleep(3*(a+1))
        else: raise RuntimeError("giving up on "+u)
        out+=d
        if not r.headers.get("x-next-page"): return out
        p+=1
iids=sorted({json.loads(l)["iid"] for l in open("issues.jsonl")},key=int)
done=set()
if os.path.exists("labels_all.jsonl"):
    done={json.loads(l)["iid"] for l in open("labels_all.jsonl")}
todo=[i for i in iids if i not in done]
lock=threading.Lock(); f=open("labels_all.jsonl","a"); n=[0]; t0=time.time()
def work(i):
    ev=get(B+i+"/resource_label_events")
    row={"iid":i,"events":None if ev is None else [{"t":e["created_at"],"action":e["action"],
         "label":(e.get("label") or {}).get("name"),"user":(e.get("user") or {}).get("username")} for e in ev]}
    with lock:
        f.write(json.dumps(row)+"\n"); n[0]+=1
        if n[0]%1000==0: f.flush(); print(f"{n[0]}/{len(todo)} {time.time()-t0:.0f}s",file=sys.stderr,flush=True)
with ThreadPoolExecutor(8) as ex: list(ex.map(work,todo))
f.close(); print(f"done {n[0]} in {time.time()-t0:.0f}s",file=sys.stderr)
