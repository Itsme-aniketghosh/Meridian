"""Targeted cross-team pass, step 1a: every note (comments + system notes) for each human-filed issue
that was ever workflow::blocked. Token from ~/.gitlab_token, never printed. Writes cross_notes.jsonl."""
import json, os, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
T = open(os.path.expanduser("~/.gitlab_token")).read().strip()
B = "https://gitlab.com/api/v4/projects/278964/issues/"
TESTBOT = "project_278964_bot_87c17d71a842955abfceaf361a49f249"
au = {json.loads(l)["iid"]: json.loads(l)["author"] for l in open("authors.jsonl")}
blocked = sorted({r["iid"] for r in map(json.loads, open("labels_all.jsonl")) if r["events"]
                  and any(e["label"] == "workflow::blocked" and e["action"] == "add" for e in r["events"])
                  and au.get(r["iid"]) != TESTBOT}, key=int)
def get(u):
    out, p = [], 1
    while True:
        for a in range(6):
            try:
                r = urllib.request.urlopen(urllib.request.Request(f"{u}&per_page=100&page={p}", headers={"PRIVATE-TOKEN": T}), timeout=60)
                d = json.load(r); break
            except urllib.error.HTTPError as e:
                if e.code == 404: return []
                time.sleep(10 * (a + 1) if e.code == 429 else 3 * (a + 1))
            except Exception: time.sleep(3 * (a + 1))
        else: raise RuntimeError("giving up on " + u)
        out += d
        if not r.headers.get("x-next-page"): return out
        p += 1
done = {json.loads(l)["iid"] for l in open("cross_notes.jsonl")} if os.path.exists("cross_notes.jsonl") else set()
lock = threading.Lock(); f = open("cross_notes.jsonl", "a")
def work(i):
    ns = get(B + i + "/notes?sort=asc&order_by=created_at")
    row = {"iid": i, "notes": [{"t": n["created_at"], "system": n["system"], "author": n["author"]["username"], "body": n["body"]} for n in ns]}
    with lock: f.write(json.dumps(row) + "\n")
with ThreadPoolExecutor(6) as ex: list(ex.map(work, [i for i in blocked if i not in done]))
f.close(); print("blocked issues", len(blocked), file=sys.stderr)
