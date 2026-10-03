"""Activity notes (who linked what, when) for a list of iids. Usage: pull_notes.py iids.txt out.jsonl [ONLY_ACTIVITY|ONLY_COMMENTS]"""
import json, os, sys, time, urllib.request
src, out, filt = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "ONLY_ACTIVITY")
iids = [l.strip() for l in open(src) if l.strip()]
done = {json.loads(l)["iid"] for l in open(out)} if os.path.exists(out) else set()
todo = [i for i in iids if i not in done]
Q = """query($i:[String!]){ project(fullPath:"gitlab-org/gitlab"){ issues(iids:$i, first:20){ nodes{ iid
  notes(first:100, filter:%s){ pageInfo{hasNextPage} nodes{ body createdAt system author{ username } } } } } } }""" % filt
with open(out, "a") as f:
    for k in range(0, len(todo), 20):
        body = json.dumps({"query": Q, "variables": {"i": todo[k:k+20]}}).encode()
        for a in range(6):
            try:
                d = json.load(urllib.request.urlopen(urllib.request.Request("https://gitlab.com/api/graphql", body, {"Content-Type": "application/json"}), timeout=120))
                if "errors" in d: raise RuntimeError(str(d["errors"])[:200])
                break
            except Exception as e:
                print("retry", a, e, file=sys.stderr, flush=True); time.sleep(5 * (a + 1))
        else: sys.exit("giving up")
        for n in d["data"]["project"]["issues"]["nodes"]: f.write(json.dumps(n) + "\n")
        print(k + 20, "/", len(todo), flush=True); time.sleep(0.2)
print("DONE", flush=True)
