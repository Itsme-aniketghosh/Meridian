"""Pull gitlab-org/gitlab issues created in a window, with blocking counts and labels.

Anonymous GraphQL. Writes issues.jsonl (one issue per line). Resumes from cursor.
Usage: python3 pull.py 2025-01-01 2025-07-01
"""
import json, os, sys, time, urllib.request

URL = "https://gitlab.com/api/graphql"
Q = """query($after:String,$a:Time,$b:Time){ project(fullPath:"gitlab-org/gitlab"){
  issues(createdAfter:$a, createdBefore:$b, first:100, after:$after, sort:CREATED_ASC){
    pageInfo{ hasNextPage endCursor }
    nodes{ iid createdAt closedAt state blockedByCount blockingCount
      labels(first:100){ nodes{ title } }
      blockedByIssues{ nodes{ iid labels(first:100){ nodes{ title } } } } } } } }"""

a, b = sys.argv[1], sys.argv[2]
out, cur = "issues.jsonl", "cursor.txt"
after = open(cur).read().strip() or None if os.path.exists(cur) else None
with open(out, "a") as f:
    while True:
        body = json.dumps({"query": Q, "variables": {"after": after, "a": a, "b": b}}).encode()
        req = urllib.request.Request(URL, body, {"Content-Type": "application/json"})
        for attempt in range(5):
            try:
                d = json.load(urllib.request.urlopen(req, timeout=60))
                if "errors" in d:
                    raise RuntimeError(d["errors"])
                break
            except Exception as e:
                print("retry", attempt, e, file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        else:
            sys.exit("giving up")
        page = d["data"]["project"]["issues"]
        for n in page["nodes"]:
            f.write(json.dumps(n) + "\n")
        after = page["pageInfo"]["endCursor"]
        open(cur, "w").write(after or "")
        print(page["nodes"][-1]["createdAt"] if page["nodes"] else "-", file=sys.stderr)
        if not page["pageInfo"]["hasNextPage"]:
            break
        time.sleep(0.3)
