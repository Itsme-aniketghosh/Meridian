"""Label history + issue links for the seeded 1,000 sample. Token read from ~/.gitlab_token, never printed."""
import json, os, sys, time, urllib.request
T=open(os.path.expanduser("~/.gitlab_token")).read().strip()
B="https://gitlab.com/api/v4/projects/278964/issues/"
def get(u):
    out=[]; page=1
    while True:
        for a in range(5):
            try:
                r=urllib.request.urlopen(urllib.request.Request(f"{u}{'&' if '?' in u else '?'}per_page=100&page={page}",headers={"PRIVATE-TOKEN":T}),timeout=60)
                d=json.load(r); break
            except Exception as e: print("retry",a,type(e).__name__,file=sys.stderr); time.sleep(5*(a+1))
        else: sys.exit("giving up")
        out+=d
        if not r.headers.get("x-next-page"): return out
        page+=1
sample=open("sample_1000.txt").read().split()
done={json.loads(l)["iid"] for l in open("labels.jsonl")} if os.path.exists("labels.jsonl") else set()
with open("labels.jsonl","a") as f:
    for k,i in enumerate(s for s in sample if s not in done):
        ev=get(B+i+"/resource_label_events")
        ln=get(B+i+"/links")
        f.write(json.dumps({"iid":i,
          "events":[{"t":e["created_at"],"action":e["action"],"label":(e.get("label") or {}).get("name")} for e in ev],
          "links":[{"iid":l["iid"],"project":l.get("project_id"),"type":l.get("link_type"),
                    "labels":l.get("labels",[])} for l in ln]})+"\n")
        if k%100==0: print(k,file=sys.stderr)
