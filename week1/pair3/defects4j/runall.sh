#!/bin/bash
# Bisect a list of bugs, one d4j container per bug, 6 at a time, 10 min cap each.
# usage: ./runall.sh [bugs.txt]   (lines "Project BugId"; default: sample_38.txt)
# needs: docker image "d4j" (docker build -t d4j <defects4j clone>)
cd "$(dirname "$0")" && mkdir -p res logs
W=$PWD; command -v cygpath >/dev/null && W=$(cygpath -w "$PWD")
tr -d '\r' < "${1:-sample_38.txt}" | xargs -P 6 -L 1 bash -c '
  p=$0; b=$1; [ -s res/${p}_$b.json ] && exit 0
  repo=$(awk -F, -v p=$p "\$1==p{print \$2}" repos.csv | sed "s#.*/##").git
  MSYS_NO_PATHCONV=1 timeout 600 docker run --rm --name d4j_${p}_$b -v "'"$W"':/out" d4j bash /out/bisect.sh $p $b /defects4j/project_repos/$repo > res/${p}_$b.json 2>/dev/null \
    || { docker kill d4j_${p}_$b >/dev/null 2>&1; echo "{\"pid\":\"$p\",\"bid\":$b,\"status\":\"timeout\"}" > res/${p}_$b.json; }
  echo "$(date +%T) $p-$b $(grep -o "\"status\":\"[^\"]*\"" res/${p}_$b.json)"'
