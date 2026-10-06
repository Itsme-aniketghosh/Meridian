#!/usr/bin/env python3
"""Git-side identities for home-assistant/core, 2025-04-01..snapshot (all commits reachable, not only first-parent).
Usage: python3 tix_identities.py   (needs HA_REPO clone)
Merges author identities on (lowercased name) OR (email local part), like Pair 2. Reports duplicate share,
committer != author share, and share of commits whose author email is a GitHub noreply (gives login)."""
import os, re, subprocess, collections
REPO = os.environ.get("HA_REPO", "data/repo")
SNAP = "75c314edf0013bff046106015ab6cfb0b8c6b452"
BOT = re.compile(r"\[bot\]|dependabot|renovate|github-actions|pre-commit-ci", re.I)
NOREPLY = re.compile(r"^(?:(\d+)\+)?([^@]+)@users\.noreply\.github\.com$", re.I)

def run(first_parent):
    args = ["git", "-C", REPO, "log", SNAP, "--since=2025-04-01", "--format=%an\x1f%ae\x1f%cn\x1f%ce"]
    if first_parent: args.insert(4, "--first-parent")
    rows = [l.split("\x1f") for l in subprocess.check_output(args, text=True).splitlines()]
    n = len(rows)
    bots = sum(1 for an, ae, *_ in rows if BOT.search(an) or BOT.search(ae))
    human = [r for r in rows if not (BOT.search(r[0]) or BOT.search(r[1]))]
    # union-find over identities
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b): parent[find(a)] = find(b)
    idents = set()
    for an, ae, *_ in human:
        ae = ae.lower(); m = NOREPLY.match(ae)
        local = m.group(2) if m else ae.split("@")[0]
        key = ("id", an, ae); idents.add(key)
        union(key, ("name", an.strip().lower()))
        if len(local) >= 4 and local not in ("info", "mail", "contact", "github", "dev", "me", "noreply"):
            union(key, ("local", local))
    people = {find(k) for k in idents}
    emails = {k[2] for k in idents}
    comm_ne = sum(1 for an, ae, cn, ce in rows if ae.lower() != ce.lower())
    comm_gh = sum(1 for *_, cn, ce in rows if ce.lower() == "noreply@github.com")
    nore = sum(1 for an, ae, *_ in human if NOREPLY.match(ae.lower()))
    tag = "first-parent (= squash-merged PRs)" if first_parent else "all commits"
    print(f"== {tag}: N={n} commits, bot-authored {bots} ({bots/n:.1%})")
    print(f"   human commits {len(human)}; distinct (name,email) identities {len(idents)}; distinct emails {len(emails)}; merged people {len(people)}")
    print(f"   duplicate share = 1 - people/identities = {1-len(people)/len(idents):.1%}")
    print(f"   committer email != author email: {comm_ne}/{n} ({comm_ne/n:.1%}); committer is GitHub web-merge (noreply@github.com): {comm_gh}/{n} ({comm_gh/n:.1%})")
    print(f"   human commits with GitHub noreply author email (login readable): {nore}/{len(human)} ({nore/len(human):.1%})")

if __name__ == "__main__":
    run(True); run(False)
