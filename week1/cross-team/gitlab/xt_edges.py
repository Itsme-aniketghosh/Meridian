"""Build 'X blocked by Y' edges for human-filed issues X in the window, from activity notes. Writes edges.json."""
import json, re, collections
PROJ = "gitlab-org/gitlab"
REF = re.compile(r"(?:https://gitlab\.com/)?([\w.-]+(?:/[\w.-]+)*)?(?:/-/(?:issues|work_items)/|#)(\d+)")
I = {}
for l in open("issues.jsonl", encoding="utf-8"):
    i = json.loads(l); I[i["iid"]] = i
human = set(open("human_iids.txt").read().split())
BOT = lambda u: "_bot_" in u or u.endswith("-bot") or u in ("gitlab-bot",)
edges = {}  # (x_proj, x_iid, y_proj, y_iid) -> {added_at, added_by, removed_at}
def ref(s):
    m = REF.search(s)
    return ((m.group(1) or PROJ), m.group(2)) if m else None
import glob
for l in (l for f in sorted(glob.glob("activity*.jsonl")) for l in open(f, encoding="utf-8")):
    n = json.loads(l); me = n["iid"]
    for x in n["notes"]["nodes"]:
        b, who, at = x["body"], (x["author"] or {}).get("username", ""), x["createdAt"]
        if b.startswith("marked this issue as blocked by "):
            r = ref(b[len("marked this issue as blocked by "):]); k = (PROJ, me, *r) if r else None
        elif b.startswith("marked this issue as blocking "):
            r = ref(b[len("marked this issue as blocking "):]); k = (*r, PROJ, me) if r else None
        else:
            k = None
        if k:
            e = edges.setdefault(k, {"added_at": at, "added_by": who, "bot": BOT(who)})
            if at < e["added_at"]: e.update(added_at=at, added_by=who, bot=BOT(who))
        if b.startswith("removed the relation with"):
            pass
# keep edges whose blocked side X is a human issue in our window
keep = {k: v for k, v in edges.items() if k[0] == PROJ and k[1] in human}
print("edges total", len(edges), "| X in window & human", len(keep), "| distinct blocked X", len({k[1] for k in keep}))
print("blocker project:", collections.Counter("same" if k[2] == PROJ else "other" for k in keep))
print("added by bot:", sum(v["bot"] for v in keep.values()))
json.dump([{"x": k[1], "y_proj": k[2], "y": k[3], **v} for k, v in sorted(keep.items())], open("edges.json", "w"), indent=0)
