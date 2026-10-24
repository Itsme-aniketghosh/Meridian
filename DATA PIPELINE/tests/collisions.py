"""D6 graph check: Spark collision pairs, with 50 sampled for a hand-check (05-test "Blocking, chains", P3).

    python -m tests.collisions --run-id 70dd1dabfed544b8 --config meridian.toml      # from DATA PIPELINE/

Week 1's check (week1/pair2/spark/collisions.py) on the team run: a pair is two first-parent Spark commits since
SINCE, keyed to different Bug+Fixed tickets ([szz] fix), within WINDOW days, that edit the same function. A function
is (path, the name in git's hunk header), Scala named by a funcname driver passed on the command line: nothing is
written into the snapshot. Dropped first (PIPELINE-PLAN.md): generic names, and a name defined more than once in its
file, where the header can't say which one was edited.

Writes labels/spark_collision_pairs_50.csv: a seeded sample of 50 same-function pairs, hash(run_id, pair) order. A
rerun keeps verdicts already filled in. verdict: 'real' when both commits change code inside that function, 'no'
when either change sits outside it (the header named the wrong function), 'unclear'. checked_by: model or initials.
"""
import csv, hashlib, json, re, subprocess, tempfile
from datetime import date
from pathlib import Path

from meridian.common import data_root, git, handoff, load_config, log, parser

REPO = "apache/spark"
SINCE, WINDOW, MAX_FILES, SAMPLE = "2024-01-01", 30, 50, 50   # week 1's, so the numbers compare
SKIP = re.compile(r"(^|/)(tests?|test-data|benchmarks?)/|Suite\.scala$|_pb2\.pyi?$|/generated/|\.(md|txt|json)$")
REGISTRY = re.compile(r"SQLConf\.scala$")  # one giant config object: hunk headers there name the wrong thing
GENERIC = {"apply", "unapply", "__hash__", "__eq__", "__init__", "__repr__", "__str__", "hashCode", "equals", "toString"}
ATTR = "*.scala diff=scala\n*.java diff=java\n*.py diff=python\n"
SCALA_FN = (r"^[ \t]*((private|protected|override|final|implicit|lazy|\[[^]]*\]|[ \t])*"
            r"(def|class|object|trait|case class)[ \t]+[A-Za-z_][A-Za-z0-9_]*.*)$")
DEF = r"(?:\b(?:def|class|object|trait)\s+{0}\b|\b{0}\s*\([^;{{}}]*\)\s*(?:throws [\w., ]+)?\{{)"   # scala, python, java
HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "labels" / "spark_collision_pairs_50.csv"
COLUMNS = ["key_a", "key_b", "path", "function", "sha_a", "sha_b", "date_a", "date_b", "both_open", "inside",
           "evidence_url_a", "evidence_url_b", "checked_by", "verdict", "human_verdict", "note"]


def stream(path):
    """jsonl rows one at a time: ticket.jsonl is 130 MB, too much to hold at once on a small machine."""
    with open(path, encoding="utf-8", newline="") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def functions(co, attrs, shas):
    """{sha: files, fns {(path, name)}, hunks {(path, name): [(old start, old len, new start, new len)]}} from git's
    hunk headers, -U0, renames off (week 1's rules)."""
    got, cur, f = {}, None, None
    for i in range(0, len(shas), 200):
        out = git(co, "-c", f"core.attributesFile={attrs}", "-c", f"diff.scala.xfuncname={SCALA_FN}", "show", "-U0",
                  "--no-renames", "--no-color", "--format=@@@C %H", *shas[i:i + 200], "--", "*.scala", "*.java", "*.py")
        for line in out.split("\n"):
            if line.startswith("@@@C "):
                cur, f = got.setdefault(line[5:], {"files": set(), "fns": set(), "hunks": {}}), None
            elif line.startswith("+++ "):
                p = line[4:]
                f = None if p == "/dev/null" or SKIP.search(p) else p[2:]
                if f:
                    cur["files"].add(f)
            elif line.startswith("@@ ") and f:
                head = line.split("@@", 2)[2].strip()
                m = head and (re.search(r"\b(?:def|class|object|trait)\s+([A-Za-z_]\w*)", head)
                              or re.search(r"([A-Za-z_]\w*)\s*\(", head))
                if m and not REGISTRY.search(f):
                    cur["fns"].add((f, m.group(1)))
                    n = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
                    cur["hunks"].setdefault((f, m.group(1)), []).append(
                        (int(n[1]), int(n[2] or 1), int(n[3]), int(n[4] or 1)))
    return got


def indent(s):
    return len(s) - len(s.lstrip())


def within(lines, at, fn):
    """Line `at` (1-based) sits in fn's body: below fn's def, deeper than it, and no line between comes back to the
    def's depth (a closing ')' or ']' of a long signature excepted). A hunk header names the nearest def above a
    change, also when the change is a new method, a class field or a sibling function after it."""
    pat = re.compile(DEF.format(re.escape(fn)))
    d = next((d for d in range(at - 1, 0, -1) if pat.search(lines[d - 1])), None)
    if d is None:
        return False
    base = indent(lines[d - 1])
    return all(not l.strip() or indent(l) > base or l.lstrip()[:1] in ")]" for l in lines[d:at])


def edits_inside(co, sha, path, fn, hunks, texts):
    """Some non-blank line the commit adds or removes under fn's header lies inside fn (new side, else old side)."""
    def lines(rev):
        if (rev, path) not in texts:
            try:
                texts[(rev, path)] = git(co, "show", f"{rev}:{path}").split("\n")
            except subprocess.CalledProcessError:  # added or deleted file: no such side
                texts[(rev, path)] = []
        return texts[(rev, path)]
    for rev, start, n in [(sha, h[2], h[3]) for h in hunks] + [(f"{sha}^", h[0], h[1]) for h in hunks]:
        text = lines(rev)
        if any(text[at - 1].strip() and within(text, at, fn) for at in range(start, start + n) if at <= len(text)):
            return True
    return False


def main(run_id, config):
    cfg = load_config(config)
    out = handoff(cfg, run_id, data_root())
    co = data_root() / "snapshots" / run_id / "repos" / REPO.replace("/", "__")
    fix = cfg["szz"]["fix"]
    tix = {t["key"]: (t["created_at"], t["resolved_at"]) for t in stream(out / "ticket.jsonl")
           if t["tracker"] == "jira" and t["effective_type"] == fix["effective_type"] and t["resolution"] == fix["resolution"]}
    keys = {}
    for c in stream(out / "commit_ticket.jsonl"):
        if c["repo"] == REPO and c["key"] in tix:
            keys.setdefault(c["sha"], []).append(c["key"])
    when = {c["sha"]: c["authored_at"][:10] for c in stream(out / "git_commit.jsonl")
            if c["repo"] == REPO and c["first_parent"] and c["authored_at"] >= SINCE and c["sha"] in keys}
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "attributes").write_text(ATTR)
        found = functions(co, Path(tmp) / "attributes", sorted(when))
    fixes = sorted(({"sha": s, "date": when[s], "key": min(keys[s]), **found[s]} for s in found
                    if found[s]["files"] and len(found[s]["files"]) <= MAX_FILES), key=lambda c: (c["date"], c["sha"]))
    log(f"{len(when):,} Bug+Fixed keyed first-parent commits since {SINCE} Â· {len(fixes):,} touching 1 to {MAX_FILES} source files")

    day = date.fromisoformat
    same_file, pairs = set(), {}
    for i, a in enumerate(fixes):
        for b in fixes[i + 1:]:
            if (day(b["date"]) - day(a["date"])).days > WINDOW:
                break
            if a["key"] == b["key"]:
                continue
            if a["files"] & b["files"]:
                same_file.add(tuple(sorted((a["key"], b["key"]))))
            for path, fn in sorted(a["fns"] & b["fns"]):
                pairs.setdefault((*sorted((a["key"], b["key"])), path, fn), (a, b))
    texts = {}
    # shortcut: the Java pattern also counts a call followed by '{' (if (f(x)) {), so it drops a few real pairs too
    defined = lambda sha, path, fn: len(re.findall(DEF.format(re.escape(fn)), texts.get((sha, path)) or
                                                   texts.setdefault((sha, path), git(co, "show", f"{sha}:{path}"))))
    # defined more than once in the newer commit's version: the header can't say which one was edited
    kept = {k: v for k, v in pairs.items() if k[3] not in GENERIC and defined(v[1]["sha"], k[2], k[3]) == 1}
    by_pair = {}
    for k, v in sorted(kept.items()):
        by_pair.setdefault(k[:2], (k, v))      # one row per ticket pair: its first shared function
    both = lambda a, b: tix[a][0] < (tix[b][1] or "9") and tix[b][0] < (tix[a][1] or "9")
    sides = {}
    inside = lambda k, v: all(edits_inside(co, c["sha"], k[2], k[3], c["hunks"][(k[2], k[3])], sides) for c in v)
    real = {k[:2] for k, v in kept.items() if inside(k, v)}
    log(f"pairs within {WINDOW} days: same file {len(same_file):,} Â· same function {len({k[:2] for k in pairs}):,} Â· "
        f"after dropping generic and ambiguous names {len(by_pair):,} ({sum(both(*p) for p in by_pair):,} both open) Â· "
        f"both edits inside the function {len(real):,} ({sum(both(*p) for p in real):,} both open)")

    seed = lambda p: hashlib.sha256("\x1f".join((run_id, "collision", *p)).encode()).hexdigest()
    sample = sorted(sorted(by_pair, key=seed)[:SAMPLE])
    done = {}
    if OUT.exists():  # a rerun keeps the hand-check
        with open(OUT, newline="", encoding="utf-8") as f:
            done = {(r["key_a"], r["key_b"], r["path"], r["function"]): r for r in csv.DictReader(f)}
    rows = []
    for p in sample:
        (ka, kb, path, fn), (a, b) = by_pair[p]
        a, b = (a, b) if a["key"] == ka else (b, a)
        old = done.get((ka, kb, path, fn), {})
        rows.append({"key_a": ka, "key_b": kb, "path": path, "function": fn, "sha_a": a["sha"][:12], "sha_b": b["sha"][:12],
                     "date_a": a["date"], "date_b": b["date"], "both_open": int(both(ka, kb)),
                     "inside": int(inside((ka, kb, path, fn), (a, b))),
                     "evidence_url_a": f"https://github.com/apache/spark/commit/{a['sha']}",
                     "evidence_url_b": f"https://github.com/apache/spark/commit/{b['sha']}",
                     **{c: old.get(c, "") for c in ("checked_by", "verdict", "human_verdict", "note")}})
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for who, col in (("model read", "verdict"), ("hand-check", "human_verdict")):
        judged = [(r[col], r["inside"]) for r in rows if r[col] in ("real", "no")]
        if judged:
            hit = [v for v, ins in judged if ins]
            log(f"{who}, N={len(judged)}: hunk headers {sum(v == 'real' for v, _ in judged)} real Â· precision "
                f"{sum(v == 'real' for v, _ in judged) / len(judged):.2f} | inside rule keeps {len(hit)}: precision "
                f"{hit.count('real') / max(len(hit), 1):.2f}, recall {hit.count('real') / max(sum(v == 'real' for v, _ in judged), 1):.2f}")
    log(f"{len(rows)} sampled -> {OUT}")


if __name__ == "__main__":
    a = parser(__doc__).parse_args()
    main(a.run_id, a.config)
