"""#1 step: seeded sample (random.seed(1), n=150) of HACS default integrations; archived check via one batched GraphQL query;
clone each with --shallow-since=2023-06-01 (fallback --depth 1). Run: python3 code_hacs_clone.py -> $SCRATCH/ha/code/hacs/{owner__repo}, hacs_sample.json"""
import json, os, random, subprocess, urllib.request
S = "data/code"
f = f"{S}/hacs_integration.json"
if not os.path.exists(f):
    urllib.request.urlretrieve("https://raw.githubusercontent.com/hacs/default/master/integration", f)
repos = sorted(json.load(open(f))); random.seed(1); sample = random.sample(repos, 150)
meta = {}
for i in range(0, 150, 50):
    part = sample[i:i + 50]
    q = "query{" + " ".join(f'r{i+j}:repository(owner:"{r.split("/")[0]}",name:"{r.split("/")[1]}"){{isArchived isEmpty pushedAt}}' for j, r in enumerate(part)) + "}"
    out = json.loads(subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True).stdout or "{}")
    for j, r in enumerate(part): meta[r] = (out.get("data") or {}).get(f"r{i+j}")
os.makedirs(f"{S}/hacs", exist_ok=True); env = dict(os.environ, GIT_TERMINAL_PROMPT="0"); res = []
for r in sample:
    m = meta.get(r); d = f"{S}/hacs/{r.replace('/', '__')}"
    st = "missing" if m is None else "archived" if m["isArchived"] else "empty" if m["isEmpty"] else "ok"
    if st == "ok" and not os.path.exists(d):
        url = f"https://github.com/{r}.git"
        p = subprocess.run(["git", "clone", "-q", "--shallow-since=2023-06-01", url, d], capture_output=True, text=True, env=env, timeout=300)
        if p.returncode != 0:  # no commits since that date -> take tip only
            subprocess.run(["rm", "-rf", d]); p = subprocess.run(["git", "clone", "-q", "--depth", "1", url, d], capture_output=True, text=True, env=env, timeout=300)
            st = "ok" if p.returncode == 0 else "clone_failed"
    res.append({"repo": r, "status": st, "pushedAt": (m or {}).get("pushedAt")}); print(r, st, flush=True)
json.dump(res, open(f"{S}/hacs_sample.json", "w"), indent=0)
import collections; print(collections.Counter(x["status"] for x in res))
