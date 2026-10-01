#!/bin/bash
# usage: runall.sh  (runs from scratchpad/d4j; one docker container per bug, 5 at a time)
cd "$(dirname "$0")"
W=$(cygpath -w "$PWD")
cat ${1:-sample.txt} | xargs -P 6 -L 1 bash -c '
  p=$0; b=$1; [ -s res/${p}_$b.json ] && exit 0
  repo=$(awk -F, -v p=$p "\$1==p{print \$2}" repos.csv | sed "s#.*/##").git
  MSYS_NO_PATHCONV=1 timeout 600 docker run --rm --name d4j_${p}_$b -v "'"$W"':/out" d4j bash /out/bisect.sh $p $b /defects4j/project_repos/$repo > res/${p}_$b.json 2>/dev/null \
    || { docker kill d4j_${p}_$b >/dev/null 2>&1; echo "{\"pid\":\"$p\",\"bid\":$b,\"status\":\"timeout\"}" > res/${p}_$b.json; }
  echo "$(date +%T) $p-$b $(grep -o "\"status\":\"[^\"]*\"" res/${p}_$b.json)"'
