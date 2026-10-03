"""Condensed timeline per issue for reading."""
import json, re, sys
import os; rec=json.load(open(os.environ.get("REC","issues17.json")))
want=sys.argv[1:] or list(rec)
NOISE=re.compile(r"^(changed the description|mentioned in |changed title|added .*label|removed .*label|set weight|changed weight|marked the checklist)")
for i in want:
    r=rec[i]; s=r["issue"]
    print(f"\n######## #{i} [{s['state']}] {s['title']}\n created {s['created_at'][:10]} by {s['author']['username']}  closed {str(s.get('closed_at'))[:10]}  milestone {(s.get('milestone') or {}).get('title')}")
    print(" labels now:", [l for l in s['labels'] if l.split('::')[0] in ('workflow','group','type','missed') or l.startswith('missed')])
    print(" desc:", re.sub(r"\s+"," ",s.get("description") or "")[:700])
    ev=[]
    for e in r["labels"]:
        if e.get("label") and (e["label"]["name"].startswith("workflow::") ):
            ev.append((e["created_at"],(e.get("user") or {}).get("username"),f"{e['action']} label {e['label']['name']}"))
    for e in r["milestones"]:
        ev.append((e["created_at"],(e.get("user") or {}).get("username"),f"{e['action']} milestone {(e.get('milestone') or {}).get('title')}"))
    for e in r["state"]:
        ev.append((e["created_at"],(e.get("user") or {}).get("username"),f"state {e['state']}"))
    for n in r["notes"]:
        b=re.sub(r"\s+"," ",n["body"])
        if n["system"]:
            if NOISE.match(b): continue
            ev.append((n["created_at"],n["author"]["username"],"· "+b[:160]))
        else:
            ev.append((n["created_at"],n["author"]["username"],"💬 "+b[:400]))
    for t,u,x in sorted(ev): print(f"  {t[:10]} {u:<22} {x}")
    print(" links now:",[(l["iid"],l["link_type"],l["state"],[g for g in l["labels"] if g.startswith("group::")],l["title"][:50]) for l in r["links"]])
