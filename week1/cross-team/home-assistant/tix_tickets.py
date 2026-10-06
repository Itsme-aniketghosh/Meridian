#!/usr/bin/env python3
"""#2 Ticket says done / code disagrees + ticket quality + PR/issue bot shares, home-assistant/core.
Usage: python3 tix_tickets.py      (needs tix/issues.jsonl and tix/prs.jsonl from tix_pull.py)
Writes tix/closed_no_pr_pool.json (candidates for the hand-read; seeded sample of 20 with random.seed(1))."""
import json, os, re, glob, collections, random, datetime as dt
SCR = "data"
TIX = os.environ.get("HA_TIX", SCR + "/tix")
BOTRE = re.compile(r"\[bot\]$|^(dependabot|renovate|github-actions|home-assistant|copilot|issue-triage-workflows)", re.I)
STALE = {"issue-triage-workflows", "github-actions"}
def is_bot(a): a = a or {}; return a.get("__typename") == "Bot" or not a.get("login") or bool(BOTRE.search(a["login"]))
def load(pat): return list({r["number"]: r for p in sorted(glob.glob(pat)) for r in (json.loads(l) for l in open(p))}.values())
def pct(a, b): return f"{a}/{b} = {a/b:.1%}" if b else f"{a}/0"

I = load(f"{TIX}/issues.jsonl"); P = load(f"{TIX}/prs*.jsonl")
print(f"issues created 2025-01-01..2025-07-01: N={len(I)}; PRs created: N={len(P)}")
# ---- bots
merged = [p for p in P if p.get("mergedAt")]
bm = [p for p in merged if is_bot(p["author"])]
print(f"\nBOTS  merged PRs (of window-created PRs) N={len(merged)}: bot-authored {pct(len(bm), len(merged))}  {collections.Counter(p['author']['login'] if p['author'] else 'ghost' for p in bm).most_common(6)}")
bi = [i for i in I if is_bot(i["author"])]
print(f"      issue authors bot/ghost: {pct(len(bi), len(I))} {collections.Counter((i['author'] or {}).get('login','ghost') for i in bi).most_common(5)}")
print(f"      draft PRs at snapshot {sum(p['isDraft'] for p in P)}; PR states {collections.Counter(p['state'] for p in P)}")

# ---- closures
def closes(i): return [e for e in i["timelineItems"]["nodes"] if e["__typename"] == "ClosedEvent"]
def merged_refs(i): return [n for n in i["closedByPullRequestsReferences"]["nodes"] if n["merged"]]
def classify(i):
    ev = closes(i)
    last = ev[-1] if ev else {}
    actor = ((last.get("actor") or {}).get("login") or "").lower()
    closer = last.get("closer") or {}
    if actor in STALE: return "stale-bot"
    sr = i["stateReason"] or last.get("stateReason")
    if sr == "DUPLICATE": return "DUPLICATE"
    if sr == "NOT_PLANNED": return "NOT_PLANNED (human/bot cmd)"
    if closer.get("__typename") == "PullRequest" and closer.get("merged") or merged_refs(i): return "COMPLETED + merged PR"
    if closer.get("__typename") == "Commit": return "COMPLETED by commit"
    if actor == "home-assistant": return "COMPLETED via bot command, no PR"
    return "COMPLETED, no PR"
closed = [i for i in I if i["state"] == "CLOSED"]
cls = {i["number"]: classify(i) for i in closed}
C = collections.Counter(cls.values())
print(f"\nCLOSED ISSUES (state at snapshot), N={len(closed)} of {len(I)}; open {len(I)-len(closed)}")
for k, v in C.most_common(): print(f"  {k:34s} {pct(v, len(closed))}")
labs = collections.Counter(l["name"][13:] for i in closed for l in i["labels"]["nodes"] if l["name"].startswith("integration: "))
print("\n  by integration label (top 20):  N | merged PR | no PR (human+cmd) | NOT_PLANNED | DUP | stale")
rows = []
for d, n in labs.most_common(20):
    cc = collections.Counter(cls[i["number"]] for i in closed if any(l["name"] == "integration: " + d for l in i["labels"]["nodes"]))
    rows.append((d, n, cc))
    print(f"  {d:18s} {n:4d} | {cc['COMPLETED + merged PR']/n:5.0%} | {(cc['COMPLETED, no PR']+cc['COMPLETED via bot command, no PR'])/n:5.0%} | {cc['NOT_PLANNED (human/bot cmd)']/n:5.0%} | {cc['DUPLICATE']/n:4.0%} | {cc['stale-bot']/n:4.0%}")
nolab = sum(1 for i in closed if not any(l["name"].startswith("integration: ") for l in i["labels"]["nodes"]))
print(f"  closed issues with no integration label: {pct(nolab, len(closed))}")
pool = sorted(i["number"] for i in closed if cls[i["number"]] == "COMPLETED, no PR")
json.dump(pool, open(f"{TIX}/closed_no_pr_pool.json", "w"))
random.seed(1); print(f"\n  hand-read pool (COMPLETED, no merged PR, last closer human) N={len(pool)}; sample: {sorted(random.sample(pool, 20))}")

# ---- reverse
def ts(s): return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
open_fixed = [i for i in I if i["state"] == "OPEN" and merged_refs(i)]
print(f"\nREVERSE  still-open issues with a merged closing PR: {pct(len(open_fixed), len(I)-len(closed))} of open; e.g. {[ (i['number'], [n['number'] for n in merged_refs(i)]) for i in open_fixed[:6]]}")
reop = []
for i in I:
    mt = [ts(n["mergedAt"]) for n in merged_refs(i)] + [ts(e["closer"]["mergedAt"]) for e in closes(i) if (e.get("closer") or {}).get("merged")]
    if not mt: continue
    ro = [e for e in i["timelineItems"]["nodes"] if e["__typename"] == "ReopenedEvent" and ts(e["createdAt"]) > min(mt)]
    if ro: reop.append((i["number"], i["state"], (ro[0].get("actor") or {}).get("login")))
withfix = sum(1 for i in I if merged_refs(i))
print(f"         reopened after a fix merged: {pct(len(reop), withfix)} of issues with a merged closing PR; still open now {sum(1 for r in reop if r[1]=='OPEN')}; e.g. {reop[:8]}")
allreop = sum(1 for i in I if any(e["__typename"] == "ReopenedEvent" for e in i["timelineItems"]["nodes"]))
print(f"         any reopen at all: {pct(allreop, len(I))}")

# ---- ticket key on merged human PRs
REF = re.compile(r"\b(fix(?:es|ed)?|close[sd]?|resolve[sd]?)\b\s*:?\s*(?:#\d+|https://github\.com/home-assistant/core/issues/\d+)", re.I)
ANY = re.compile(r"(?<![\w/])#\d{3,}|github\.com/home-assistant/core/issues/\d+")
hm = [p for p in merged if not is_bot(p["author"])]
def strip(b): return re.sub(r"<!--.*?-->", "", b or "", flags=re.S)
kw = [p for p in hm if REF.search(strip(p["body"])) or p["closingIssuesReferences"]["nodes"]]
an = [p for p in hm if ANY.search(strip(p["body"])) or p["closingIssuesReferences"]["nodes"]]
cir = [p for p in hm if p["closingIssuesReferences"]["nodes"]]
print(f"\nTICKET KEY  merged human PRs N={len(hm)}: closing keyword or closingIssuesReferences {pct(len(kw), len(hm))}; closingIssuesReferences alone {pct(len(cir), len(hm))}; any #N / issue URL in body {pct(len(an), len(hm))}")
for lab in ("Bug fix", "bugfix"):
    pass
tl = collections.Counter(l["name"] for p in hm for l in p["labels"]["nodes"] if l["name"] in ("bugfix", "new-feature", "code-quality", "dependency", "breaking-change", "new-integration", "small-pr"))
bug = [p for p in hm if any(l["name"] == "bugfix" for l in p["labels"]["nodes"])]
print(f"   PR type labels {dict(tl)}; among 'bugfix' PRs with closing ref: {pct(sum(1 for p in bug if p in kw), len(bug))}")

# ---- labels / quality table
print("\nQUALITY (issues created in window)")
def bulk_actors():
    hours = collections.Counter()
    for i in I:
        for e in closes(i):
            hours[((e.get("actor") or {}).get("login"), e["createdAt"][:13])] += 1
    return {k: v for k, v in hours.items() if v >= 200}
B = bulk_actors(); print(f"  bulk closes (>=200 in one clock hour by one actor, window issues only): {B or 'none'}")
mx = max(collections.Counter(((e.get('actor') or {}).get('login'), e['createdAt'][:13]) for i in I for e in closes(i)).items(), key=lambda x: x[1])
print(f"  max closes by one actor in one hour: {mx}")
dup = [i for i in I if i["stateReason"] == "DUPLICATE" or any(l["name"] == "duplicate" for l in i["labels"]["nodes"])]
dupl = [i for i in dup if any(e["__typename"] == "MarkedAsDuplicateEvent" and e.get("canonical") for e in i["timelineItems"]["nodes"])]
print(f"  duplicates (stateReason DUPLICATE or label): {len(dup)}; with original linked (MarkedAsDuplicateEvent): {pct(len(dupl), len(dup))}")
def lab(i, n): return any(l["name"] == n for l in i["labels"]["nodes"])
stale_closed = [i for i in closed if cls[i["number"]] == "stale-bot"]
np_ = [i for i in closed if i["stateReason"] == "NOT_PLANNED"]
tbl = [("All issues", I), ("Closed", closed), ("NOT_PLANNED (any closer)", np_), ("closed by stale bot", stale_closed),
       ("`stale` label (now)", [i for i in I if lab(i, "stale")]), ("`needs-more-information`", [i for i in I if lab(i, "needs-more-information")]),
       ("DUPLICATE / `duplicate`", dup), ("COMPLETED + merged PR", [i for i in closed if cls[i['number']] == "COMPLETED + merged PR"]),
       ("COMPLETED, no PR (human)", [i for i in closed if cls[i['number']] == "COMPLETED, no PR"]), ("bot-command close (home-assistant)", [i for i in closed if closes(i) and ((closes(i)[-1].get('actor') or {}).get('login') == 'home-assistant')])]
print("  | Group | Total | bot or bulk closed | usable (human close / still open) |")
for name, g in tbl:
    bb = sum(1 for i in g if i["state"] == "CLOSED" and closes(i) and is_bot((closes(i)[-1].get("actor"))))
    print(f"  | {name} | {len(g)} | {bb} | {len(g)-bb} |")
