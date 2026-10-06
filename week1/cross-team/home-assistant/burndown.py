"""Monthly burndown of one deprecated symbol across integrations, from first-parent snapshots.
Usage (from the clone): python3 burndown.py <regex> <start YYYY-MM> <end YYYY-MM>  -> burndown_<n>.json"""
import json, re, subprocess, sys, collections
pat, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
def sh(*a): return subprocess.run(a, capture_output=True, text=True).stdout
y, m = map(int, start.split("-")); ey, em = map(int, end.split("-")); out = []
while (y, m) <= (ey, em):
    d = f"{y:04d}-{m:02d}-01T00:00:00Z"
    c = sh("git", "rev-list", "-1", "--first-parent", f"--before={d}", "dev").strip()
    sites = collections.Counter(); tests = 0
    for line in sh("git", "grep", "-c", "-P", pat, c, "--", "homeassistant", "tests").splitlines():
        path, n = line.split(":", 1)[1].rsplit(":", 1)
        if path.startswith("tests/"): tests += int(n); continue
        mm = re.match(r"homeassistant/components/([^/]+)/", path)
        sites[mm.group(1) if mm else "(core) " + path] += int(n)
    out.append({"month": d[:7], "commit": c, "sites": sum(sites.values()), "test_sites": tests, "per_integration": dict(sites)})
    print(d[:7], c[:10], "integrations", len(sites), "sites", sum(sites.values()), "tests", tests, flush=True)
    m += 1
    if m == 13: y, m = y + 1, 1
json.dump(out, open("burndown.json", "w"))
