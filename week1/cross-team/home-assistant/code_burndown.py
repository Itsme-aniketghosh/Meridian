"""#1 burndown for each curated deprecation (code_specs.SPECS): monthly first-parent snapshots 2022-01..removal+3mo,
sites per integration (homeassistant/components, non-test), test sites, core sites. Run: python3 code_burndown.py -> $SC/burndown_<name>.json"""
import json, re, subprocess, collections, sys
from multiprocessing import Pool
from code_specs import R, SC, SPECS
def sh(*a): return subprocess.run(["git", "-C", R, *a], capture_output=True, text=True).stdout
def months(a, b):
    y, m = map(int, a.split("-")[:2]); ey, em = map(int, b.split("-")[:2])
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"; m += 1
        if m == 13: y, m = y + 1, 1
def snap(args):
    name, pat, mo = args
    c = sh("rev-list", "-1", "--first-parent", f"--before={mo}-01T00:00:00Z", "dev").strip()
    sites = collections.Counter(); tests = collections.Counter(); core = 0
    for line in sh("grep", "-c", "-P", pat, c, "--", "homeassistant", "tests").splitlines():
        path, n = line.split(":", 1)[1].rsplit(":", 1); n = int(n)
        if (m := re.match(r"tests/components/([^/]+)/", path)): tests[m.group(1)] += n
        elif path.startswith("tests/"): tests["(core)"] += n
        elif (m := re.match(r"homeassistant/components/([^/]+)/", path)): sites[m.group(1)] += n
        else: core += n
    return name, {"month": mo, "commit": c, "sites": sum(sites.values()), "integrations": len(sites), "test_sites": sum(tests.values()),
                  "core_sites": core, "per_integration": dict(sites), "tests_per_integration": dict(tests)}
if __name__ == "__main__":
    names = sys.argv[1:] or list(SPECS)
    jobs = []
    for n in names:
        pat, dep, rem = SPECS[n][:3]; y, m = map(int, rem.split("-")[:2]); m += 4; y += (m - 1) // 12; m = (m - 1) % 12 + 1
        jobs += [(n, pat, mo) for mo in months("2022-01", f"{y}-{m:02d}")]
    out = collections.defaultdict(list)
    with Pool(6) as p:
        for n, r in p.imap(snap, jobs): out[n].append(r)
    for n, rows in out.items():
        rows.sort(key=lambda r: r["month"]); json.dump(rows, open(f"{SC}/burndown_{n}.json", "w"))
        pk = max(rows, key=lambda r: r["sites"]); zero = next((r["month"] for r in rows if r["month"] > pk["month"] and r["sites"] == 0), None)
        pi = max(r["integrations"] for r in rows)
        print(f"{n}: peak {pk['sites']} sites/{pk['integrations']} integ @ {pk['month']} (max integ {pi}); first month with 0 = {zero}; dep {SPECS[n][1]} rem {SPECS[n][2]}", flush=True)
