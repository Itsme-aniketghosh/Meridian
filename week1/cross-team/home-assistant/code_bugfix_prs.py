"""#4 step 1: merged PRs into dev labelled `bugfix`, 2024-10-01..2026-10-01, via GraphQL search in weekly windows.
Run: python3 code_bugfix_prs.py  -> $SCRATCH/ha/code/bugfix_prs.json (resumable, skips windows already fetched)"""
import json, os, subprocess, time, datetime as dt
OUT = "data/code/bugfix_prs.json"
Q = """query($q:String!,$after:String){search(query:$q,type:ISSUE,first:100,after:$after){issueCount pageInfo{hasNextPage endCursor}
 nodes{... on PullRequest{number mergedAt title author{login} mergeCommit{oid} labels(first:15){nodes{name}}}}} rateLimit{remaining cost}}"""
def gql(q, after):
    args = ["gh", "api", "graphql", "-f", f"query={Q}", "-f", f"q={q}"] + (["-f", f"after={after}"] if after else [])
    for i in range(5):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0: return json.loads(r.stdout)["data"]
        print("retry", r.stderr[:200]); time.sleep(20 * (i + 1))
    raise SystemExit("gql failed")
data = json.load(open(OUT)) if os.path.exists(OUT) else {}
d = dt.date(2024, 10, 1); end = dt.date(2026, 10, 1)
while d < end:
    e = min(d + dt.timedelta(days=7), end); key = f"{d}..{e - dt.timedelta(days=1)}"
    if key in data: d = e; continue
    q = f"repo:home-assistant/core is:pr is:merged base:dev label:bugfix merged:{key}"
    nodes, after = [], None
    while True:
        s = gql(q, after)["search"]; nodes += s["nodes"]
        if not s["pageInfo"]["hasNextPage"]: break
        after = s["pageInfo"]["endCursor"]; time.sleep(1)
    if s["issueCount"] > 1000: print("WARNING >1000 in", key)
    data[key] = {"issueCount": s["issueCount"], "prs": nodes}
    json.dump(data, open(OUT, "w")); print(key, s["issueCount"], len(nodes), flush=True)
    time.sleep(2.5); d = e
print("total", sum(len(v["prs"]) for v in data.values()))
