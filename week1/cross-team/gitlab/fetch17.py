"""Full record for the 17 hand-check issues: issue, notes, label/milestone/state events, links. Token from ~/.gitlab_token."""
import json, os, csv, time, urllib.request
T=open(os.path.expanduser("~/.gitlab_token")).read().strip()
B="https://gitlab.com/api/v4/projects/278964/issues/"
def get(u, paged=True):
    out=[]; p=1
    while True:
        r=urllib.request.urlopen(urllib.request.Request(u+("&" if "?" in u else "?")+f"per_page=100&page={p}",headers={"PRIVATE-TOKEN":T}),timeout=60)
        d=json.load(r)
        if not paged: return d
        out+=d
        if not r.headers.get("x-next-page"): return out
        p+=1
rows=list(csv.DictReader(open("hand_check_17.csv")))
with open("issues17.json","w") as f:
    rec={}
    for r in rows:
        i=r["iid"]
        rec[i]={"issue":get(B+i,False),"notes":get(B+i+"/notes?sort=asc"),
          "labels":get(B+i+"/resource_label_events"),"milestones":get(B+i+"/resource_milestone_events"),
          "state":get(B+i+"/resource_state_events"),"links":get(B+i+"/links")}
        time.sleep(0.2)
    json.dump(rec,f)
print("ok",len(rec))
