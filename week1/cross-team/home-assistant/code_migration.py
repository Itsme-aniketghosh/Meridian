"""#1 migration concentration + #6 adds-while-removing, per curated deprecation (code_specs.SPECS).
git log --first-parent -G<regex> over components+tests, 2022-01..removal+90d; per commit per integration net +/- of matching lines.
Adders (#6): integration with 0 non-test sites at the deprecation date that gains sites between dep and removal; author login via one
batched GraphQL call; codeowner = login in that integration's manifest.json codeowners at the adding commit.
Run: python3 code_migration.py -> code_migration_summary.csv, code_adders.csv (+ $SC/migration_<name>.json)"""
import json, re, subprocess, collections, csv, datetime as dt
from code_specs import R, SC, SPECS, HERE
def sh(*a): return subprocess.run(["git", "-C", R, *a], capture_output=True, text=True).stdout
def integ(path):
    m = re.match(r"(tests/)?(?:homeassistant/)?components/([^/]+)/", path.replace("tests/components", "tests/components").replace("homeassistant/components", "components"))
    return (m.group(2), bool(m.group(1))) if m else (None, None)
summary, adders = [], []
for name, (pat, dep, rem, *inf) in SPECS.items():
    dep = inf[0] if inf else dep  # adds/at-dep measured from the first (informal) deprecation signal
    rx = re.compile(pat); ere = pat.replace(r"(?<![.\w])", "").replace(r"\b", ""); until = (dt.date.fromisoformat(rem) + dt.timedelta(days=90)).isoformat()
    log = sh("log", "--first-parent", "--reverse", "--since=2022-01-01", f"--until={until}", "-p", "-U0", f"-G{ere}",
             "--format=COMMIT %H %cI %ae %an", "dev", "--", "homeassistant/components", "tests/components")
    commits = []; cur = None; path = None
    for l in log.splitlines():
        if l.startswith("COMMIT "):
            _, h, d, ae, an = (l.split(" ", 4) + [""])[:5]; cur = {"h": h, "d": d[:10], "ae": ae, "an": an, "net": collections.Counter(), "tnet": collections.Counter()}; commits.append(cur); continue
        if l.startswith("+++ ") or l.startswith("--- "):
            if l.startswith("+++ "): path = l[6:] if l != "+++ /dev/null" else path
            else: path = l[6:] if l != "--- /dev/null" else None
            continue
        if cur is None or not path or not l or l[0] not in "+-": continue
        i, t = integ(path)
        if i is None: continue
        n = len(rx.findall(l[1:])) * (1 if l[0] == "+" else -1)
        if n: (cur["tnet"] if t else cur["net"])[i] += n
    # sites per integration at dep date (snapshot commit just before dep)
    c0 = sh("rev-list", "-1", "--first-parent", f"--before={dep}T00:00:00Z", "dev").strip()
    at_dep = collections.Counter()
    for line in sh("grep", "-c", "-P", pat, c0, "--", "homeassistant/components").splitlines():
        p, n = line.split(":", 1)[1].rsplit(":", 1); at_dep[p.split("/")[2]] += int(n)
    mig = [c for c in commits if sum(c["net"].values()) < 0]
    removed = sum(-sum(c["net"].values()) for c in mig)
    by_auth = collections.Counter(); by_auth_sites = collections.Counter()
    for c in mig: by_auth[c["an"]] += 1; by_auth_sites[c["an"]] += -sum(c["net"].values())
    top = by_auth.most_common(1)[0] if by_auth else ("", 0)
    single = sum(1 for c in mig if len([k for k, v in c["net"].items() if v < 0]) == 1)
    maxint = max((len([k for k, v in c["net"].items() if v < 0]) for c in mig), default=0)
    # adds after deprecation
    post = [c for c in commits if dep <= c["d"] <= rem]
    first_new = {}
    seen = set(k for k, v in at_dep.items() if v > 0)
    for c in post:
        for k, v in c["net"].items():
            if v > 0 and k not in seen and k not in first_new: first_new[k] = (c, v)
    add_commits = [c for c in post if any(v > 0 for v in c["net"].values())]
    test_add = sum(v for c in post for v in c["tnet"].values() if v > 0)
    test_add_int = len({k for c in post for k, v in c["tnet"].items() if v > 0})
    for k, (c, v) in first_new.items():
        try: owners = json.loads(sh("show", f"{c['h']}:homeassistant/components/{k}/manifest.json")).get("codeowners", [])
        except Exception: owners = []
        adders.append({"deprecation": name, "integration": k, "commit": c["h"][:10], "date": c["d"], "sites_added": v, "author_name": c["an"],
                       "author_email": c["ae"], "codeowners": " ".join(owners), "new_integration": not sh("ls-tree", f"{c['h']}^", f"homeassistant/components/{k}/manifest.json").strip()})
    summary.append({"deprecation": name, "dep": dep, "rem": rem, "commits_touching": len(commits), "migration_commits": len(mig), "sites_removed": removed,
                    "authors": len(by_auth), "top_author": top[0], "top_author_commit_share": round(top[1] / max(len(mig), 1), 2),
                    "top_author_site_share": round(by_auth_sites[top[0]] / max(removed, 1), 2), "single_integration_commits": single, "max_integrations_per_commit": maxint,
                    "integrations_at_dep": len(+at_dep), "sites_at_dep": sum(at_dep.values()), "commits_adding_after_dep": len(add_commits),
                    "integrations_first_use_after_dep": len(first_new), "test_sites_added_after_dep": test_add, "test_integrations_added_after_dep": test_add_int})
    json.dump([{**c, "net": dict(c["net"]), "tnet": dict(c["tnet"])} for c in commits], open(f"{SC}/migration_{name}.json", "w"))
    print(summary[-1], flush=True)
# GitHub logins for adder commits (one GraphQL call per 50)
ids = sorted({a["commit"] for a in adders}); full = {a["commit"]: a for a in adders}
login = {}
hs = {a["commit"]: sh("rev-parse", a["commit"]).strip() for a in adders}
for i in range(0, len(ids), 50):
    part = ids[i:i + 50]
    q = "query{repository(owner:\"home-assistant\",name:\"core\"){" + " ".join(f'c{j}:object(oid:"{hs[x]}"){{... on Commit{{author{{user{{login}}}} associatedPullRequests(first:1){{nodes{{number author{{login}}}}}}}}}}' for j, x in enumerate(part)) + "}}"
    d = json.loads(subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True).stdout)["data"]["repository"]
    for j, x in enumerate(part):
        o = d.get(f"c{j}") or {}; prs = ((o.get("associatedPullRequests") or {}).get("nodes") or [{}])
        login[x] = ((prs[0].get("author") or {}).get("login")) or (((o.get("author") or {}).get("user") or {}).get("login")) or ""
for a in adders:
    a["login"] = login.get(a["commit"], ""); a["is_codeowner"] = ("@" + a["login"]).lower() in a["codeowners"].lower().split()
for f, rows in (("code_migration_summary.csv", summary), ("code_adders.csv", adders)):
    if rows:
        w = csv.DictWriter(open(f"{HERE}/{f}", "w", newline=""), fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("adders", len(adders), "codeowner", sum(a["is_codeowner"] for a in adders), "new integrations", sum(a["new_integration"] for a in adders))
