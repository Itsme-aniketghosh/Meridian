"""Rebuild defects4j_bic_labels.csv: Fonte's 130 bug-inducing commits mapped to Defects4J v3 IDs.

Fonte's Lang and Math IDs come from the older Defects4J v2 repo bundle. For those, read the
commit's author time + subject from the v2 repo and find the same commit in the v3 repo
(fallbacks: time only, then patch-id). All other projects' IDs exist in v3 as-is.

Inputs (paths are arguments):
  fonte_csv   https://raw.githubusercontent.com/coinse/fonte/HEAD/data/Defects4J/BIC_dataset/combined.csv
  v3_repos    Defects4J v3 project_repos/ (inside the Docker image: /defects4j/project_repos)
  v2_repos    project_repos/ from https://defects4j.org/downloads/defects4j-repos-v2.zip
usage: python make_labels.py combined.csv V3_REPOS V2_REPOS > defects4j_bic_labels.csv
"""
import csv, subprocess, sys

fonte_csv, V3, V2 = sys.argv[1:4]
REPO = {r[0]: r[1].rsplit("/", 1)[1] + ".git" for r in csv.reader(open(f"{V3}/repos.csv"))}


def git(repo, *a, inp=None):
    return subprocess.run(["git", "--git-dir", repo, *a], input=inp, capture_output=True,
                          text=True, errors="replace").stdout


def meta(repo, sha):  # (full sha, author unix time, subject) or None
    out = git(repo, "show", "-s", "--format=%H%x09%at%x09%s", sha).strip()
    return out.split("\t", 2) if out else None


def patch_id(repo, sha):
    return git(repo, "patch-id", "--stable", inp=git(repo, "show", sha)).split(" ")[0]


logs = {}
w = csv.writer(sys.stdout)
w.writerow(["pid", "bid", "bic_d4j_v3", "fonte_sha", "provenance", "mapping"])
rows = sorted(csv.DictReader(open(fonte_csv)), key=lambda r: (r["pid"], int(r["vid"])))
for r in rows:
    p, v, c = r["pid"], r["vid"], r["commit"]
    v3 = f"{V3}/{REPO[p]}"
    m = meta(v3, c)
    if m:
        w.writerow([p, v, m[0], c, r["provenance"], "direct"]); continue
    old = meta(f"{V2}/{REPO[p]}", c)
    if not old:
        w.writerow([p, v, "", c, r["provenance"], "missing"]); continue
    if p not in logs:
        logs[p] = [l.split("\t", 2) for l in git(v3, "log", "--all", "--format=%H%x09%at%x09%s").splitlines()]
    _, ts, subj = old
    how = "time+subject"; hit = [x for x in logs[p] if x[1] == ts and x[2].strip() == subj.strip()]
    if not hit:
        how = "time-only"; hit = [x for x in logs[p] if x[1] == ts]
    if len(hit) > 1:
        pid_old = patch_id(f"{V2}/{REPO[p]}", c)
        how = "patch-id"; hit = [x for x in hit if patch_id(v3, x[0]) == pid_old]
    w.writerow([p, v, hit[0][0] if len(hit) == 1 else "", c, r["provenance"], how if len(hit) == 1 else "unmapped"])
