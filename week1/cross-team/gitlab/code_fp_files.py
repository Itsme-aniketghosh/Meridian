"""Files changed by every first-parent commit on master 2024-04-01..snapshot (diff vs first parent, no rename
detection so no blobs are needed). Writes $OUT/data/fp_files.json {sha: [committer_iso, author_email, [files]]}.
Rerun: python3 code_fp_files.py"""
import json, os, subprocess
OUT = os.environ.get("OUT", "data/code")
SNAP = json.load(open(f"{OUT}/data/git_basics.json"))["snapshot"]
log = subprocess.run(["git", "-C", f"{OUT}/repo", "log", "--first-parent", "--diff-merges=first-parent", "--name-only",
                      "--no-renames", "--format=%x1e%H %cI %ae", "--since=2024-04-01T00:00:00Z", SNAP],
                     capture_output=True, text=True, check=True, env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}).stdout
out = {}
for rec in log.split("\x1e")[1:]:
    lines = rec.strip("\n").split("\n")
    sha, at, ae = lines[0].split(" ", 2)
    out[sha] = [at, ae.lower(), [l for l in lines[1:] if l]]
json.dump(out, open(f"{OUT}/data/fp_files.json", "w"))
print("first-parent commits", len(out), "file-changes", sum(len(v[2]) for v in out.values()))
