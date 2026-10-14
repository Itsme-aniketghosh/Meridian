"""Collect · step 1 · D3: git_commit and commit_file for every repo in a snapshot. See CONTRACTS.md, "Vishwa · start kit".

    python3 "DATA PIPELINE/collect/commits.py" <run_id>     # snapshots/<run_id>/ -> out/<run_id>/{git_commit,commit_file}.jsonl

- Every commit reachable from the pinned SHA. first_parent = 1 on the first-parent chain, and seq counts along
  that chain from 0 at the root. A commit that came in through a merge gets first_parent = 0 and the seq of the
  merge that brought it in.
- Files are diffed against the first parent (merges too), renames found, so old_path is set on a rename.
  Binary files have added and deleted NULL.
- Times are UTC. Emails are lowercased. Author and committer both kept: ownership uses the author.
Same as week1/pair2/merged/build.py read_commits(), plus renames, merges, and every repo.
"""
import argparse, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FMT = "%x1e%H%x1f%P%x1f%at%x1f%ae%x1f%ce%x1f%s"


def git(co, *args):
    return subprocess.run(["git", "-C", str(co), *args], check=True, capture_output=True).stdout.decode("utf-8", "replace")


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


def write_jsonl(path, rows, key):
    rows.sort(key=lambda r: tuple(r[k] for k in key))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def main(run_id, root=ROOT):
    snap = Path(root) / "snapshots" / run_id
    manifest = json.loads((snap / "manifest.json").read_text())
    commits, files = [], []
    for repo, sha in sorted(manifest["repos"].items()):
        c, f = read_repo(snap / "repos" / repo.replace("/", "__"), repo, sha, run_id)
        print(f"{repo:45} {sum(r['first_parent'] for r in c):>7,} first-parent  {len(c):>7,} commits  {len(f):>9,} files",
              file=sys.stderr, flush=True)
        commits += c
        files += f
    out = Path(root) / "out" / run_id
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "git_commit.jsonl", commits, ("repo", "sha"))
    write_jsonl(out / "commit_file.jsonl", files, ("repo", "sha", "path"))
    return commits, files


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_id")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args()
    main(a.run_id, a.root)
