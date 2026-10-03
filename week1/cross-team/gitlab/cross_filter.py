"""Targeted cross-team pass, step 1b: flag blocked issues likely to be waiting on ANOTHER team.
Signals (any one is enough):
  A  a "blocked by" ever recorded (system notes, so deleted links count) points to another project
  B  ... or to a gitlab-org/gitlab issue whose group:: label differs from this issue's
  C  a human comment says it waits (block/wait/depend/until/once/pending) AND names another team:
     a ~group::/~devops:: label other than its own, an @gitlab-org/... or @gitlab-com/... group handle,
     a link to another project's issue/MR, or a team word (database team, security, infra, SRE, ...)
Writes cross_candidates.json and prints counts."""
import json, re, collections, urllib.request, time
iss = {json.loads(l)["iid"]: json.loads(l) for l in open("issues.jsonl")}
notes = {json.loads(l)["iid"]: json.loads(l)["notes"] for l in open("cross_notes.jsonl")}
grp = lambda labels: {l.split("::", 1)[1] for l in labels if l.startswith("group::")}
own = {i: grp(l["title"] for l in iss[i]["labels"]["nodes"]) for i in notes}
REF = re.compile(r"((?:[\w.-]+/)+[\w.-]+)?#(\d+)")
blockers = {}
for i, ns in notes.items():
    refs = set()
    for n in ns:
        if n["system"] and re.search(r"marked this (issue|item|task) as blocked by", n["body"]):
            tail = n["body"].split("blocked by", 1)[1]
            refs |= {(p or "gitlab-org/gitlab", int(k)) for p, k in REF.findall(tail)}
    blockers[i] = refs
same = sorted({k for r in blockers.values() for p, k in r if p == "gitlab-org/gitlab"})
Q = """query($i:[String!]){project(fullPath:"gitlab-org/gitlab"){issues(iids:$i,first:100){nodes{iid labels(first:100){nodes{title}}}}}}"""
bg = {}
for k in range(0, len(same), 100):
    b = json.dumps({"query": Q, "variables": {"i": [str(x) for x in same[k:k + 100]]}}).encode()
    d = json.load(urllib.request.urlopen(urllib.request.Request("https://gitlab.com/api/graphql", b, {"Content-Type": "application/json"}), timeout=60))
    for n in d["data"]["project"]["issues"]["nodes"]: bg[int(n["iid"])] = grp(l["title"] for l in n["labels"]["nodes"])
    time.sleep(0.3)
WAIT = re.compile(r"\b(block\w*|wait\w*|depend\w*|until|once|pending|prerequisite)\b", re.I)
TEAMWORD = re.compile(r"\b(database team|db team|dba|security team|appsec|infrastructure|infra team|sre|delivery team|release (team|managers?)|distribution team|ux team|design system|pajamas|legal|technical writ\w+|docs team|quality team|qa team|support team|product security|compliance team|data team|finance)\b", re.I)
LABEL = re.compile(r'~"?(?:group|devops)::([^"\s~]+(?: [^"~\n]+?)?)"?(?=[\s,.)]|$)')
HANDLE = re.compile(r"@(gitlab-(?:org|com)/[\w./-]+)")
OTHERLINK = re.compile(r"gitlab\.com/((?:gitlab-org|gitlab-com)/[\w./-]+?)/-/(?:issues|merge_requests|work_items|epics)/\d+")
BOTS = ("gitlab-bot", "cogbot", "triage")
cand = {}; why = collections.Counter()
for i, ns in notes.items():
    sig = []
    for p, k in blockers[i]:
        if p != "gitlab-org/gitlab": sig.append(f"A:{p}#{k}")
        elif bg.get(k) and own[i] and not (bg[k] & own[i]): sig.append(f"B:#{k} {sorted(bg[k])}")
    for n in ns:
        if n["system"] or any(b in n["author"] for b in BOTS): continue
        t = n["body"]
        if not WAIT.search(t): continue
        lab = {x.strip().lower() for x in LABEL.findall(t)} - {g.lower() for g in own[i]}
        links = {p for p in OTHERLINK.findall(t) if p != "gitlab-org/gitlab"}
        hits = [f"label:{x}" for x in lab] + [f"handle:{h}" for h in HANDLE.findall(t)] + [f"link:{p}" for p in links] + [f"word:{w[0] if isinstance(w, tuple) else w}" for w in TEAMWORD.findall(t)]
        if hits: sig.append("C:" + ",".join(sorted(set(hits)))[:200])
    if sig:
        cand[i] = sorted(set(sig))
        for s in set(x[0] for x in sig): why[s] += 1
print("blocked issues", len(notes), "| ever had a 'blocked by' record", sum(bool(v) for v in blockers.values()))
print("candidates", len(cand), "| by signal", dict(why))
print("A only", sum(1 for v in cand.values() if {x[0] for x in v} == {"A"}), "| B only", sum(1 for v in cand.values() if {x[0] for x in v} == {"B"}), "| C only", sum(1 for v in cand.values() if {x[0] for x in v} == {"C"}), "| 2+ signals", sum(1 for v in cand.values() if len({x[0] for x in v}) > 1))
print("other projects named in A", collections.Counter(s.split(":")[1].split("#")[0] for v in cand.values() for s in v if s.startswith("A")).most_common(10))
json.dump(cand, open("cross_candidates.json", "w"), indent=1)
