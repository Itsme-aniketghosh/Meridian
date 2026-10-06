"""#1 'found out late': for the HACS sample cloned by code_hacs_clone.py, per removed core deprecation: does the custom integration use
the symbol (non-test .py) at HEAD, at the core deprecation date, at the core removal date; and the date of its fixing commit (oldest commit
after which the use count stays 0, found via git log -G). Run: python3 code_hacs_usage.py -> code_hacs_usage.csv + summary"""
import json, subprocess, re, csv, statistics, datetime as dt, collections
from code_specs import SC, SPECS, HERE
PICK = ["async_forward_entry_setup", "TEMP_consts", "hass_helpers"]  # unit_consts dropped: names like TIME_SECONDS collide with custom repos' own constants
def git(d, *a): return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True).stdout
def count(d, rev, pat):
    out = git(d, "grep", "-c", "-P", pat, rev, "--", "*.py", ":!*test*")
    return sum(int(l.rsplit(":", 1)[1]) for l in out.splitlines() if l.strip())
sample = [r for r in json.load(open(f"{SC}/hacs_sample.json"))]
rows = []
for s in sample:
    if s["status"] != "ok": continue
    d = f"{SC}/hacs/{s['repo'].replace('/', '__')}"
    first = git(d, "log", "--reverse", "--format=%cI").split("\n")[0][:10]; head = git(d, "log", "-1", "--format=%cI")[:10]
    shallow = git(d, "rev-parse", "--is-shallow-repository").strip() == "true"
    for name in PICK:
        pat, dep, rem = SPECS[name][:3]
        r = {"repo": s["repo"], "deprecation": name, "first_commit_in_clone": first, "head_date": head, "shallow": shallow}
        r["uses_head"] = count(d, "HEAD", pat)
        for tag, date in (("at_dep", dep), ("at_rem", rem)):
            c = git(d, "rev-list", "-1", f"--before={date}T00:00:00Z", "HEAD").strip()
            # no commit before date in clone: unknown if repo history (or shallow boundary) starts after date
            r["uses_" + tag] = count(d, c, pat) if c else ""
        r["fix_date"] = ""
        if r["uses_head"] == 0:
            ere = pat.replace(r"(?<![.\w])", "").replace(r"\b", "")
            cs = git(d, "log", f"-G{ere}", "--format=%H %cI", "--", "*.py", ":!*test*").split("\n")
            for line in [x for x in cs if x]:  # newest first; the newest -G commit that leaves count 0 is the fix
                h, cd = line.split(); 
                if count(d, h, pat) == 0 and git(d, "rev-parse", "-q", "--verify", h + "^") and count(d, h + "^", pat) > 0: r["fix_date"] = cd[:10]  # real removal, not a root/boundary commit
                break
        r["fix_days_after_removal"] = (dt.date.fromisoformat(r["fix_date"]) - dt.date.fromisoformat(rem)).days if r["fix_date"] else ""
        rows.append(r)
w = csv.DictWriter(open(f"{HERE}/code_hacs_usage.csv", "w", newline=""), fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("sample", len(sample), collections.Counter(s["status"] for s in sample))
for name in PICK:
    R = [r for r in rows if r["deprecation"] == name]; dep, rem = SPECS[name][1:3]
    ever = [r for r in R if (r["uses_at_dep"] or 0) > 0 or (r["uses_at_rem"] or 0) > 0 or r["uses_head"] > 0 or r["fix_date"]]
    known_rem = [r for r in R if r["uses_at_rem"] != ""]
    at_rem = [r for r in known_rem if r["uses_at_rem"] > 0]
    late = [r["fix_days_after_removal"] for r in R if r["fix_days_after_removal"] != "" and r["fix_days_after_removal"] > 0]
    early = [r for r in R if r["fix_days_after_removal"] != "" and r["fix_days_after_removal"] <= 0]
    print(f"{name} (dep {dep}, rem {rem}): repos {len(R)} | ever-used {len(ever)} | still at HEAD {sum(r['uses_head']>0 for r in R)} ({sum(r['uses_head']>0 for r in R)/len(R):.1%})"
          f" | used at removal {len(at_rem)}/{len(known_rem)} known ({len(at_rem)/max(len(known_rem),1):.1%}) | fixed before/at removal {len(early)} | fixed after {len(late)},"
          f" median days after removal {statistics.median(late) if late else None} (IQR {statistics.quantiles(late, n=4) if len(late) > 3 else late})")
