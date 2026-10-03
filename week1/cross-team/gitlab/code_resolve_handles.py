"""Resolve CODEOWNERS handles.
  1) handles without '/': GET /users?username= ; if empty, GET /groups/:name -> top-level group (data/toplevel_groups.txt),
     else NOT_FOUND (blocked/deleted user) -> data/user_handles.json
  2) every group handle: GET /groups/:path/members (DIRECT members only, paginated) with the token in ~/.gitlab_token
     (members API is 401 anonymously; 404 = group not visible to this token -> None). Throttled to ~1 req/s. Writes data/group_members.json {group: [usernames]}.
Rerun: python3 code_resolve_handles.py && python3 code_codeowners.py"""
import json, os, time, urllib.request, urllib.parse, urllib.error
OUT = os.environ.get("OUT", "data/code")
API = "https://gitlab.com/api/v4"
TOKEN = open(os.path.expanduser("~/.gitlab_token")).read().strip()


def get(url, auth=False):
    for a in range(6):
        h = {"User-Agent": "meridian-study"}
        if auth: h["PRIVATE-TOKEN"] = TOKEN
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60) as r:
                time.sleep(1.0 if auth else 0.2)
                return json.load(r), r.headers
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500: time.sleep(15 * (a + 1)); continue
            return None, {}
        except Exception:
            time.sleep(10)
    return None, {}


handles = [h.strip() for h in open(f"{OUT}/data/handles.txt") if h.strip()]
import code_codeowners as co
secs = co.parse()
handles = sorted({o for s in secs for r in s["rules"] for o in r["owners"]} | {o for s in secs for o in s["default"]})
tops, users = [], {}
for h in handles:
    n = h.lstrip("@")
    if "/" in n or "@" in n: continue
    d, _ = get(f"{API}/users?username={urllib.parse.quote(n)}", auth=True)
    if d: users[n] = d[0].get("state"); continue
    g, _ = get(f"{API}/groups/{urllib.parse.quote(n)}", auth=True)
    if g: tops.append(n.lower())
    else: users[n] = "NOT_FOUND"   # no such visible user or group: blocked/deleted account
open(f"{OUT}/data/toplevel_groups.txt", "w").write("\n".join(tops))
json.dump(users, open(f"{OUT}/data/user_handles.json", "w"), indent=1)
print("users", len(users), "not found", [u for u, v in users.items() if v == "NOT_FOUND"], "top-level groups", tops)
groups = [h.lstrip("@") for h in handles if "/" in h or h.lstrip("@").lower() in tops]
members = {}
for g in groups:
    mem, page = [], 1
    while True:
        d, hd = get(f"{API}/groups/{urllib.parse.quote(g, safe='')}/members?per_page=100&page={page}", auth=True)
        if d is None: mem = None; break
        mem += [m["username"] for m in d]
        if not hd.get("X-Next-Page"): break
        page += 1
    members[g] = mem
    print(g, None if mem is None else len(mem), flush=True)
json.dump(members, open(f"{OUT}/data/group_members.json", "w"), indent=1)
