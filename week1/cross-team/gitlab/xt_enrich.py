"""Add blocker (Y) team labels and dates, classify cross-team, apply the order check. Writes candidates.json."""
import json, time, urllib.request, collections
PROJ = "gitlab-org/gitlab"
E = json.load(open("edges.json"))
I = {}
for l in open("issues.jsonl", encoding="utf-8"):
    i = json.loads(l); I[i["iid"]] = i
def gql(q, v):
    body = json.dumps({"query": q, "variables": v}).encode()
    for a in range(6):
        try:
            d = json.load(urllib.request.urlopen(urllib.request.Request("https://gitlab.com/api/graphql", body, {"Content-Type": "application/json"}), timeout=90))
            if "errors" in d and not d.get("data"): raise RuntimeError(str(d["errors"])[:200])
            return d["data"]
        except Exception as e:
            print("retry", a, e); time.sleep(5 * (a + 1))
    raise SystemExit("giving up")
F = "iid title createdAt closedAt state webUrl labels(first:100){ nodes{ title } }"
Y = {}  # (proj, iid) -> issue
need = collections.defaultdict(set)
for e in E:
    k = (e["y_proj"], e["y"])
    if e["y_proj"] == PROJ and e["y"] in I: Y[k] = I[e["y"]]
    else: need[e["y_proj"]].add(e["y"])
for p, iids in need.items():
    iids = sorted(iids)
    for s in range(0, len(iids), 50):
        d = gql('query($p:ID!,$i:[String!]){ project(fullPath:$p){ issues(iids:$i, first:50){ nodes{ %s } } } }' % F, {"p": p, "i": iids[s:s+50]})
        for n in ((d.get("project") or {}).get("issues") or {}).get("nodes") or []: Y[(p, n["iid"])] = n
groups = lambda i: {l["title"] for l in i["labels"]["nodes"] if l["title"].startswith("group::")} if i else set()
out, c = [], collections.Counter()
for e in E:
    x, y = I[e["x"]], Y.get((e["y_proj"], e["y"]))
    gx, gy = groups(x), groups(y)
    if y is None: team = "blocker not visible"
    elif e["y_proj"] != PROJ and not gy: team = "other project"
    elif not gx or not gy: team = "no group label"
    elif gx & gy: team = "same"
    else: team = "cross"
    at = e["added_at"]
    if y is None: order = "unknown"
    elif y["createdAt"] > at: order = "blocker created after link"
    elif y.get("closedAt") and y["closedAt"] < at: order = "blocker already closed"
    elif x.get("closedAt") and (not y.get("closedAt") or x["closedAt"] < y["closedAt"]): order = "X closed before blocker"
    else: order = "ok"
    c[(team, order)] += 1
    out.append({**e, "team": team, "order": order, "x_groups": sorted(gx), "y_groups": sorted(gy),
                "x_title": x["title"], "y_title": y["title"] if y else None, "x_closed": x.get("closedAt"),
                "y_created": y["createdAt"] if y else None, "y_closed": y.get("closedAt") if y else None,
                "y_url": (y.get("webUrl") or f"https://gitlab.com/{e['y_proj']}/-/issues/{e['y']}") if y else None})
json.dump(out, open("candidates.json", "w"), indent=1)
for k, v in sorted(c.items(), key=lambda kv: -kv[1]): print(v, k)
