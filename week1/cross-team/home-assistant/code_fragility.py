"""#4 fragility: bugfix PRs (code_bugfix_prs.py) -> files via the clone; year A = 2024-10-01..2025-10-01, year B = 2025-10-01..2026-10-01.
Eligible = .py under homeassistant/ (not generated/), existing at the cutoff; everything else (tests, translations, strings.json, manifest.json,
requirements*.txt, CODEOWNERS, .strict-typing, ...) is excluded and counted. Fix-history set F = files with >=3 bugfix PRs in A; churn set = same
number of files ranked by year-A first-parent commit count. Same at integration (folder) level. Run: python3 code_fragility.py"""
import json, subprocess, collections, re
from code_specs import R, SC
def sh(*a, inp=None): return subprocess.run(["git", "-C", R, *a], capture_output=True, text=True, input=inp).stdout
A0, CUT, B1 = "2024-10-01", "2025-10-01", "2026-10-01"
prs = {p["number"]: p for w in json.load(open(f"{SC}/bugfix_prs.json")).values() for p in w["prs"] if p.get("mergeCommit")}
print("bugfix PRs", len(prs), "without mergeCommit", sum(1 for w in json.load(open(f"{SC}/bugfix_prs.json")).values() for p in w["prs"] if not p.get("mergeCommit")))
oids = [p["mergeCommit"]["oid"] for p in prs.values()]
present = set(sh("cat-file", "--batch-check=%(objectname) %(objecttype)", inp="\n".join(oids)).split())
out = sh("diff-tree", "--stdin", "-r", "--name-only", "-m", "--first-parent", inp="\n".join(o for o in oids if o in present))
files_of = collections.defaultdict(set); cur = None
for l in out.splitlines():
    if re.fullmatch(r"[0-9a-f]{40}", l): cur = l
    elif l and cur: files_of[cur].add(l)
par = collections.Counter(len(sh("rev-list", "--parents", "-n1", o).split()) - 1 for o in list(present)[:300])
print("merge commits found in clone", len(present & set(oids)), "of", len(oids), "| parent-count sample(300)", dict(par))
cutc = sh("rev-list", "-1", "--first-parent", f"--before={CUT}T00:00:00Z", "dev").strip()
exist = set(sh("ls-tree", "-r", "--name-only", cutc).splitlines())
def eligible(f): return f.startswith("homeassistant/") and f.endswith(".py") and "/generated/" not in f and f in exist
excl = collections.Counter(); touchA = collections.Counter(); touchB = collections.Counter(); iA = collections.Counter(); iB = collections.Counter()
for p in prs.values():
    o = p["mergeCommit"]["oid"]; yr = "A" if A0 <= p["mergedAt"][:10] < CUT else "B" if CUT <= p["mergedAt"][:10] < B1 else None
    if yr is None or o not in files_of: continue
    fs = files_of[o]; el = {f for f in fs if eligible(f)}; excl["excluded file-touches"] += len(fs - el); excl["eligible file-touches"] += len(el)
    if not el: excl["PRs with no eligible file"] += 1
    ints = {f.split("/")[2] for f in el if f.startswith("homeassistant/components/")}
    for f in el: (touchA if yr == "A" else touchB)[f] += 1
    for i in ints: (iA if yr == "A" else iB)[i] += 1
print(dict(excl), "| PRs in A", sum(1 for p in prs.values() if A0 <= p["mergedAt"][:10] < CUT), "in B", sum(1 for p in prs.values() if CUT <= p["mergedAt"][:10] < B1))
churn = collections.Counter(); ichurn = collections.Counter(); churn2 = collections.Counter(); ichurn2 = collections.Counter()
log = sh("log", "--first-parent", f"--since={A0}T00:00:00Z", f"--until={CUT}T00:00:00Z", "--name-only", "--format=tformat:@@C%H", "dev")
fixoids = set(oids); churn3 = collections.Counter(); ichurn3 = collections.Counter()
for blk in log.split("@@C"):
    ls = blk.splitlines(); h = ls[0] if ls else ""
    fs = {f for f in ls[1:] if f and eligible(f)}
    ii = {f.split("/")[2] for f in fs if f.startswith("homeassistant/components/")}
    for f in fs: churn[f] += 1; churn2[f] += len(fs) <= 20   # churn2 = 'targeted' churn: ignore sweep commits touching >20 files
    for i in ii: ichurn[i] += 1; ichurn2[i] += len(ii) <= 3
    if h not in fixoids:  # churn3 = non-bugfix commits only
        for f in fs: churn3[f] += 1
        for i in ii: ichurn3[i] += 1
print("year-A commits", log.count("@@C"), "| top churn files", churn.most_common(5), "| top targeted", churn2.most_common(3))
def report(label, universe, A, B, ch):
    F = {f for f in universe if A[f] >= 3}; n = len(F)
    C = {f for f, _ in sorted(((f, ch[f]) for f in universe), key=lambda x: -x[1])[:n]}
    totB = sum(B[f] for f in universe)
    def stats(S):
        rest = universe - S; rS = sum(B[f] for f in S) / max(len(S), 1); rR = sum(B[f] for f in rest) / max(len(rest), 1)
        return f"N={len(S)} ({len(S)/len(universe):.1%} of {len(universe)}), year-B fix share {sum(B[f] for f in S)/totB:.1%}, lift {rS/rR:.1f}x, any-B-fix {sum(1 for f in S if B[f])/max(len(S),1):.0%} vs rest {sum(1 for f in rest if B[f])/max(len(rest),1):.0%}"
    print(f"[{label}] year-B fix touches {totB}")
    print("  fix-history (>=3 in A):", stats(F)); print("  churn top-N          :", stats(C)); print("  overlap F&C", len(F & C), "| F only", stats(F - C), "| C only", stats(C - F))
    return F, C
eligible_files = {f for f in exist if eligible(f)}
report("files", eligible_files, touchA, touchB, churn); report("files, targeted churn (commits <=20 files)", eligible_files, touchA, touchB, churn2); report("files, NON-bugfix churn", eligible_files, touchA, touchB, churn3)
ints = {f.split("/")[2] for f in eligible_files if f.startswith("homeassistant/components/")}
report("integrations", ints, iA, iB, ichurn); report("integrations, targeted churn (commits <=3 integrations)", ints, iA, iB, ichurn2); report("integrations, NON-bugfix churn", ints, iA, iB, ichurn3)
