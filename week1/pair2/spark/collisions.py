"""Week-1 check: collision pairs in Spark (05-test, "Blocking, chains" and P3).

A pair = two first-parent commits keyed to different Bug+Fixed tickets, within WINDOW days,
that edit the same function. Function = git's hunk header, with a Scala funcname driver
configured in .git/info/attributes. Also reported: same file only, and pairs whose tickets
were open at the same time (created before the other resolved), which is what P3 fires on.

Needs merged/data/spark_jira.sqlite from merged/build.py (tickets as of the snapshot) and a full
(not blobless) clone in spark/repos/.

Side effect: writes .git/info/attributes and a diff.scala.xfuncname config into the Spark clone,
so git can name Scala functions in hunk headers. It touches nothing outside spark/repos/.
"""
import collections, json, re, sqlite3, subprocess, sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE / "repos" / (sys.argv[1] if len(sys.argv) > 1 else "apache_spark")
SPARK_SHA = "f868de6914d0e6ede86309648614bb436b2b82e6"  # snapshot used in pair2/README.md
SINCE, WINDOW, MAX_FILES = "2024-01-01", 30, 50
SKIP = re.compile(r"(^|/)(tests?|test-data|benchmarks?)/|Suite\.scala$|_pb2\.pyi?$|/generated/|\.(md|txt|json)$")
KEY = re.compile(r"SPARK-\d+")
REGISTRY = re.compile(r"SQLConf\.scala$")  # one giant config object: hunk headers there name the wrong thing

ATTR = "*.scala diff=scala\n*.java diff=java\n*.py diff=python\n"
SCALA_FN = (r"^[ \t]*((private|protected|override|final|implicit|lazy|\[[^]]*\]|[ \t])*"
            r"(def|class|object|trait|case class)[ \t]+[A-Za-z_][A-Za-z0-9_]*.*)$")


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, errors="replace").stdout


def commits():
    (REPO / ".git" / "info").mkdir(exist_ok=True)
    # writes into the Spark clone (not our repo): per-language funcname drivers for hunk headers
    (REPO / ".git" / "info" / "attributes").write_text(ATTR)
    git("config", "diff.scala.xfuncname", SCALA_FN)
    out = git("log", "--first-parent", f"--since={SINCE}", "-p", "-U0", "--no-renames",
              "--format=@@@C %H %ad %s", "--date=short", SPARK_SHA, "--", "*.scala", "*.java", "*.py")
    cur, f = None, None
    for line in out.splitlines():
        if line.startswith("@@@C "):
            if cur:
                yield cur
            sha, d, subj = line[5:].split(" ", 2)
            cur, f = {"sha": sha, "date": d, "keys": sorted(set(KEY.findall(subj))), "files": set(), "fns": set()}, None
        elif line.startswith("+++ "):
            p = line[4:]
            f = None if p == "/dev/null" or SKIP.search(p) else p[2:]
            if f:
                cur["files"].add(f)
        elif line.startswith("@@ ") and f:
            head = line.split("@@", 2)[2].strip()
            if head:
                m = (re.search(r"\b(?:def|class|object|trait)\s+([A-Za-z_]\w*)", head)  # scala / python
                     or re.search(r"([A-Za-z_]\w*)\s*\(", head))                        # java method
                if m and not REGISTRY.search(f):
                    cur["fns"].add((f, m.group(1)))
    if cur:
        yield cur


def main():
    (HERE / "results").mkdir(exist_ok=True)
    db = sqlite3.connect(HERE.parent / "merged" / "data" / "spark_jira.sqlite")
    tix = {k: {"type": t, "res": r, "created": c, "resolved": d} for k, t, r, c, d in
           db.execute("select key, type, resolution, created_at, resolved_at from tickets")}
    fixes = []
    n = 0
    for c in commits():
        n += 1
        ks = [k for k in c["keys"] if k in tix and tix[k]["type"] == "Bug" and tix[k]["res"] == "Fixed"]
        if ks and len(c["files"]) <= MAX_FILES and c["files"]:
            c["key"] = ks[0]
            fixes.append(c)
    fixes.sort(key=lambda c: c["date"])
    print(f"commits since {SINCE}: {n}, Bug+Fixed fix commits (<= {MAX_FILES} src files): {len(fixes)}")

    day = lambda s: date.fromisoformat(s[:10])
    same_file, same_fn, overlap = set(), set(), set()
    examples = []
    for i, a in enumerate(fixes):
        for b in fixes[i + 1:]:
            if (day(b["date"]) - day(a["date"])).days > WINDOW:
                break
            if a["key"] == b["key"]:
                continue
            pair = tuple(sorted((a["key"], b["key"])))
            if a["files"] & b["files"]:
                same_file.add(pair)
            shared = a["fns"] & b["fns"]
            if shared:
                same_fn.add(pair)
                ta, tb = tix[a["key"]], tix[b["key"]]
                if ta["created"] < (tb["resolved"] or "9") and tb["created"] < (ta["resolved"] or "9"):
                    overlap.add(pair)
                    if len(examples) < 15:
                        examples.append((pair, a["sha"][:10], b["sha"][:10], sorted(shared)[:2]))
    print(f"pairs within {WINDOW} days: same file {len(same_file)}, same function {len(same_fn)}, "
          f"same function AND both open at once {len(overlap)}")
    fn_counts = collections.Counter(fn for p in fixes for fn in p["fns"])
    print("most-fixed functions", fn_counts.most_common(8))
    for e in examples:
        print("  ", e)
    json.dump({"same_fn": sorted(same_fn), "overlap": sorted(overlap), "examples": examples},
              open(HERE / "results" / "collisions.json", "w"), indent=1)


if __name__ == "__main__":
    main()
