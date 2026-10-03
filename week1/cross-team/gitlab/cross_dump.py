"""Targeted cross-team pass, step 2a: readable timeline per candidate (description, human comments,
block/label/state events, blocker teams, filter signals). Usage: cross_dump.py cross_toread.txt <batch size> -> cross_dump/batchN.txt"""
import json, os, re, sys, urllib.request, time
ids = open(sys.argv[1]).read().split(); size = int(sys.argv[2])
iss = {json.loads(l)["iid"]: json.loads(l) for l in open("issues.jsonl")}
notes = {json.loads(l)["iid"]: json.loads(l)["notes"] for l in open("cross_notes.jsonl")}
labs = {json.loads(l)["iid"]: json.loads(l)["events"] for l in open("labels_all.jsonl")}
cand = json.load(open("cross_candidates.json"))
Q = """query($i:[String!]){project(fullPath:"gitlab-org/gitlab"){issues(iids:$i,first:100){nodes{iid title description state}}}}"""
meta = {}
for k in range(0, len(ids), 100):
    b = json.dumps({"query": Q, "variables": {"i": ids[k:k + 100]}}).encode()
    d = json.load(urllib.request.urlopen(urllib.request.Request("https://gitlab.com/api/graphql", b, {"Content-Type": "application/json"}), timeout=60))
    for n in d["data"]["project"]["issues"]["nodes"]: meta[n["iid"]] = n
    time.sleep(0.3)
KEEP = re.compile(r"blocked by|blocking|status to|moved to|closed|reopened|relation")
os.makedirs("cross_dump", exist_ok=True)
for b in range(0, len(ids), size):
    with open(f"cross_dump/batch{b // size}.txt", "w") as f:
        for i in ids[b:b + size]:
            m = meta.get(i, {}); own = [l["title"] for l in iss[i]["labels"]["nodes"] if l["title"].startswith(("group::", "devops::", "section::"))]
            f.write(f"\n######## #{i} [{m.get('state')}] {m.get('title')}\n url https://gitlab.com/gitlab-org/gitlab/-/issues/{i}\n own labels: {own}\n filter signals: {cand.get(i)}\n")
            f.write(" desc: " + re.sub(r"\s+", " ", re.sub(r"<!--.*?-->", "", m.get("description") or "", flags=re.S))[:900] + "\n")
            ev = [(e["t"], e["user"], f"{e['action']} label {e['label']}") for e in labs.get(i) or [] if (e["label"] or "").startswith("workflow::")]
            for n in notes[i]:
                body = re.sub(r"\s+", " ", n["body"])
                if n["system"]:
                    if KEEP.search(body): ev.append((n["t"], n["author"], "· " + body[:200]))
                elif "gitlab-bot" not in n["author"] and "triage" not in n["author"]:
                    ev.append((n["t"], n["author"], "💬 " + body[:600]))
            for t, u, x in sorted(ev): f.write(f"  {t[:10]} {u:<20} {x}\n")
print("batches", (len(ids) + size - 1) // size)
