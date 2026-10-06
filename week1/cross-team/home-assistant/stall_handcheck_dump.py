"""Dump readable timelines (comments, reviews, labels, commits) for the 40 seeded stalled PRs (stall/stall_sample40.json from stall_prs.py)
into stall/handcheck_dump.txt for manual coding -> stall_hand_check_40.csv. python3 stall_handcheck_dump.py"""
import json, subprocess, time
from stall_common import *
S=json.load(open(f"{OUT}/stall_sample40.json")); own=owners(manifests())
out=open(f"{OUT}/handcheck_dump.txt","w")
for k in range(0,len(S),5):
    b=S[k:k+5]
    q="{repository(owner:\"home-assistant\",name:\"core\"){"+" ".join(f"""n{r['number']}:pullRequest(number:{r['number']}){{number title state createdAt mergedAt closedAt author{{login}} body
 timelineItems(first:120,itemTypes:[ISSUE_COMMENT,PULL_REQUEST_REVIEW,LABELED_EVENT,UNLABELED_EVENT,PULL_REQUEST_COMMIT,CLOSED_EVENT,MERGED_EVENT,READY_FOR_REVIEW_EVENT,CONVERT_TO_DRAFT_EVENT,REVIEW_REQUESTED_EVENT,CROSS_REFERENCED_EVENT]){{nodes{{__typename
 ... on IssueComment{{author{{login}} createdAt body}} ... on PullRequestReview{{author{{login}} state submittedAt body comments(first:5){{nodes{{body}}}}}}
 ... on LabeledEvent{{createdAt actor{{login}} label{{name}}}} ... on UnlabeledEvent{{createdAt label{{name}}}} ... on PullRequestCommit{{commit{{committedDate messageHeadline}}}}
 ... on ClosedEvent{{createdAt actor{{login}}}} ... on MergedEvent{{createdAt actor{{login}}}} ... on ReadyForReviewEvent{{createdAt}} ... on ConvertToDraftEvent{{createdAt}}
 ... on ReviewRequestedEvent{{createdAt requestedReviewer{{... on User{{login}}}}}} ... on CrossReferencedEvent{{createdAt source{{... on PullRequest{{number title repository{{nameWithOwner}}}} ... on Issue{{number title repository{{nameWithOwner}}}}}}}}}}}}}}""" for r in b)+"}}"
    d=json.loads(subprocess.run(["gh","api","graphql","-f",f"query={q}"],capture_output=True,text=True).stdout)["data"]["repository"]
    for r in b:
        p=d[f"n{r['number']}"]; co={i:sorted(own.get(i,[])) for i in r["integrations"]}
        out.write(f"\n{'='*100}\n#{p['number']} {p['title']} | {p['state']} | by {p['author']['login']} | created {p['createdAt'][:10]} merged {(p['mergedAt'] or '')[:10]} closed {(p['closedAt'] or '')[:10]} | max_gap {r['max_gap']:.0f}d | codeowners {co} | author_codeowner {r['author_codeowner']}\nBODY: {(p['body'] or '')[:700]}\n")
        for n in p["timelineItems"]["nodes"]:
            t=n["__typename"]; ts=n.get("createdAt") or n.get("submittedAt") or (n.get("commit") or {}).get("committedDate") or ""
            who=(n.get("author") or n.get("actor") or {}).get("login","") if (n.get("author") or n.get("actor")) else ""
            if t=="IssueComment" and who in("home-assistant","codecov"): continue
            txt=(n.get("body") or "")+" ".join(c["body"] for c in (n.get("comments") or {}).get("nodes",[]))
            extra={"LabeledEvent":lambda:"+"+n["label"]["name"],"UnlabeledEvent":lambda:"-"+n["label"]["name"],"PullRequestCommit":lambda:n["commit"]["messageHeadline"],
                   "PullRequestReview":lambda:n["state"],"ReviewRequestedEvent":lambda:str((n.get("requestedReviewer") or {}).get("login")),
                   "CrossReferencedEvent":lambda:f"{n['source'].get('repository',{}).get('nameWithOwner')}#{n['source'].get('number')} {n['source'].get('title','')[:60]}" if n.get("source") else ""}.get(t,lambda:"")()
            out.write(f"  {ts[:10]} {t[:14]:14s} {who:18s} {extra[:70]} {txt[:400].replace(chr(10),' ').replace(chr(13),'')}\n")
    time.sleep(1)
print("wrote",f"{OUT}/handcheck_dump.txt")
