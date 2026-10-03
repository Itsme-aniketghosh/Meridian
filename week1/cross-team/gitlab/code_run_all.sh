#!/bin/sh
# Code-side GitLab checks (#3, #4, #6 + identities). Everything lands in $OUT (default: session scratchpad).
# Usage: sh code_run_all.sh      (fetch steps are cached/resumable; resolve_handles uses ~/.gitlab_token, ~150 calls)
set -e
OUT=${OUT:-data/code}
export OUT
cd "$(dirname "$0")"
mkdir -p "$OUT/data"
if [ ! -d "$OUT/repo" ]; then
  git clone --filter=blob:none --no-checkout --single-branch --branch master --shallow-since=2023-06-01 \
    https://gitlab.com/gitlab-org/gitlab.git "$OUT/repo"
  git -C "$OUT/repo" fetch --filter=blob:none --deepen=16000 origin master   # reach Jan 2023 on first-parent
fi
python3 code_git_basics.py > /dev/null          # snapshot, per-year, identities -> data/git_basics.json
SNAP=$(python3 -c "import json;print(json.load(open('$OUT/data/git_basics.json'))['snapshot'])")
git -C "$OUT/repo" show "$SNAP:.gitlab/CODEOWNERS" > "$OUT/data/CODEOWNERS"
grep -o '@[A-Za-z0-9_./-]*' "$OUT/data/CODEOWNERS" | sort -u > "$OUT/data/handles.txt"
python3 code_fp_files.py
[ -f "$OUT/data/bug_mrs.jsonl" ] || python3 code_fetch_mrs.py        # ~10 min anonymous
[ -f "$OUT/data/all_mrs.jsonl" ] || python3 code_fetch_mrs.py all    # ~25 min anonymous
[ -f "$OUT/data/group_members.json" ] || python3 code_resolve_handles.py
python3 code_codeowners.py
python3 code_fragility.py
python3 code_ownership.py
python3 code_rubocop_todo.py
