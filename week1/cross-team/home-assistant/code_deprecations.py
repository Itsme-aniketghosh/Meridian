"""#1 step 1: deprecation list from first-parent history of homeassistant/ (not components) since 2022-01.
Finds added DeprecatedConstant/Enum/Alias, @deprecated_function/@deprecated_class, report_usage(/frame.report( / breaks_in_ha_version=
lines; symbol = constant name, decorated def/class, or enclosing function (hunk header). Removal = last commit where the
definition line was deleted, if the definition is gone at the snapshot.  Run: python3 code_deprecations.py -> code_deprecations.csv"""
import csv, re, subprocess, collections
R = "data/repo"
SNAP = "75c314edf0013bff046106015ab6cfb0b8c6b452"
ATTR = R.rsplit("/", 1)[0] + "/code/gitattributes"; open(ATTR, "w").write("*.py diff=python\n")
def sh(*a): return subprocess.run(["git", "-C", R, "-c", f"core.attributesFile={ATTR}", *a], capture_output=True, text=True).stdout
log = sh("log", "--first-parent", "--since=2022-01-01", "-p", "-U0", "--format=COMMIT %H %cI %an", SNAP, "--", "homeassistant", ":!homeassistant/components")
recs = {}; dels = collections.defaultdict(list)  # (file-agnostic) definition text -> [(commit,date)]
commit = date = fn = path = None; pend = None; lines = log.splitlines()
for i, l in enumerate(lines):
    if l.startswith("COMMIT "): _, commit, date, *_ = l.split(" ", 3); continue
    if l.startswith("+++ "): path = l[6:]; continue
    if l.startswith("@@"):
        m = re.search(r"@@ .*? @@\s*(?:async )?(?:def|class) (\w+)", l); fn = m.group(1) if m else None; continue
    if l.startswith("-") and not l.startswith("---"):
        m = re.match(r"-\s*(?:async )?(?:def|class) (\w+)|-\s*_DEPRECATED_(\w+)\s*[:=]|-\s*(\w+)\s*(?::[^=]+)?=\s*", l)
        if m: dels[m.group(1) or m.group(2) or m.group(3)].append((commit, date))
        continue
    if not l.startswith("+") or l.startswith("+++"): continue
    sym = kind = None
    if (m := re.match(r"\+\s*_DEPRECATED_(\w+)\s*(?::\s*\w+(?:\[[^\]]*\])?)?\s*=\s*(Deprecated\w+)", l)): sym, kind = m.group(1), m.group(2)
    elif re.match(r"\+\s*@deprecated_(function|class)", l):
        kind = "deprecated_" + re.match(r"\+\s*@deprecated_(\w+)", l).group(1)
        for k in lines[i + 1:i + 8]:
            if (m := re.match(r"\+\s*(?:async )?(?:def|class) (\w+)", k)): sym = m.group(1); break
    elif re.search(r"report_usage\(|frame\.report\(|\breport\(\s*$|breaks_in_ha_version\s*=", l) and fn:
        sym, kind = fn, "report_usage" if "report" in l else "breaks_in_ha_version"
    if sym and sym not in recs:
        ctx = " ".join(k[1:].strip() for k in lines[i:i + 6] if k.startswith("+"))[:160]
        recs[sym] = dict(symbol=sym, kind=kind, file=path, dep_commit=commit[:10], dep_date=date[:10], context=ctx)
snapdefs = sh("grep", "-h", "-o", "-P", r"^\s*(?:async )?(?:def|class) \w+|^_?\w+\s*(?=[:=])", SNAP, "--", "homeassistant", ":!homeassistant/components")
alive = {re.sub(r"^\s*(?:async )?(?:def|class) ", "", x).strip().removeprefix("_DEPRECATED_") for x in snapdefs.splitlines()}
alive |= set(re.findall(r"_DEPRECATED_(\w+)", sh("grep", "-h", "_DEPRECATED_", SNAP, "--", "homeassistant", ":!homeassistant/components")))
for r in recs.values():
    after = [d for d in dels.get(r["symbol"], []) if d[1] > r["dep_date"]]
    r["removed_by_snapshot"] = r["symbol"] not in alive
    r["rem_commit"], r["rem_date"] = (after[0][0][:10], after[0][1][:10]) if (r["removed_by_snapshot"] and after) else ("", "")  # log is newest-first
    if r["rem_date"]:
        y1, m1, d1 = map(int, r["dep_date"].split("-")); y2, m2, d2 = map(int, r["rem_date"].split("-"))
        r["months_warning"] = round(((y2 - y1) * 365 + (m2 - m1) * 30.4 + (d2 - d1)) / 30.4, 1)
    else: r["months_warning"] = ""
rows = sorted(recs.values(), key=lambda r: r["dep_date"])
F = "code_deprecations.csv"
w = csv.DictWriter(open(F, "w", newline=""), fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
rem = [r for r in rows if r["rem_date"]]; mw = sorted(r["months_warning"] for r in rem)
print("records", len(rows), "removed by snapshot", sum(r["removed_by_snapshot"] for r in rows), "with removal commit", len(rem),
      "median months warning", mw[len(mw) // 2] if mw else None, collections.Counter(r["kind"] for r in rows))
