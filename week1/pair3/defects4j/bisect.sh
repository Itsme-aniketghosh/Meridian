#!/bin/bash
# Find the bug-inducing commit for one Defects4J bug with `git bisect run` on the FULL
# upstream repo (Defects4J's bundled repo is pruned). Keeps the FIXED version's trigger
# tests and swaps in the main source of each candidate commit.
# usage: bisect2.sh PID BID UPSTREAM_GIT_DIR   -> prints one JSON line
set -u
P=$1; B=$2; UP=$3
D4J=/defects4j
W=/tmp/w_${P}_${B}; R=/tmp/r_${P}_${B}
LOG=/out/logs/${P}_${B}.log
mkdir -p /out/logs; : > $LOG
CSV=$D4J/framework/projects/$P/active-bugs.csv
BUGGY=$(awk -F, -v b=$B '$1==b{print $2}' $CSV)
FIXED=$(awk -F, -v b=$B '$1==b{print $3}' $CSV)
TRIGGERS=$(grep '^--- ' $D4J/framework/projects/$P/trigger_tests/$B | sed 's/^--- //' | tr '\n' ' ')
T0=$(date +%s)
# Expected exception type per trigger test (line after each "--- test" header).
EXP=/tmp/exp_${P}_${B}
awk '/^--- /{t=substr($0,5); getline; sub(/[: ].*/,""); print t "\t" $0}' $D4J/framework/projects/$P/trigger_tests/$B > $EXP

json() { echo "{\"pid\":\"$P\",\"bid\":$B,\"fixed\":\"$FIXED\",\"buggy\":\"$BUGGY\",\"status\":\"$1\",\"bic\":\"${2:-}\",\"good\":\"${GOOD:-}\",\"steps\":$(grep -c '^STEP' $LOG),\"skips\":$(grep -c '^STEP.* SKIP' $LOG),\"secs\":$(( $(date +%s)-T0 ))}"; }

rm -rf $W $R
defects4j checkout -p $P -v ${B}f -w $W >>$LOG 2>&1 || { json checkout_failed; exit; }
SRC=$(cd $W && defects4j export -p dir.src.classes 2>>$LOG)
TST=$(cd $W && defects4j export -p dir.src.tests 2>>$LOG)
BINC=$(cd $W && defects4j export -p dir.bin.classes 2>>$LOG); BINT=$(cd $W && defects4j export -p dir.bin.tests 2>>$LOG)

# Old source won't compile against the fixed version's other tests: keep only the trigger
# test classes plus helpers. Only concrete *Test / *Tests classes that nothing extends are removed.
KEEP=$(for t in $TRIGGERS; do echo "${t%%::*}" | sed 's#\.#/#g; s#\$.*##; s#$#.java#'; done | sort -u)
(cd "$W/$TST" && EXT=$(grep -rhoE 'extends +[A-Za-z0-9_]+' --include='*.java' . | awk '{print $2}' | sort -u)
  find . -name '*.java' | sed 's#^\./##' | grep -E '(Test|Tests)\.java$' | grep -vxF "$KEEP" \
  | while read f; do n=$(basename "$f" .java)
      grep -qxF "$n" <<<"$EXT" && continue
      grep -q 'abstract class' "$f" && continue
      rm -f "$f"; done)

git clone -q --no-checkout --shared "$UP" $R

# Files Defects4J generates into the source dir (e.g. Jackson's PackageVersion.java) are not in
# git. Save them so they can be put back after each source swap.
GEN=/tmp/gen_${P}_${B}; rm -rf $GEN; mkdir -p $GEN
(cd "$W/$SRC" && find . -type f | sed 's#^\./##' | sort) > /tmp/have_${P}_${B}
git -C $R ls-tree -r --name-only $FIXED -- "$SRC" | sed "s#^$SRC/##" | sort > /tmp/tracked_${P}_${B}
comm -23 /tmp/have_${P}_${B} /tmp/tracked_${P}_${B} | while read f; do mkdir -p "$GEN/$(dirname "$f")"; cp "$W/$SRC/$f" "$GEN/$f"; done
echo "generated files kept: $(find $GEN -type f | wc -l)" >>$LOG

# Two copies of the tests: as-is (lang3 era) and rewritten for the pre-2009-12 `lang` package.
T3=/tmp/t3_${P}_${B}; T2=/tmp/t2_${P}_${B}; rm -rf $T3 $T2
cp -r "$W/$TST" $T3
cp -r "$W/$TST" $T2
if [ -d $T2/org/apache/commons/lang3 ]; then
  mkdir -p $T2/org/apache/commons/lang && cp -r $T2/org/apache/commons/lang3/. $T2/org/apache/commons/lang/ && rm -rf $T2/org/apache/commons/lang3
  find $T2 -name '*.java' -exec sed -i 's/org\.apache\.commons\.lang3/org.apache.commons.lang/g' {} +
fi

# Runner for `git bisect run`: 0 = good, 1 = bad, 125 = skip (can't build/test this commit).
cat > /tmp/run_${P}_${B}.sh <<EOF
#!/bin/bash
c=\$(git -C $R rev-parse HEAD)
src=""
for d in "$SRC" src/main/java src/java; do git -C $R cat-file -e "\$c:\$d" 2>/dev/null && { src=\$d; break; }; done
[ -n "\$src" ] || { echo "STEP \$c SKIP nosrc" >>$LOG; exit 125; }
rm -rf "$W/$SRC" && mkdir -p "$W/$SRC"
X=/tmp/x_${P}_${B}; rm -rf \$X && mkdir -p \$X
git -C $R archive "\$c" "\$src" | tar -x -C \$X && cp -r \$X/\$src/. "$W/$SRC"/
find "$W/$SRC" -type d -name enum -prune -exec rm -rf {} + 2>/dev/null  # pre-Java-5 package name
cp -rn $GEN/. "$W/$SRC"/ 2>/dev/null  # put generated files back if the commit lacks them
if [ -d "$W/$SRC/org/apache/commons/lang3" ]; then TS=$T3; TRIG="$TRIGGERS"; else TS=$T2; TRIG=\$(echo "$TRIGGERS" | sed 's/commons\.lang3/commons.lang/g'); fi
rm -rf "$W/$TST" && cp -r \$TS "$W/$TST"
cd $W
# Compile; on failure drop non-trigger test files javac complains about and retry (max 5 rounds).
KEEPF=\$(for t in \$TRIG; do echo "$W/$TST/\$(echo "\${t%%::*}" | sed 's#\.#/#g; s#\\\$.*##').java"; done | sort -u)
ok=0
rm -rf "$W/$BINC" "$W/$BINT"   # no stale classes from a newer commit
for round in 1 2 3 4 5; do
  timeout 600 defects4j compile > /tmp/c_${P}_${B}.log 2>&1 && { ok=1; break; }
  cat /tmp/c_${P}_${B}.log >>$LOG
  bad=\$(grep -oP "$W/$TST/[^:]+\.java(?=:[0-9]+: error)" /tmp/c_${P}_${B}.log | sort -u)
  [ -n "\$bad" ] || break                       # errors in main source: can't fix
  echo "\$bad" | grep -qxF "\$KEEPF" && break    # trigger test itself doesn't compile
  echo "\$bad" | xargs rm -f
done
[ \$ok = 1 ] || { echo "STEP \$c SKIP compile" >>$LOG; exit 125; }
for t in \$TRIG; do
  out=\$(timeout 600 defects4j test -t "\$t" 2>>$LOG)
  echo "\$out" | grep -q '^Failing tests: 0' && continue
  # Only count it as the bug if it fails with the exception Defects4J recorded for this bug.
  want=\$(awk -F'\t' -v t="\$t" '\$1==t{print \$2}' $EXP | sed 's/commons\.lang3/commons.lang/g')
  got=\$(awk -v h="--- \$t" '\$0==h{getline; print; exit}' $W/failing_tests | sed 's/[: ].*//; s/commons\.lang3/commons.lang/g')
  if [ -z "\$want" ] || [ "\$got" = "\$want" ]; then echo "STEP \$c BAD \$t" >>$LOG; exit 1; fi
  echo "STEP \$c SKIP other-failure got=\$got want=\$want" >>$LOG; exit 125
done
echo "STEP \$c GOOD" >>$LOG; exit 0
EOF
chmod +x /tmp/run_${P}_${B}.sh
mkdir -p /tmp/x_${P}_${B}
probe() { git -C $R checkout -q --detach "$1" 2>>$LOG; /tmp/run_${P}_${B}.sh; echo $?; }

# Sanity: parent-of-fix must be bad.
[ "$(probe $BUGGY)" = 1 ] || { json buggy_not_bad; exit; }

# Exponential search back along first-parent src-touching commits for a GOOD commit.
mapfile -t L < <(git -C $R rev-list --first-parent $BUGGY -- "$SRC" src/java src/main/java)
N=${#L[@]}; GOOD=""; i=1; BADI=0; GI=-1
while [ $i -lt $N ]; do
  r=$(probe ${L[$i]})
  [ "$r" = 0 ] && { GOOD=${L[$i]}; GI=$i; break; }
  if [ "$r" = 1 ]; then SK=0; BADI=$i; [ $i -eq $((N-1)) ] && break; i=$((i*2)); [ $i -ge $N ] && i=$((N-1)); continue; fi
  # skip: step older, faster the further back we are; give up after 15 skips in a row
  SK=$(( ${SK:-0}+1 )); [ $SK -gt 15 ] && break
  i=$(( i + (i/16>1 ? i/16 : 1) ))
done
[ -n "$GOOD" ] || { json no_good_commit; exit; }

# Binary search between BADI (newer, fails) and GI (older, passes). Skips step toward GI.
while [ $((GI-BADI)) -gt 1 ]; do
  m=$(( (BADI+GI)/2 )); r=$(probe ${L[$m]})
  while [ "$r" = 125 ] && [ $((m+1)) -lt $GI ]; do m=$((m+1)); r=$(probe ${L[$m]}); done
  case $r in
    1) BADI=$m ;;
    0) GI=$m ;;
    *) json ambiguous_skips "${L[$BADI]}"; exit ;;
  esac
done
json found ${L[$BADI]}
