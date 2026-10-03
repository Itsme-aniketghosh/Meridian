"""Seeded sample of 1,000 issues from issues.jsonl; pull system notes for each. Writes notes.jsonl."""
import json, random, sys, time, urllib.request, os
iids=sorted({json.loads(l)["iid"] for l in open("issues.jsonl")}, key=int)
random.seed(1); sample=random.sample(iids, 1000)
open("sample_1000.txt","w").write("\n".join(sample))
done={json.loads(l)["iid"] for l in open("notes.jsonl")} if os.path.exists("notes.jsonl") else set()
todo=[i for i in sample if i not in done]
Q="""query($i:[String!]){ project(fullPath:"gitlab-org/gitlab"){ issues(iids:$i, first:20){ nodes{ iid
  notes(first:100, filter:ONLY_ACTIVITY){ pageInfo{hasNextPage} nodes{ body createdAt } } } } } }"""
with open("notes.jsonl","a") as f:
    for k in range(0,len(todo),20):
        body=json.dumps({"query":Q,"variables":{"i":todo[k:k+20]}}).encode()
        for a in range(5):
            try:
                d=json.load(urllib.request.urlopen(urllib.request.Request("https://gitlab.com/api/graphql",body,{"Content-Type":"application/json"}),timeout=90))
                if "errors" in d: raise RuntimeError(str(d["errors"])[:300])
                break
            except Exception as e: print("retry",a,e,file=sys.stderr); time.sleep(5*(a+1))
        else: sys.exit("giving up")
        for n in d["data"]["project"]["issues"]["nodes"]: f.write(json.dumps(n)+"\n")
        print(k,file=sys.stderr); time.sleep(0.3)
