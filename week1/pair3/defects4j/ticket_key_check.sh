#!/bin/bash
# For every active Defects4J bug: does the fix commit's message name its ticket?
# Run inside the d4j container:  bash ticket_key_check.sh > results/ticket_key_in_fix_commit.csv
D4J=${D4J:-/defects4j}; PR=$D4J/project_repos
echo "pid,tracker_host,result"
for p in $(ls $D4J/framework/projects | grep -v '\.'); do
  csv=$D4J/framework/projects/$p/active-bugs.csv; [ -f $csv ] || continue
  repo=$PR/$(awk -F, -v p=$p '$1==p{print $2}' $PR/repos.csv | sed 's#.*/##').git
  tail -n +2 $csv | while IFS=, read id b f rid url; do
    msg=$(git --git-dir=$repo log -1 --format=%B $f 2>/dev/null)
    if [ -z "$msg" ]; then k=nomsg
    elif [ "$rid" = UNKNOWN ] || [ -z "$rid" ]; then k=noticket
    elif echo "$msg" | grep -qiF "$rid"; then k=key_in_msg
    elif echo "$url" | grep -q github && echo "$msg" | grep -qE "#${rid##*-}\b"; then k=hash_in_msg
    else k=absent; fi
    echo "$p,$(echo $url | sed -E 's#https?://([^/]+).*#\1#'),$k"
  done
done
