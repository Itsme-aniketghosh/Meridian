"""For cross-team candidates that pass the order check, pull X's human comments and keep snippets
that mention the blocker, its team, or waiting. Writes evidence.json and evidence.md (for reading)."""
import json, re, subprocess, sys
C = [c for c in json.load(open("candidates.json")) if c["team"] in ("cross", "other project") and c["order"] == "ok"]
xs = sorted({c["x"] for c in C}, key=int)
open("cand_x.txt", "w").write("\n".join(xs))
subprocess.run([sys.executable, "xt_pull_notes.py", "cand_x.txt", "comments.jsonl", "ONLY_COMMENTS"], check=True)
N = {json.loads(l)["iid"]: json.loads(l)["notes"]["nodes"] for l in open("comments.jsonl", encoding="utf-8")}
WAIT = re.compile(r"\b(wait(ing|s|ed)? (for|on|until)|blocked (by|on|until)|blocker|depends on|dependency|once .{0,40}(lands?|merged?|ships?|done|ready|released)|until .{0,40}(is|are) (done|merged|ready|fixed)|unblock)", re.I)
BOT = lambda u: "_bot_" in u or u.endswith("-bot") or u == "gitlab-bot"
out = []
for c in C:
    yref = re.compile(r"(#%s\b|/%s\b)" % (c["y"], c["y"]))
    teams = [g.split("::", 1)[1] for g in c["y_groups"]]
    hits = []
    for n in N.get(c["x"], []):
        u = (n["author"] or {}).get("username", "")
        if n["system"] or BOT(u): continue
        b = n["body"]
        why = [w for w, ok in (("ref", yref.search(b)), ("wait", WAIT.search(b)), ("team", any(t.lower() in b.lower() for t in teams))) if ok]
        if why:
            m = (yref.search(b) or WAIT.search(b))
            s = max(0, (m.start() if m else 0) - 150)
            hits.append({"at": n["createdAt"][:10], "by": u, "why": why, "text": " ".join(b[s:s+350].split())})
    out.append({**c, "n_comments": len(N.get(c["x"], [])), "hits": hits[:5]})
json.dump(out, open("evidence.json", "w"), indent=1)
with open("evidence.md", "w", encoding="utf-8") as f:
    for k, c in enumerate(out, 1):
        f.write(f"## {k}. #{c['x']} {c['x_groups']} blocked by {c['y_proj']}#{c['y']} {c['y_groups']}\n")
        f.write(f"X: {c['x_title'][:90]} | closed {str(c['x_closed'])[:10]}\nY: {str(c['y_title'])[:90]} | {str(c['y_created'])[:10]} -> {str(c['y_closed'])[:10]}\n")
        f.write(f"link added {c['added_at'][:10]} by {c['added_by']} | {c['n_comments']} human comments\n")
        for h in c["hits"]: f.write(f"- {h['at']} {h['by']} [{','.join(h['why'])}] {h['text']}\n")
        f.write("\n")
print(len(out), "candidates with evidence;", sum(bool(c["hits"]) for c in out), "have at least one relevant snippet")
