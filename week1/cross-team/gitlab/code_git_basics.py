"""Git basics on the blobless clone of gitlab-org/gitlab (no blobs needed).
  - snapshot = last first-parent commit on master with committer date < 2026-10-01T00:00:00Z
  - issue hint: branch name starts with an issue number ("Merge branch '12345-...'") or message links an issue
  - first-parent commits per year (committer date, UTC) and share whose message has "See merge request gitlab-org/gitlab!N"
  - author identities over all NON-merge commits reachable from the snapshot, authored 2024-01-01..snapshot
    (people = union-find on normalised name OR email local part >= 4 chars, Pair 2's rule), bot share, committer != author.
Rerun: python3 code_git_basics.py"""
import collections, datetime, json, os, re, subprocess
OUT = os.environ.get("OUT", "data/code")
REPO = f"{OUT}/repo"
GENERIC = {"root", "ubuntu", "unknown", "user", "admin", "gitlab", "localhost", "none", "test", "noreply", "git", "info"}
BOT = re.compile(r"bot\b|\[bot\]|renovate|dependabot|release tools|gitlab-bot|project_\d+_bot|group_\d+_bot|"
                 r"service account|automation|housekeeper|ops-gitlab-net|gitlab-dependency", re.I)


def git(*a):
    return subprocess.run(["git", "-C", REPO, *a], capture_output=True, text=True, check=True).stdout


def snapshot():
    out = git("log", "--first-parent", "-1", "--before=2026-10-01T00:00:00Z", "--format=%H %cI", "master")
    sha, at = out.split()
    return sha, at


if __name__ == "__main__":
    snap, snap_at = snapshot()
    res = {"snapshot": snap, "snapshot_committer_date": snap_at}
    oldest = git("log", "--first-parent", "--format=%cI", snap).strip().splitlines()[-1]
    res["oldest_first_parent_in_clone"] = oldest
    # Q1 first-parent per year
    MRREF = re.compile(r"See merge request (?:gitlab-org/gitlab!|https://gitlab\.com/gitlab-org/gitlab/-/merge_requests/)(\d+)")
    ISSUE = re.compile(r"(?:Closes|Fixes|Resolves|Related to|Relates to)?\s*(?:gitlab-org/gitlab#\d+|https://gitlab\.com/gitlab-org/gitlab/-/(?:issues|work_items)/\d+)", re.I)
    BRANCH = re.compile(r"^Merge branch '(?:[^/']+/)?(\d{3,})-")
    per = collections.defaultdict(lambda: [0, 0, 0, 0, 0, 0])
    mr_of = {}
    for rec in git("log", "--first-parent", "--format=%x1e%H%x1f%ct%x1f%P%x1f%B", snap).split("\x1e")[1:]:
        h, ct, parents, body = rec.split("\x1f", 3)
        y = datetime.datetime.fromtimestamp(int(ct), datetime.timezone.utc).year
        m = MRREF.search(body)
        if m: mr_of[h] = int(m.group(1))
        per[y][3] += "Co-authored-by: GitLab Duo" in body
        b = bool(BRANCH.search(body)); per[y][4] += b; per[y][5] += b or bool(ISSUE.search(body))
        per[y][0] += 1
        per[y][1] += len(parents.split()) > 1
        per[y][2] += bool(m)
    res["first_parent_per_year"] = {y: {"commits": v[0], "merge_commits": v[1], "with_MR_ref": v[2],
                                        "MR_ref_share": round(v[2] / v[0], 4), "duo_coauthored": v[3],
                                        "branch_starts_with_issue_no": v[4], "issue_hint_branch_or_message": v[5],
                                        "issue_hint_share": round(v[5] / v[0], 4)} for y, v in sorted(per.items()) if y >= 2023}
    json.dump(mr_of, open(f"{OUT}/data/merge_sha_to_mr.json", "w"))
    # identities
    rows = git("log", "--no-merges", "--since=2024-01-01T00:00:00Z", "--format=%an%x1f%ae%x1f%cn%x1f%ce", snap).splitlines()
    rows = [r.split("\x1f") for r in rows]
    names = collections.defaultdict(collections.Counter)
    for an, ae, cn, ce in rows: names[ae.lower()][an] += 1
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[max(ra, rb)] = min(ra, rb)
    for e, ns in names.items():
        node = "E:" + e; find(node)
        local = re.sub(r"^\d+-", "", e.split("@")[0])   # gitlab noreply: 12345-username@users.noreply.gitlab.com
        n = re.sub(r"[\s.\-_]", "", ns.most_common(1)[0][0].lower())
        if local in GENERIC or n in GENERIC: continue
        if n: union(node, "N:" + n)
        if len(local) >= 4: union(node, "L:" + local)
    people = {find("E:" + e) for e in names}
    bot_rows = [r for r in rows if BOT.search(r[0]) or BOT.search(r[1])]
    bot_ids = collections.Counter((r[0], r[1].lower()) for r in bot_rows)
    res["identities"] = {"non_merge_commits_since_2024": len(rows), "author_emails": len(names), "people": len(people),
                         "duplicate_emails": len(names) - len(people),
                         "duplicate_share": round((len(names) - len(people)) / len(names), 4),
                         "gitlab_com_email_share_of_commits": round(sum(r[1].lower().endswith("@gitlab.com") for r in rows) / len(rows), 4),
                         "bot_commits": len(bot_rows), "bot_share": round(len(bot_rows) / len(rows), 4),
                         "top_bot_identities": [[f"{n} <{e}>", c] for (n, e), c in bot_ids.most_common(10)],
                         "committer_ne_author_email": sum(r[1].lower() != r[3].lower() for r in rows),
                         "committer_ne_author_share": round(sum(r[1].lower() != r[3].lower() for r in rows) / len(rows), 4),
                         "committer_is_gitlab_noreply": sum(r[3].lower() == "noreply@gitlab.com" for r in rows)}
    # first-parent merge commits: author of merge commit = merger
    print(json.dumps(res, indent=1))
    json.dump(res, open(f"{OUT}/data/git_basics.json", "w"), indent=1)
