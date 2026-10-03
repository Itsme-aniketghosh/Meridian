"""Pull gitlab-org/gitlab issues created 2025-01-01..2025-07-01: author, title, dates, group labels, current blocked-by links."""
import json, os, sys, time, urllib.request
URL = "https://gitlab.com/api/graphql"
Q = """query($after:String){ project(fullPath:"gitlab-org/gitlab"){
  issues(createdAfter:"2025-01-01T00:00:00Z", createdBefore:"2025-07-01T00:00:00Z", first:100, after:$after, sort:CREATED_ASC){
    pageInfo{ hasNextPage endCursor }
    nodes{ iid title createdAt closedAt state author{ username }
      labels(first:100){ nodes{ title } }
      blockedByIssues{ nodes{ iid webUrl state closedAt labels(first:100){ nodes{ title } } } } } } } }"""
out, cur = "issues.jsonl", "cursor.txt"
after = (open(cur).read().strip() or None) if os.path.exists(cur) else None
with open(out, "a") as f:
    while True:
        body = json.dumps({"query": Q, "variables": {"after": after}}).encode()
        for attempt in range(6):
            try:
                d = json.load(urllib.request.urlopen(urllib.request.Request(URL, body, {"Content-Type": "application/json"}), timeout=90))
                if "errors" in d: raise RuntimeError(str(d["errors"])[:200])
                break
            except Exception as e:
                print("retry", attempt, e, file=sys.stderr, flush=True); time.sleep(5 * (attempt + 1))
        else: sys.exit("giving up")
        page = d["data"]["project"]["issues"]
        for n in page["nodes"]: f.write(json.dumps(n) + "\n")
        after = page["pageInfo"]["endCursor"]; open(cur, "w").write(after or "")
        print(page["nodes"][-1]["createdAt"] if page["nodes"] else "-", flush=True)
        if not page["pageInfo"]["hasNextPage"]: break
        time.sleep(0.3)
print("DONE", flush=True)
