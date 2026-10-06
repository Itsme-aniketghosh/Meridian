#!/usr/bin/env python3
"""Pull Home Assistant core tickets via GitHub GraphQL.

Usage: python3 tix_pull.py [issues|prs|merged|all]
  issues : issues created 2025-01-01..2025-07-01 (search, 5-day windows)
  prs    : PRs created 2025-01-01..2025-07-01, all states, with body (search, 3-day windows)
  merged : all MERGED PRs (repository.pullRequests, createdAt DESC until 2024-12-01),
           with author, mergedAt, mergeCommit, labels, reviews -> used for #3
Output jsonl in $HA_TIX (default: scratchpad/ha/tix). Resumable per window/page.
"""
import json, os, subprocess, sys, time, datetime as dt, urllib.request

OUT = os.environ.get("HA_TIX", "data/tix")
os.makedirs(OUT, exist_ok=True)
TOKEN = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
REPO = "home-assistant/core"

def gql(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    for attempt in range(6):
        req = urllib.request.Request("https://api.github.com/graphql", data=body,
                                     headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.load(r)
            if "errors" in d and not d.get("data"):
                raise RuntimeError(d["errors"])
            rl = d["data"].get("rateLimit") or {}
            if rl and rl.get("remaining", 9999) < 300:
                wait = max(0, (dt.datetime.fromisoformat(rl["resetAt"].replace("Z", "+00:00")) - dt.datetime.now(dt.timezone.utc)).total_seconds()) + 5
                print("rate low, sleeping", wait, flush=True); time.sleep(wait)
            return d["data"]
        except Exception as e:
            print("retry", attempt, str(e)[:200], flush=True); time.sleep(10 * (attempt + 1))
    raise SystemExit("gave up")

ISSUE_FIELDS = """
 ... on Issue { number url author{login __typename} createdAt closedAt state stateReason
   labels(first:30){nodes{name}}
   closedByPullRequestsReferences(first:5, includeClosedPrs:true){nodes{number merged mergedAt author{login}}}
   comments(first:15){nodes{author{login __typename} createdAt}}
   timelineItems(first:30, itemTypes:[CLOSED_EVENT, REOPENED_EVENT, MARKED_AS_DUPLICATE_EVENT]){nodes{
     __typename
     ... on ClosedEvent{createdAt stateReason actor{login __typename}
        closer{__typename ... on PullRequest{number merged mergedAt} ... on Commit{oid}}}
     ... on ReopenedEvent{createdAt actor{login}}
     ... on MarkedAsDuplicateEvent{createdAt actor{login} canonical{__typename ... on Issue{number} ... on PullRequest{number}}}
   }}
 }"""
PR_FIELDS = """
 ... on PullRequest { number url author{login __typename} createdAt closedAt mergedAt state isDraft
   mergeCommit{oid} body labels(first:30){nodes{name}}
   closingIssuesReferences(first:5){nodes{number}}
 }"""
SEARCH_Q = """query($q:String!, $after:String){ rateLimit{cost remaining resetAt}
 search(type:ISSUE, query:$q, first:100, after:$after){ issueCount pageInfo{hasNextPage endCursor} nodes{ %s } } }"""

def search_pull(kind, fields, step_days, d0=dt.date(2025, 1, 1), end=dt.date(2025, 7, 1), extra=""):
    path = f"{OUT}/{kind}.jsonl"; done_path = f"{OUT}/{kind}.done"
    done = set(open(done_path).read().split()) if os.path.exists(done_path) else set()
    while d0 < end:
        d1 = min(d0 + dt.timedelta(days=step_days), end)
        win = f"{d0}T00:00:00Z..{(d1 - dt.timedelta(days=1))}T23:59:59Z"
        if win not in done:
            q = f"repo:{REPO} is:{'issue' if kind == 'issues' else 'pr'} created:{win}{extra}"
            after, rows = None, []
            while True:
                data = gql(SEARCH_Q % fields, {"q": q, "after": after})
                s = data["search"]
                if s["issueCount"] > 1000: raise SystemExit(f"window too big {win} {s['issueCount']}")
                rows += s["nodes"]; time.sleep(2.5)
                if not s["pageInfo"]["hasNextPage"]: break
                after = s["pageInfo"]["endCursor"]
            with open(path, "a") as f:
                for r in rows: f.write(json.dumps(r) + "\n")
            with open(done_path, "a") as f: f.write(win + "\n")
            print(kind, win, len(rows), "of", s["issueCount"], "rl", data["rateLimit"], flush=True)
        d0 = d1

MERGED_Q = """query($after:String){ rateLimit{cost remaining resetAt}
 repository(owner:"home-assistant", name:"core"){ pullRequests(states:MERGED, first:100, after:$after, orderBy:{field:CREATED_AT, direction:DESC}){
  pageInfo{hasNextPage endCursor} nodes{ number author{login __typename} createdAt mergedAt mergeCommit{oid}
   labels(first:30){nodes{name}} reviews(first:20){nodes{author{login} state}} } } } }"""

def merged_pull():
    path = f"{OUT}/merged_prs.jsonl"; cur_path = f"{OUT}/merged_prs.cursor"
    after = (open(cur_path).read().strip() or None) if os.path.exists(cur_path) else None
    while True:
        data = gql(MERGED_Q, {"after": after})
        c = data["repository"]["pullRequests"]
        with open(path, "a") as f:
            for r in c["nodes"]: f.write(json.dumps(r) + "\n")
        after = c["pageInfo"]["endCursor"]
        open(cur_path, "w").write(after or "")
        last = c["nodes"][-1]["createdAt"] if c["nodes"] else "0"
        print("merged page last", last, "rl", data["rateLimit"], flush=True)
        if not c["pageInfo"]["hasNextPage"] or last < "2024-12-01": break
        time.sleep(1)

MERGED_FIELDS = """ ... on PullRequest { number author{login __typename} createdAt mergedAt mergeCommit{oid}
   labels(first:30){nodes{name}} reviews(first:20){nodes{author{login} state}} }"""

if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what == "part":  # python3 tix_pull.py part <prs|merged> START END  -> parallel slice, own output file
        kind, a, b = sys.argv[2], dt.date.fromisoformat(sys.argv[3]), dt.date.fromisoformat(sys.argv[4])
        if kind == "prs": search_pull(f"prs_{a}", PR_FIELDS, 3, a, b)
        else: search_pull(f"merged_s_{a}", MERGED_FIELDS, 4, a, b, " is:merged")
        sys.exit()
    if what in ("issues", "all"): search_pull("issues", ISSUE_FIELDS, 5)
    if what in ("prs", "all"): search_pull("prs", PR_FIELDS, 3)
    if what in ("merged", "all"): merged_pull()
    # optional search-based duplicate of 'merged' (not used by tix_owners.py)
    if what == "merged_search1": search_pull("merged_s1", MERGED_FIELDS, 4, dt.date(2024, 12, 1), dt.date(2025, 7, 1), " is:merged")
    if what == "merged_search2": search_pull("merged_s2", MERGED_FIELDS, 4, dt.date(2025, 7, 1), dt.date(2026, 1, 1), " is:merged")
