"""Fetch merged MRs from gitlab-org/gitlab (anonymous REST, monthly merged_after/merged_before windows, 3-6 workers,
per-month cache in data/mr_months/ so reruns resume).
  python3 code_fetch_mrs.py            -> type::bug MRs merged 2024-10..2026-09 -> $OUT/data/bug_mrs.jsonl
  python3 code_fetch_mrs.py all        -> ALL MRs merged 2025-04..2026-09      -> $OUT/data/all_mrs.jsonl"""
import sys
MODE = sys.argv[1] if len(sys.argv) > 1 else "bug"
START = (2024, 10) if MODE == "bug" else (2025, 4)
import json, os, time, urllib.request, urllib.parse, urllib.error
from concurrent.futures import ThreadPoolExecutor
OUT = os.environ.get("OUT", "data/code")
API = "https://gitlab.com/api/v4/projects/278964/merge_requests"
KEEP = ["iid", "title", "merged_at", "created_at", "merge_commit_sha", "squash_commit_sha", "sha", "labels", "target_branch"]
def get(url):
    for a in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "meridian-study"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r), r.headers
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504): time.sleep(10 * (a + 1)); continue
            raise
        except Exception:
            time.sleep(10 * (a + 1))
    raise RuntimeError(url)
def months():
    y, m = START
    while (y, m) < (2026, 10):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        yield f"{y}-{m:02d}-01T00:00:00Z", f"{ny}-{nm:02d}-01T00:00:00Z"
        y, m = ny, nm
def window(w):
    a, b = w; rows = []; page = 1
    cache = f"{OUT}/data/mr_months/{MODE}_{a[:7]}.jsonl"
    if os.path.exists(cache):
        return [json.loads(l) for l in open(cache)]
    while True:
        p = {"state": "merged", "merged_after": a, "merged_before": b,
                                    "per_page": 100, "page": page, "order_by": "created_at", "sort": "asc"}
        if MODE == "bug": p["labels"] = "type::bug"
        q = urllib.parse.urlencode(p)
        data, h = get(f"{API}?{q}")
        for m in data:
            r = {k: m.get(k) for k in KEEP}; r["author"] = (m.get("author") or {}).get("username"); rows.append(r)
        if not data or not h.get("X-Next-Page"): break
        page += 1; time.sleep(0.3)
    with open(cache + ".tmp", "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")
    os.replace(cache + ".tmp", cache)
    print(a[:7], len(rows), flush=True)
    return rows
os.makedirs(f"{OUT}/data/mr_months", exist_ok=True)
with ThreadPoolExecutor(3 if MODE == "bug" else 6) as ex:
    allrows = [r for rows in ex.map(window, list(months())) for r in rows]
seen = {}
for r in allrows: seen[r["iid"]] = r
with open(f"{OUT}/data/{MODE}_mrs.jsonl", "w") as f:
    for r in seen.values(): f.write(json.dumps(r) + "\n")
print("done", len(seen))
