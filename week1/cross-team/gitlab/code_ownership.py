"""#3 stale owners on GitLab: who touches a file next, vs owner rule / last toucher / CODEOWNERS.
Unit = merged MR to master, identity = MR author username (data/all_mrs.jsonl), joined to its first-parent merge
commit via "See merge request ...!N" (data/merge_sha_to_mr.json); files = diff vs first parent (data/fp_files.json).
Time = committer date of the merge commit. MRs authored by bots are dropped everywhere.
  prior window P = 2025-04-01..2026-04-01, test window T = 2026-04-01..2026-10-01.
  target  = author of the FIRST MR in T that touches the file.
  (a) owner rule  = author with most MRs touching the file in P (ties -> most recent). Also Pair 2's variant:
                    only if that author has >= 30% of the file's P MRs, else "unowned" (not scored).
  (b) last toucher = author of the last MR in P touching the file.
      (a),(b) scored on the same files: changed in T and touched in P.
  (c) CODEOWNERS as of the cutoff commit (last first-parent commit before 2026-04-01):
      c1 = target is a user named individually on an applicable rule (any section);
      c2 = target is in the expanded owner set (named users + DIRECT members of groups we could resolve).
  c3 = same as c2 but ignoring the generic approver sections ((none)/[Maintainers]/[Backend]/[Frontend]/[Backend and/or Frontend]) (approver pools like 157 rails-backend
       maintainers): only team/feature sections. Coverage, hit rate and median owner-set size reported.
  stale = file changed in T whose applicable CODEOWNERS owners (c2 set; and separately named users only, for files
          that have any named user) include NOBODY who authored an MR touching the file in P.
Rerun: python3 code_ownership.py   (after code_fetch_mrs.py all, code_fp_files.py, code_resolve_handles.py)"""
import collections, json, os, re, subprocess
import code_codeowners as co
OUT = os.environ.get("OUT", "data/code")
SNAP = json.load(open(f"{OUT}/data/git_basics.json"))["snapshot"]
P0, CUT, T1 = "2025-04-01", "2026-04-01", "2026-10-01"
BOT = re.compile(r"bot|renovate|dependabot|release-tools|^project_\d+_|^group_\d+_|service[-_]account|housekeeper", re.I)
# same file filters as code_fragility.py
EXCL = re.compile(r"(^|/)(Gemfile(\.next)?\.(lock|checksum)|yarn\.lock|package-lock\.json|go\.sum|Cargo\.lock)$|^db/structure\.sql$|"
                  r"^db/schema_migrations/|^locale/.*\.(pot|po)$|\.snap$|^\.rubocop_todo/|^doc/api/graphql/reference/|"
                  r"^config/feature_flags/|^ee/config/feature_flags/|^db/docs/|^config/metrics/|^ee/config/metrics/|CHANGELOG")
SRC = re.compile(r"\.(rb|js|vue|ts|go|haml|erb|graphql)$")
TEST = re.compile(r"(^|/)(spec|qa|test|fixtures)/|_spec\.rb$|\.spec\.js$|_test\.go$")

mr_author = {}
for l in open(f"{OUT}/data/all_mrs.jsonl"):
    m = json.loads(l)
    if m.get("target_branch") == "master": mr_author[m["iid"]] = (m.get("author") or "").lower()
sha2mr = json.load(open(f"{OUT}/data/merge_sha_to_mr.json"))
fp = json.load(open(f"{OUT}/data/fp_files.json"))
events, nomr, bots = [], 0, 0          # (date, author, files)
for sha, (at, ae, fs) in fp.items():
    d = at[:10]
    if not (P0 <= d < T1): continue
    iid = sha2mr.get(sha)
    a = mr_author.get(iid) if iid else None
    if not a: nomr += 1; continue
    if BOT.search(a): bots += 1; continue
    events.append((at, a, fs))
events.sort()
cut_sha = subprocess.run(["git", "-C", f"{OUT}/repo", "log", "--first-parent", "-1", f"--before={CUT}T00:00:00Z",
                          "--format=%H", SNAP], capture_output=True, text=True).stdout.strip()
co_path = f"{OUT}/data/CODEOWNERS_at_cut"
open(co_path, "w").write(subprocess.run(["git", "-C", f"{OUT}/repo", "show", f"{cut_sha}:.gitlab/CODEOWNERS"],
                                        capture_output=True, text=True).stdout)
secs = co.parse(co_path)
tg = co.toplevel_groups()
gm = json.load(open(f"{OUT}/data/group_members.json"))
gm = {k.lower(): ({u.lower() for u in v} if v is not None else None) for k, v in gm.items()}
unresolved_groups = set()
POOL = {"(none)", "Maintainers", "Backend", "Frontend", "Backend and/or Frontend"}   # generic approver sections

def expand(owners):
    named, exp = set(), set()
    for o in owners:
        h = o.lstrip("@").lower()
        if co.is_user(o, tg): named.add(h); exp.add(h)
        elif gm.get(h) is not None: exp |= gm[h]
        else: unresolved_groups.add(h)
    return named, exp

res = {"cutoff_commit": cut_sha, "fp_commits_in_P_T_without_MR_author": nomr, "bot_MRs_dropped": bots,
       "MR_events_used": len(events)}
for name, keep in (("SRC", lambda p: SRC.search(p) and not TEST.search(p) and not EXCL.search(p)),
                   ("ALL", lambda p: not EXCL.search(p))):
    prior = collections.defaultdict(list); target = {}
    for at, a, fs in events:
        for f in set(fs):
            if not keep(f): continue
            if at[:10] < CUT: prior[f].append(a)
            elif f not in target: target[f] = a
    ev = [f for f in target if f in prior]
    top, top30 = {}, {}
    for f in ev:
        c = collections.Counter(prior[f]); best = max(c.values())
        cand = [a for a in reversed(prior[f]) if c[a] == best][0]
        top[f] = cand
        top30[f] = cand if best / len(prior[f]) >= 0.30 else None
    hit = lambda pred, fs: sum(pred[f] == target[f] for f in fs)
    owned30 = [f for f in ev if top30[f]]
    c1 = c2 = c2n = stale_exp = stale_exp_n = stale_named = stale_named_n = 0
    t_n = t_hit = t_stale = t_stale_n = 0; t_sizes = []
    for f in target:
        named, exp, texp = set(), set(), set()
        for sec, _, owners in co.owners_of(secs, f):
            n, e = expand(owners); named |= n; exp |= e
            if sec not in POOL: texp |= e
        if texp:
            t_n += 1; t_hit += target[f] in texp; t_sizes.append(len(texp))
            if f in prior: t_stale_n += 1; t_stale += not (texp & set(prior[f]))
        c1 += target[f] in named
        if exp: c2n += 1; c2 += target[f] in exp
        touched = set(prior.get(f, []))
        if f in prior:
            if exp: stale_exp_n += 1; stale_exp += not (exp & touched)
            if named: stale_named_n += 1; stale_named += not (named & touched)
    N = len(target)
    res[name] = {
        "files_changed_in_T": N, "scored_files_(also_touched_in_P)": len(ev),
        "a_owner_rule_hit": round(hit(top, ev) / len(ev), 4),
        "a30_owner_rule_hit_on_owned": round(hit(top30, owned30) / len(owned30), 4), "a30_owned_files": len(owned30),
        "b_last_toucher_hit": round(hit({f: prior[f][-1] for f in ev}, ev) / len(ev), 4),
        "b_last_toucher_hit_on_a30_files": round(sum(prior[f][-1] == target[f] for f in owned30) / len(owned30), 4),
        "c1_target_named_individually": round(c1 / N, 4), "c1_N": N,
        "c2_target_in_expanded_owners": round(c2 / c2n, 4), "c2_N_files_with_resolvable_owners": c2n,
        "stale_expanded_no_owner_touched_in_P": round(stale_exp / max(1, stale_exp_n), 4), "stale_expanded_N": stale_exp_n,
        "stale_named_no_named_user_touched_in_P": round(stale_named / max(1, stale_named_n), 4), "stale_named_N": stale_named_n,
        "c3_team_sections_only_files": t_n, "c3_target_in_team_owners": round(t_hit / max(1, t_n), 4),
        "c3_median_team_owner_set_size": sorted(t_sizes)[len(t_sizes) // 2] if t_sizes else None,
        "c3_stale_team_owners_none_touched_in_P": round(t_stale / max(1, t_stale_n), 4), "c3_stale_N": t_stale_n,
    }
res["unresolved_group_handles"] = sorted(unresolved_groups)
print(json.dumps(res, indent=1))
json.dump(res, open(f"{OUT}/data/ownership.json", "w"), indent=1)
