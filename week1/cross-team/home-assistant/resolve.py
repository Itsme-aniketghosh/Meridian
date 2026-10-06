"""Method-call benchmark: can Jedi resolve `<x>.async_forward_entry_setup(...)` to ConfigEntries.async_forward_entry_setup?
Run: python3 resolve.py <worktree dir>  (worktree checked out at the commit to test)  -> resolve.csv"""
import csv, re, subprocess, sys, time, jedi
W=sys.argv[1]; TARGET="homeassistant.config_entries.ConfigEntries.async_forward_entry_setup"
hits=subprocess.run(["git","-C",W,"grep","-n","-P",r"async_forward_entry_setup\(","--","homeassistant/components"],capture_output=True,text=True).stdout.splitlines()
proj=jedi.Project(W); rows=[]
for h in hits:
    path,line,code=h.split(":",2); line=int(line); col=code.index("async_forward_entry_setup(")
    t=time.time()
    try:
        r=jedi.Script(path=f"{W}/{path}",project=proj).goto(line,col+1,follow_imports=True)
        names=[d.full_name for d in r]
    except Exception as e: names=[f"error {type(e).__name__}"]
    rows.append({"file":path,"line":line,"answer":";".join(map(str,names)) or "(none)","right":TARGET in names,"seconds":round(time.time()-t,2),"code":code.strip()[:90]})
w=csv.DictWriter(open("resolve.csv","w",newline=""),fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print(f"call sites {len(rows)}; right {sum(r['right'] for r in rows)}; none {sum(r['answer']=='(none)' for r in rows)}; median s {sorted(r['seconds'] for r in rows)[len(rows)//2]}")
for r in rows:
    if not r["right"]: print("  miss",r["file"],r["line"],r["answer"][:80],"|",r["code"])
