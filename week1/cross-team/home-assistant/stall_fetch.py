"""Fetch every PR and issue numbered 134374..147841 (= created 2025-01-01..2025-06-30) in home-assistant/core with timelines.
Uses aliased issueOrPullRequest(number:) batches of 25 (search-nested timelines come back silently trimmed, so no search).
Usage: python3 stall_fetch.py   (runs 4 parallel workers over the number range; writes prs_*.jsonl, issues_*.jsonl; resumable; ~1 point/call, ~540 calls)"""
import json, subprocess, sys, time, os, datetime as dt
out="data/stall"
LO,HI=134374,147842
if len(sys.argv)==1:   # parent: spawn 4 workers and wait
    edges=[LO+i*25*135 for i in range(4)]+[HI]
    ps=[subprocess.Popen([sys.executable,__file__,str(a),str(b)]) for a,b in zip(edges,edges[1:])]
    [p.wait() for p in ps]; raise SystemExit
A,B=int(sys.argv[1]),int(sys.argv[2])
FP,FI=f"{out}/prs_{A}.jsonl",f"{out}/issues_{A}.jsonl"; FD=f"{out}/done_batches.txt"
done=set(open(FD).read().split()) if os.path.exists(FD) else set()
common="""number createdAt closedAt state author{login __typename} authorAssociation body
 labels(first:30){nodes{name}}"""
frag_pr=f"""... on PullRequest {{ {common} mergedAt isDraft
 timelineItems(first:100,itemTypes:[PULL_REQUEST_REVIEW,ISSUE_COMMENT,LABELED_EVENT,UNLABELED_EVENT,PULL_REQUEST_COMMIT,READY_FOR_REVIEW_EVENT,CONVERT_TO_DRAFT_EVENT,CLOSED_EVENT,MERGED_EVENT,REOPENED_EVENT,HEAD_REF_FORCE_PUSHED_EVENT]) {{ totalCount nodes {{ __typename
  ... on PullRequestReview {{ author{{login}} authorAssociation state submittedAt }}
  ... on IssueComment {{ author{{login __typename}} authorAssociation createdAt }}
  ... on LabeledEvent {{ createdAt actor{{login}} label{{name}} }}
  ... on UnlabeledEvent {{ createdAt actor{{login}} label{{name}} }}
  ... on PullRequestCommit {{ commit {{ committedDate }} }}
  ... on ReadyForReviewEvent {{ createdAt }}
  ... on ConvertToDraftEvent {{ createdAt }}
  ... on ClosedEvent {{ createdAt actor{{login}} }}
  ... on MergedEvent {{ createdAt actor{{login}} }}
  ... on ReopenedEvent {{ createdAt }}
  ... on HeadRefForcePushedEvent {{ createdAt }} }} }} }}"""
frag_issue=f"""... on Issue {{ {common} stateReason
 timelineItems(first:100,itemTypes:[ISSUE_COMMENT,LABELED_EVENT,UNLABELED_EVENT,CLOSED_EVENT,REOPENED_EVENT]) {{ totalCount nodes {{ __typename
  ... on IssueComment {{ author{{login __typename}} authorAssociation createdAt }}
  ... on LabeledEvent {{ createdAt actor{{login}} label{{name}} }}
  ... on UnlabeledEvent {{ createdAt actor{{login}} label{{name}} }}
  ... on ClosedEvent {{ createdAt actor{{login}} }}
  ... on ReopenedEvent {{ createdAt }} }} }} }}"""
def run(nums):
    q="query{ rateLimit{cost remaining} repository(owner:\"home-assistant\",name:\"core\"){ "+" ".join(f"n{n}:issueOrPullRequest(number:{n}){{ __typename {frag_pr} {frag_issue} }}" for n in nums)+" } }"
    for t in range(6):
        r=subprocess.run(["gh","api","graphql","-f",f"query={q}"],capture_output=True,text=True)
        try:
            j=json.loads(r.stdout)
            if j.get("data") and j["data"].get("repository") is not None: return j
        except Exception: pass
        sys.stderr.write(f"retry {t} {r.stdout[:200]} {r.stderr[:200]}\n"); time.sleep(20*(t+1))
    raise SystemExit("failed")
fp,fi,fd=open(FP,"a"),open(FI,"a"),open(FD,"a")
for b in range(A,B,25):
    if str(b) in done: continue
    j=run(range(b,min(b+25,B)))
    for k,v in j["data"]["repository"].items():
        if not v or not v.get("number"): continue
        (fp if v["__typename"]=="PullRequest" else fi).write(json.dumps(v)+"\n")
    fp.flush(); fi.flush(); fd.write(f"{b}\n"); fd.flush()
    print(b,j["data"]["rateLimit"],flush=True); time.sleep(1.0)
