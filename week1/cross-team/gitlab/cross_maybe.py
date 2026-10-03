"""Second look at the "possible" cross-team blocks: add facts about each blocker and about the waiting issue's code.
For each possible row in cross_hand_check_116.csv:
  - blocker refs (#N, !N, group/project#N, group/project!N) -> state, closed/merged date, group:: labels, author, assignees
  - waiting issue -> author, assignees, its blocked spells, and its related merge requests with merge dates
Token from ~/.gitlab_token, never printed. Writes cross_maybe/batchN.txt (facts + the earlier timeline) for re-reading."""
import csv, json, os, re, time, urllib.request, urllib.error, urllib.parse
T = open(os.path.expanduser("~/.gitlab_token")).read().strip()
API = "https://gitlab.com/api/v4"
def get(path):
    for a in range(5):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(API + path, headers={"PRIVATE-TOKEN": T}), timeout=60))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404): return None
            time.sleep(3 * (a + 1))
        except Exception: time.sleep(3 * (a + 1))
    return None
rows = [x for x in csv.DictReader(open("cross_hand_check_116.csv")) if x["verdict"] == "possible"]
labs = {json.loads(l)["iid"]: json.loads(l)["events"] for l in open("labels_all.jsonl")}
REF = re.compile(r"((?:[\w.-]+/)+[\w.-]+)?([#!])(\d+)")
grp = lambda ls: [l for l in (ls or []) if l.startswith(("group::", "devops::"))]
who = lambda o: (o or {}).get("username")
def blocker(proj, kind, n):
    enc = urllib.parse.quote(proj, safe="")
    d = get(f"/projects/{enc}/{'issues' if kind == '#' else 'merge_requests'}/{n}")
    if not d: return f"{proj}{kind}{n}: not visible"
    end = d.get("merged_at") or d.get("closed_at")
    return (f"{proj}{kind}{n}: {d.get('state')} | created {d['created_at'][:10]} | done {str(end)[:10]} | "
            f"labels {grp(d.get('labels'))} | author {who(d.get('author'))} | assignees {[who(a) for a in d.get('assignees') or []]} | {d.get('title','')[:70]}")
dumps = {}
for f in sorted(os.listdir("cross_dump")):
    if f.startswith("batch"):
        for block in open(os.path.join("cross_dump", f)).read().split("\n######## #")[1:]:
            dumps[block.split(" ", 1)[0]] = "######## #" + block
os.makedirs("cross_maybe", exist_ok=True)
out = []
for x in rows:
    i = x["iid"]; me = get(f"/projects/278964/issues/{i}") or {}
    mrs = get(f"/projects/278964/issues/{i}/related_merge_requests") or []
    spells, t0 = [], None
    for e in sorted(labs.get(i) or [], key=lambda e: e["t"]):
        if e["label"] == "workflow::blocked":
            if e["action"] == "add" and t0 is None: t0 = e["t"][:10]
            elif e["action"] == "remove" and t0: spells.append(f"{t0} -> {e['t'][:10]}"); t0 = None
    if t0: spells.append(f"{t0} -> still blocked")
    refs = {(p or "gitlab-org/gitlab", k, n) for p, k, n in REF.findall(x["waited_on_ref"])}
    facts = [f"FACTS for #{i} (earlier verdict: possible; other_team={x['other_team']} order_ok={x['order_ok']} written_wait={x['written_wait']})",
             f"  waiting issue: group {grp(me.get('labels'))} | author {who(me.get('author'))} | assignees {[who(a) for a in me.get('assignees') or []]} | state {me.get('state')} closed {str(me.get('closed_at'))[:10]}",
             f"  blocked spells: {spells}",
             "  related MRs: " + "; ".join(f"!{m['iid']} {m['state']} merged {str(m.get('merged_at'))[:10]} by {who(m.get('author'))}" for m in mrs[:8])]
    for r in sorted(refs)[:5]: facts.append("  blocker " + blocker(*r)); time.sleep(0.2)
    facts.append(f"  earlier evidence: {x['evidence']}")
    out.append("\n".join(facts) + "\n" + dumps.get(i, "(timeline missing)"))
    time.sleep(0.2)
for b in range(0, len(out), 15):
    open(f"cross_maybe/batch{b // 15}.txt", "w").write("\n\n".join(out[b:b + 15]))
print("possible rows", len(rows), "| batches", (len(out) + 14) // 15)
