"""#4 fragility on GitLab, Pair 2 style.
Bug fix = merged MR to master with label type::bug (data/bug_mrs.jsonl). Mapped to its merge commit
(merge_commit_sha, else squash_commit_sha) which must be a first-parent commit of master in the clone;
files = diff vs first parent (data/fp_files.json). One MR counts once per file.
Year A = merged 2024-10-01..2025-10-01, year B = 2025-10-01..2026-10-01 (MR merged_at, UTC).
Risky = >=3 bug-fix MRs in year A. Population ("live"): files changed by any first-parent commit in year A
(Pair 2's definition) AND present in the tree at the cutoff commit. Next-year fixes are counted only on live files.
Lift = (share of year-B file-fixes on risky files) / (share of live files that are risky).
Churn baseline: the same number of files ranked by year-A commit count (does bug history beat plain churn?).
Variants: SRC = source non-test (rb/js/vue/ts/go/haml/erb/graphql; spec/qa excluded; Pair 2 comparable),
          ALL = every file except generated/lock files (EXCL regex; excluded counts reported).
Rerun: python3 code_fragility.py"""
import collections, json, os, re, subprocess
OUT = os.environ.get("OUT", "data/code")
SNAP = json.load(open(f"{OUT}/data/git_basics.json"))["snapshot"]
A0, CUT, B1 = "2024-10-01", "2025-10-01", "2026-10-01"
EXCL = re.compile(r"(^|/)(Gemfile(\.next)?\.(lock|checksum)|yarn\.lock|package-lock\.json|go\.sum|Cargo\.lock)$|^db/structure\.sql$|"
                  r"^db/schema_migrations/|^locale/.*\.(pot|po)$|\.snap$|^\.rubocop_todo/|^doc/api/graphql/reference/|"
                  r"^config/feature_flags/|^ee/config/feature_flags/|^db/docs/|^config/metrics/|^ee/config/metrics/|CHANGELOG")
SRC = re.compile(r"\.(rb|js|vue|ts|go|haml|erb|graphql)$")
TEST = re.compile(r"(^|/)(spec|qa|test|fixtures)/|_spec\.rb$|\.spec\.js$|_test\.go$")
fp = json.load(open(f"{OUT}/data/fp_files.json"))
mrs = [json.loads(l) for l in open(f"{OUT}/data/bug_mrs.jsonl")]
mrs = [m for m in mrs if m.get("target_branch") == "master" and m.get("merged_at")]
def diff_files(sha):
    r = subprocess.run(["git", "-C", f"{OUT}/repo", "diff", "--name-only", "--no-renames", f"{sha}^1", sha],
                       capture_output=True, text=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"})
    return r.stdout.split() if r.returncode == 0 else None
mapped, miss, offfp = [], 0, 0
for m in mrs:
    sha = m.get("merge_commit_sha") if m.get("merge_commit_sha") in fp else m.get("squash_commit_sha")
    if sha in fp: mapped.append((m["merged_at"][:10], fp[sha][2])); continue
    fs = diff_files(m.get("merge_commit_sha") or m.get("squash_commit_sha"))   # merge commit reachable but not first-parent
    if fs is not None: mapped.append((m["merged_at"][:10], fs)); offfp += 1
    else: miss += 1
cut_sha = subprocess.run(["git", "-C", f"{OUT}/repo", "log", "--first-parent", "-1", f"--before={CUT}T00:00:00Z",
                          "--format=%H", SNAP], capture_output=True, text=True).stdout.strip()
tree = set(subprocess.run(["git", "-C", f"{OUT}/repo", "ls-tree", "-r", "--name-only", cut_sha],
                          capture_output=True, text=True).stdout.split("\n"))
touchedA = {f for at, ae, fs in fp.values() if A0 <= at[:10] < CUT for f in fs}   # committer date (local) approx
res = {"bug_mrs_to_master": len(mrs), "mapped_to_first_parent_commit": len(mapped), "of_which_not_on_first_parent_chain": offfp, "unmapped": miss,
       "yearA_fix_MRs": sum(A0 <= d < CUT for d, _ in mapped), "yearB_fix_MRs": sum(CUT <= d < B1 for d, _ in mapped),
       "files_in_tree_at_cutoff": len(tree), "cutoff_commit": cut_sha}
for name, keep in (("SRC", lambda p: SRC.search(p) and not TEST.search(p) and not EXCL.search(p)),
                   ("ALL", lambda p: not EXCL.search(p))):
    prev, nxt_all, excl_files = collections.Counter(), collections.Counter(), set()
    for d, fs in mapped:
        for f in set(fs):
            if EXCL.search(f): excl_files.add(f)
            if not keep(f): continue
            if A0 <= d < CUT: prev[f] += 1
            elif CUT <= d < B1: nxt_all[f] += 1
    live = {p for p in touchedA if p in tree and keep(p)}
    nxt = collections.Counter({p: n for p, n in nxt_all.items() if p in live})
    risky = {p for p, n in prev.items() if n >= 3 and p in live}
    nf, in_r = sum(nxt.values()), sum(n for p, n in nxt.items() if p in risky)
    r = {"live_files": len(live), "risky": len(risky), "risky_share_of_files": round(len(risky) / len(live), 4),
         "yearB_file_fixes_on_live": nf, "yearB_file_fixes_on_risky": in_r,
         "share_of_yearB_fixes_on_risky": round(in_r / nf, 4), "lift": round(in_r / nf / (len(risky) / len(live)), 2),
         "risky_fixed_again": sum(1 for p in risky if nxt[p]), "nonrisky_fixed": sum(1 for p in live - risky if nxt[p]),
         "nonrisky_files": len(live - risky), "yearB_file_fixes_dropped_not_live": sum(nxt_all.values()) - nf,
         "distinct_generated_files_excluded": len(excl_files),
         "top_risky": prev.most_common(8)}
    # churn baseline: same number of files, picked by most first-parent commits (any MR) in year A
    churn = collections.Counter(f for at, ae, fs in fp.values() if A0 <= at[:10] < CUT for f in set(fs) if f in live)
    top = {p for p, _ in churn.most_common(len(risky))}
    r["churn_baseline_share_of_yearB_fixes"] = round(sum(n for p, n in nxt.items() if p in top) / nf, 4)
    r["overlap_risky_vs_churn_top"] = len(top & risky)
    # per-file rate version: P(fixed in B | risky) / P(fixed in B | not risky)
    r["rate_ratio_fixed_again"] = round((r["risky_fixed_again"] / max(1, len(risky))) / (r["nonrisky_fixed"] / r["nonrisky_files"]), 2)
    res[name] = r
print(json.dumps(res, indent=1))
json.dump(res, open(f"{OUT}/data/fragility.json", "w"), indent=1)
