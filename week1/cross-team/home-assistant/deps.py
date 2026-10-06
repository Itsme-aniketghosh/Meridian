"""Integration -> integration edges at a snapshot, from manifest.json and from Python imports, split by owner.
Run from the clone: python3 deps.py <commit>"""
import json, re, subprocess, sys, collections
C=sys.argv[1]
def sh(*a): return subprocess.run(a,capture_output=True,text=True).stdout
man={}
for p in sh("git","ls-tree","-r","--name-only",C,"homeassistant/components").splitlines():
    if p.endswith("/manifest.json") and p.count("/")==3:
        try: man[p.split("/")[2]]=json.loads(sh("git","show",f"{C}:{p}"))
        except Exception: pass
own={k:set(v.get("codeowners",[])) for k,v in man.items()}
def kind(a,b):
    if not own.get(a) or not own.get(b): return "no owner"
    return "shared owner" if own[a]&own[b] else "different owners"
print("integrations",len(man),"with codeowners",sum(bool(v) for v in own.values()),"distinct owners",len(set().union(*own.values())))
for field in ("dependencies","after_dependencies"):
    e=[(a,b) for a,m in man.items() for b in m.get(field,[]) if b in man]
    print(field,"edges",len(e),"integrations with one",len({a for a,_ in e}),dict(collections.Counter(kind(a,b) for a,b in e)))
    print("  top targets",collections.Counter(b for _,b in e).most_common(6))
imp=collections.Counter()
for line in sh("git","grep","-h","-n","-E","--full-name","^from homeassistant\\.components\\.[a-z0-9_]+",C,"--","homeassistant/components").splitlines(): pass
for line in sh("git","grep","-E","^\\s*(from|import) homeassistant\\.components\\.[a-z0-9_]+",C,"--","homeassistant/components").splitlines():
    path,code=line.split(":",2)[1],line.split(":",2)[2]
    a=path.split("/")[2]; b=re.search(r"homeassistant\.components\.([a-z0-9_]+)",code).group(1)
    if a!=b and b in man: imp[(a,b)]+=1
k=collections.Counter(kind(a,b) for a,b in imp)
print("python import edges (pairs)",len(imp),"integrations importing another",len({a for a,_ in imp}),dict(k))
print("  top imported",collections.Counter(b for _,b in imp).most_common(8))
plat={"sensor","binary_sensor","light","switch","climate","cover","fan","lock","media_player","camera","button","number","select","text","vacuum","water_heater","alarm_control_panel","device_tracker","event","update","weather","siren","humidifier","lawn_mower","valve","image","date","time","datetime","notify","tts","stt","conversation","todo","calendar","scene","remote","assist_satellite","ai_task","wake_word","infrared"}
nonplat={p:n for p,n in imp.items() if p[1] not in plat}
print("  excluding entity-platform integrations (core-owned base classes):",len(nonplat),"pairs,",dict(collections.Counter(kind(a,b) for a,b in nonplat)))
print("  top non-platform targets",collections.Counter(b for _,b in nonplat).most_common(10))
