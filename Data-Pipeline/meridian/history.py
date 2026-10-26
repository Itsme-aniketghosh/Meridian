"""History · step 1 · D3 + D4: every Vishwa table after the run row. See CONTRACTS.md, "Vishwa · start kit".

    python -m meridian.history --run-id <run_id> --config meridian.toml   # snapshots/<run_id>/ -> out/<run_id>/*.jsonl

Running it again for the same run replaces its own files with the same rows.
D3: git_commit and commit_file, below. D4: ticket, ticket_event, ticket_link, commit_ticket, in meridian/tickets.py.
D6: bug_inducing and file_bug_fixes, in meridian/szz.py.

- Every commit reachable from the pinned SHA. first_parent = 1 on the first-parent chain, and seq counts along
  that chain from 0 at the root. A commit that came in through a merge gets first_parent = 0 and the seq of the
  merge that brought it in.
- Files are diffed against the first parent (merges too), renames found, so old_path is set on a rename.
  Binary files have added and deleted NULL.
- Times are UTC. Emails are lowercased. Author and committer both kept: ownership uses the author.
Same as week1/pair2/merged/build.py read_commits(), plus renames, merges, and every repo.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from meridian import szz, tickets
from meridian.common import (CONFIG, Cache, cache_on, data_root, git, handoff, load_config, load_infra, log, parser,
                             repo_checkout, write_jsonl)

FMT = "%x1e%H%x1f%P%x1f%at%x1f%ae%x1f%ce%x1f%s"


def utc(epoch):
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seqs(co, sha):
    """{commit: (seq, first_parent)}. One pass: each merge on the chain claims the side commits it brings in."""
    parents = {}
    for line in git(co, "rev-list", "--parents", sha).splitlines():
        c, *ps = line.split()
        parents[c] = ps
    chain = git(co, "rev-list", "--first-parent", "--reverse", sha).split()
    out = {}
    for seq, c in enumerate(chain):
        out[c] = (seq, 1)
        stack = parents[c][1:]  # side parents; everything behind the first parent is already claimed
        while stack:
            s = stack.pop()
            if s not in out:
                out[s] = (seq, 0)
                stack.extend(parents[s])
    return out


def read_repo(co, repo, sha, run_id):
    order = seqs(co, sha)
    out = git(co, "log", "-z", "--numstat", "--find-renames", "--diff-merges=first-parent", f"--format={FMT}", sha)
    commits, files = [], []
    for rec in out.split("\x1e")[1:]:
        head, _, stat = rec.partition("\0")
        c, ps, at, ae, ce, subject = head.split("\x1f", 5)
        ps = ps.split()
        seq, fp = order[c]
        commits.append({"run_id": run_id, "repo": repo, "sha": c, "parent_sha": ps[0] if ps else None, "seq": seq,
                        "first_parent": fp, "is_merge": int(len(ps) > 1), "authored_at": utc(at),
                        "author_email": ae.lower(), "committer_email": ce.lower(), "subject": subject})
        tokens = stat.lstrip("\n").split("\0")
        i = 0
        while i < len(tokens):
            t = tokens[i].lstrip("\n")
            i += 1
            if not t:
                continue
            add, dele, path = t.split("\t", 2)
            old = None
            if not path:  # rename: "add\tdel\t" NUL old NUL new
                old, path = tokens[i], tokens[i + 1]
                i += 2
            files.append({"run_id": run_id, "repo": repo, "sha": c, "path": path, "old_path": old,
                          "added": None if add == "-" else int(add), "deleted": None if dele == "-" else int(dele)})
    if len(commits) != len(order):
        raise RuntimeError(f"{repo}: git log gave {len(commits)} commits, rev-list {len(order)}")
    return commits, files


def messages(co, sha):
    """[(sha, subject, body)] for every commit reachable from sha."""
    out = git(co, "log", "--format=%x1e%H%x1f%s%x1f%b", sha)
    return [tuple(rec.split("\x1f", 2)) for rec in out.split("\x1e")[1:]]


def keyed(commits, commit_ticket, last):
    """Week 1's check: share of the last [checks] keyed_last_commits first-parent commits with a key, from the subject,
    and counting the body."""
    chain = sorted((c for c in commits if c["first_parent"]), key=lambda c: c["seq"])[-last:]
    src = defaultdict(set)
    for r in commit_ticket:
        src[r["sha"]].add(r["source"])
    return (sum("subject" in src[c["sha"]] for c in chain) / len(chain),
            sum(bool(src[c["sha"]]) for c in chain) / len(chain), len(chain))


def main(run_id, config=CONFIG, root=None):
    cfg, root = load_config(config), Path(root or data_root())
    snap = root / "snapshots" / run_id
    if not (snap / "manifest.json").exists():
        raise SystemExit(f"no snapshot {snap}: run meridian.collect first")
    manifest = json.loads((snap / "manifest.json").read_text())
    commits, files = [], []
    co = lambda repo: repo_checkout(snap, repo)
    for repo, sha in sorted(manifest["repos"].items()):
        c, f = read_repo(co(repo), repo, sha, run_id)
        log(f"{repo:45} {sum(r['first_parent'] for r in c):>7,} first-parent  {len(c):>7,} commits  {len(f):>9,} files")
        commits += c
        files += f
    out = handoff(cfg, run_id, root)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "git_commit.jsonl", commits, ("repo", "sha"))
    write_jsonl(out / "commit_file.jsonl", files, ("repo", "sha", "path"))

    d4 = tickets.build(snap, manifest, cfg, run_id, lambda repo: messages(co(repo), manifest["repos"][repo]))
    for repo in sorted({r["repo"] for r in d4["commit_ticket"]}):
        subj, any_, n = keyed([c for c in commits if c["repo"] == repo],
                              [r for r in d4["commit_ticket"] if r["repo"] == repo],
                              cfg["checks"]["keyed_last_commits"])
        log(f"{repo:45} last {n:,} first-parent commits keyed: {subj:.1%} from the subject, "
            f"{any_:.1%} counting the body")
    write_jsonl(out / "ticket.jsonl", d4["ticket"], ("key",))
    write_jsonl(out / "ticket_event.jsonl", d4["ticket_event"], ("key", "at", "seq"))
    write_jsonl(out / "ticket_link.jsonl", d4["ticket_link"], ("key", "link_type", "other_key"))
    write_jsonl(out / "commit_ticket.jsonl", d4["commit_ticket"], ("repo", "sha", "key"))

    cache_of = (lambda repo: Cache("szz", repo, root)) if cache_on("szz") else None
    # szz_workers 0: one per CPU
    d6 = szz.build(commits, files, d4, manifest, cfg, run_id, co, load_infra("history").get("szz_workers") or None,
                   cache_of)
    write_jsonl(out / "bug_inducing.jsonl", d6["bug_inducing"], ("repo", "fix_sha", "path", "inducing_sha"))
    write_jsonl(out / "file_bug_fixes.jsonl", d6["file_bug_fixes"], ("repo", "path"))
    return commits, files, d4, d6


if __name__ == "__main__":
    a = parser(__doc__).parse_args()
    main(a.run_id, a.config)
