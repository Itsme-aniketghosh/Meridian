"""#6 'adds while others remove' on GitLab's RuboCop todo lists (.rubocop_todo/**/*.yml).
Each todo file = one cop; each `  - 'path'` line = one file still allowed to violate it.
Walks first-parent history 2024-01-01..snapshot (diffs vs first parent). Per commit and cop:
  new     = entries on '+' lines that are not also on '-' lines of the same commit (re-sorts don't count)
  dropped = entries on '-' lines not re-added in the same commit
  commit kinds: creation (todo file added), pure-add (new>0, dropped=0), pure-remove, mixed (both: renames/regens)
Blobs: lists needed blob ids and fetches them in ONE batched `git fetch --stdin` (no per-blob lazy fetch).
Rerun: python3 code_rubocop_todo.py"""
import collections, json, os, re, subprocess
OUT = os.environ.get("OUT", "data/code")
REPO = f"{OUT}/repo"
SNAP = json.load(open(f"{OUT}/data/git_basics.json"))["snapshot"] if os.path.exists(f"{OUT}/data/git_basics.json") else "0c03c71ad4b43d4ba9bc6cb63d3f71521b650002"
SINCE = "2024-01-01T00:00:00Z"
ENTRY = re.compile(r"^([+-])\s+- ['\"]?([^'\"#]+?)['\"]?\s*$")


def git(*a, inp=None, env=None):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True, check=True, input=inp,
                          env={**os.environ, **(env or {})}).stdout


raw = git("log", "--first-parent", "--diff-merges=first-parent", "--raw", "--no-renames", "--no-abbrev", "--format=C %H",
          f"--since={SINCE}", SNAP, "--", ".rubocop_todo/")
oids = sorted({o for ln in raw.splitlines() if ln.startswith(":") for o in ln.split()[2:4] if set(o) != {"0"}})
git("-c", "fetch.negotiationAlgorithm=noop", "fetch", "origin", "--no-tags", "--no-write-fetch-head",
    "--recurse-submodules=no", "--filter=blob:none", "--stdin", inp="\n".join(oids) + "\n")
log = git("log", "--first-parent", "--diff-merges=first-parent", "-p", "--no-renames", "--format=C %H %cI",
          f"--since={SINCE}", SNAP, "--", ".rubocop_todo/", env={"GIT_NO_LAZY_FETCH": "1"})

cops = collections.defaultdict(lambda: {"added_raw": 0, "removed_raw": 0, "new": 0, "dropped": 0, "created": None,
                                        "deleted": None, "events": []})
def flush(c, f, plus, minus, created, deleted):
    if f is None: return
    d = cops[f]
    d["added_raw"] += len(plus); d["removed_raw"] += len(minus)
    new, drop = set(plus) - set(minus), set(minus) - set(plus)
    if created: d["created"] = c[1]; kind = "creation"
    elif deleted: d["deleted"] = c[1]; kind = "file-deleted"
    elif new and not drop: kind = "pure-add"
    elif drop and not new: kind = "pure-remove"
    elif new and drop: kind = "mixed"
    else: kind = "noop"
    if not created: d["new"] += len(new)
    d["dropped"] += len(drop)
    d["events"].append({"sha": c[0], "at": c[1], "kind": kind, "new": len(new), "dropped": len(drop)})

cur = None; f = None; plus = minus = None; created = deleted = False
for ln in log.splitlines():
    if ln.startswith("C "):
        flush(cur, f, plus, minus, created, deleted); f = None
        _, sha, at = ln.split(); cur = (sha, at[:10]); continue
    if ln.startswith("diff --git "):
        flush(cur, f, plus, minus, created, deleted)
        f = ln.split(" b/")[-1]; plus, minus = [], []; created = deleted = False; continue
    if f is None: continue
    if ln.startswith("new file mode"): created = True; continue
    if ln.startswith("deleted file mode"): deleted = True; continue
    if ln.startswith("+++") or ln.startswith("---"): continue
    m = ENTRY.match(ln)
    if m: (plus if m.group(1) == "+" else minus).append(m.group(2).strip())
flush(cur, f, plus, minus, created, deleted)

cops = {k: v for k, v in cops.items() if k.endswith(".yml")}
rows = []
for k, d in cops.items():
    adds = [e for e in d["events"] if e["kind"] in ("pure-add", "mixed") and e["new"] > 0]
    d["add_months"] = len({e["at"][:7] for e in adds})
    d["pure_add_months"] = len({e["at"][:7] for e in adds if e["kind"] == "pure-add"})
    d["net"] = d["new"] - d["dropped"]   # entries gained minus lost after creation (re-sorts excluded)
    rows.append((k, d))
size = collections.Counter()
for k, d in rows:
    for e in d["events"]:
        if e["kind"] in ("pure-add", "mixed") and e["new"] > 0:
            size["1-5" if e["new"] <= 5 else "6-49" if e["new"] < 50 else "50+"] += 1
size_entries = collections.Counter()
for k, d in rows:
    for e in d["events"]:
        if e["kind"] in ("pure-add", "mixed") and e["new"] > 0:
            size_entries["1-5" if e["new"] <= 5 else "6-49" if e["new"] < 50 else "50+"] += e["new"]
kinds = collections.Counter(e["kind"] for _, d in rows for e in d["events"])
created_in = [k for k, d in rows if d["created"]]
res = {
    "window": [SINCE, SNAP], "commits_touching_todo": raw.count("\nC ") + raw.startswith("C "),
    "blobs_fetched": len(oids), "cops_touched": len(rows), "cops_created_in_window": len(created_in),
    "cops_deleted_in_window(fully burned down)": sum(1 for _, d in rows if d["deleted"]),
    "cops_net_burndown(net<0)": sum(d["net"] < 0 for _, d in rows),
    "cops_net_growth(net>0)": sum(d["net"] > 0 for _, d in rows),
    "cops_with_new_entries_after_creation": sum(d["new"] > 0 for _, d in rows),
    "cops_new_entries_in_>=3_months": sum(d["add_months"] >= 3 for _, d in rows),
    "cops_pure_add_commits_in_>=3_months": sum(d["pure_add_months"] >= 3 for _, d in rows),
    "total_new_entries_after_creation": sum(d["new"] for _, d in rows),
    "total_dropped_entries": sum(d["dropped"] for _, d in rows),
    "event_kinds": dict(kinds), "adding_commits_by_size(new entries)": dict(size),
    "new_entries_by_commit_size": dict(size_entries),
    "top_cops_by_add_months": [{"cop": k, "add_months": d["add_months"], "pure_add_months": d["pure_add_months"],
                                "new": d["new"], "dropped": d["dropped"], "net": d["net"],
                                "created": d["created"]} for k, d in sorted(rows, key=lambda r: (-r[1]["add_months"], -r[1]["new"]))[:10]],
}
print(json.dumps(res, indent=1))
json.dump(res, open(f"{OUT}/data/rubocop_todo_summary.json", "w"), indent=1)
json.dump({k: d for k, d in rows}, open(f"{OUT}/data/rubocop_todo_cops.json", "w"))

# --- cross-team view (needs data/all_mrs.jsonl: MRs merged 2025-04..2026-09) ---------------------------------
# team = the MR's group:: label. For each add event (new>0, after creation) we look at the latest earlier
# remove event (dropped>0) on the same cop with a known team: is it a different team?
if os.path.exists(f"{OUT}/data/all_mrs.jsonl"):
    team = {}
    for l in open(f"{OUT}/data/all_mrs.jsonl"):
        m = json.loads(l)
        g = sorted(x for x in m.get("labels") or [] if x.startswith("group::"))
        team[m["iid"]] = g[0] if g else None
    sha2mr = json.load(open(f"{OUT}/data/merge_sha_to_mr.json"))
    tm = lambda sha: team.get(sha2mr.get(sha)) if sha2mr.get(sha) in team else "NO_MR"
    adds = cross = same = unk = 0; cops_x = collections.Counter(); per_cop_teams = {}
    for k, d in rows:
        ev = sorted((e for e in d["events"] if e["at"] >= "2025-04-01"), key=lambda e: e["at"])
        last_rm, adders, removers = None, set(), set()
        for e in ev:
            t = tm(e["sha"])
            if e["kind"] in ("pure-add", "mixed") and e["new"] > 0:
                adds += 1
                if t: adders.add(t)
                if not t or t == "NO_MR" or not last_rm: unk += 1
                elif t != last_rm: cross += 1; cops_x[k] += 1
                else: same += 1
            if e["dropped"] > 0 and t and t != "NO_MR": last_rm = t; removers.add(t)
        if adders or removers: per_cop_teams[k] = (len(adders - {"NO_MR"}), len(removers))
    labelled = sum(1 for k, d in rows for e in d["events"] if e["at"] >= "2025-04-01" and tm(e["sha"]) not in (None, "NO_MR"))
    allev = sum(1 for k, d in rows for e in d["events"] if e["at"] >= "2025-04-01")
    xt = {"window": "2025-04-01..snapshot", "todo_events": allev, "events_with_group_label": labelled,
          "add_events": adds, "add_after_removal_by_other_team": cross, "add_after_removal_by_same_team": same,
          "add_events_team_unknown_or_no_prior_removal": unk,
          "cops_with_cross_team_adds": len(cops_x), "top_cross_team_cops": cops_x.most_common(5)}
    print(json.dumps(xt, indent=1))
    res["cross_team"] = xt
    json.dump(res, open(f"{OUT}/data/rubocop_todo_summary.json", "w"), indent=1)
