"""D5 hand check: a person checks 30 of the 542 alias calls, so that answer key isn't only from our own scripts.

    python -m tests.hand_check_aliases --run-id 973531f7fd4cfd5e --config meridian.toml --initials RH   # from Data-Pipeline/

Picks 30 of the 542 calls in week1/pair1/results/alias_resolution.csv with random.seed(1), so anyone gets the
same 30. For each one it shows the code around the call and every line in that file that gives `_` (or the alias)
a meaning, and asks which function it is on the marked line. It never shows the key's answer, so the check stays
independent. Each answer is timed and saved at once: quit any time with q, and rerun to carry on.

Writes labels/alias_hand_check_30.csv (CSV-POLICY.md: no people, checked_by holds the checker's initials).
"""
import csv
import random
import re
import time
from pathlib import Path

from meridian.common import HERE, data_root, log, parser

KEY = HERE.parent / "week1" / "pair1" / "results" / "alias_resolution.csv"
OUT = HERE / "labels" / "alias_hand_check_30.csv"
SEED, N = 1, 30
CHOICES = {"1": "ugettext", "2": "ugettext_lazy", "3": "ugettext_noop", "4": "other"}
COLUMNS = ["order", "path", "line", "col", "checker_answer", "key_symbol", "agrees", "note", "seconds", "checked_by"]


def pick(key_path=KEY):
    """The 30 calls, in the order they are checked. Same seed, same list."""
    with open(key_path, newline="", encoding="utf-8") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: (r["file"], int(r["line"]), int(r["col"])))
    random.seed(SEED)
    return [(i, r["file"], int(r["line"]), int(r["col"]), r["expected_symbol"]) for i, r in enumerate(random.sample(rows, N), 1)]


def binding_lines(lines, alias):
    """(line number, text) of every line that could give the alias a meaning: imports, assignments, defs, loops."""
    a = re.escape(alias)
    pattern = re.compile(rf"(\bimport\b.*\bas\s+{a}\b)|(^\s*{a}\s*=[^=])|(\bdef\s+{a}\s*\()|(\bfor\s+{a}\s+in\b)|"
                         rf"(\blambda\s+{a}\b)|(\bexcept\b.*\bas\s+{a}\b)|(\bwith\b.*\bas\s+{a}\b)")
    return [(n, t) for n, t in enumerate(lines, 1) if pattern.search(t)]


def show(order, path, line, col, lines):
    alias = re.match(r"\w+", lines[line - 1][col:]).group(0)
    print("\n" + "─" * 100)
    print(f"[{order}/{N}]  {path}   line {line}")
    print(f"\n  Lines in this file that give `{alias}` a meaning:")
    for n, t in binding_lines(lines, alias)[:8]:
        print(f"    {n:>5} | {t.rstrip()[:110]}")
    print(f"\n  The call (marked >>):")
    for n in range(max(1, line - 6), min(len(lines), line + 2) + 1):
        print(f"  {'>>' if n == line else '  '}{n:>5} | {lines[n - 1].rstrip()[:110]}")
    print(f"\n  What does `{alias}` mean on the marked line?")
    print("    1 = ugettext   2 = ugettext_lazy   3 = ugettext_noop   4 = something else, or can't tell")


def load(out):
    if not out.exists():
        return {}
    with open(out, newline="", encoding="utf-8") as f:
        return {int(r["order"]): r for r in csv.DictReader(f)}


def save(out, done):
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for order in sorted(done):
            w.writerow(done[order])
    tmp.replace(out)


def main(argv=None, ask=input):
    ap = parser(__doc__)
    ap.add_argument("--initials", required=True, help="the checker's initials, e.g. RH (CSV-POLICY.md: checked_by)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    repo_dir = data_root() / "snapshots" / a.run_id / "repos" / "django__django"
    if not repo_dir.exists():
        raise SystemExit(f"no Django checkout at {repo_dir}")
    done = load(a.out)
    todo = [p for p in pick() if p[0] not in done]
    print(f"{len(done)} of {N} already checked. {len(todo)} to go. Answer 1-4, then Enter. q = save and stop.")
    for order, path, line, col, key_symbol in todo:
        lines = (repo_dir / path).read_text(encoding="utf-8").split("\n")
        show(order, path, line, col, lines)
        start = time.perf_counter()
        answer = ""
        while answer not in CHOICES and answer != "q":
            answer = ask("  answer: ").strip().lower()
        if answer == "q":
            break
        note = ask("  note (optional, Enter to skip): ").strip()[:200]
        picked = CHOICES[answer]
        done[order] = dict(order=order, path=path, line=line, col=col, checker_answer=picked, key_symbol=key_symbol,
                           agrees="yes" if picked == key_symbol else "no", note=note,
                           seconds=round(time.perf_counter() - start, 1), checked_by=a.initials)
        save(a.out, done)
    agree = [r for r in done.values() if r["agrees"] == "yes"]
    log(f"\n{len(done)} of {N} checked · {len(agree)} agree with the key · saved {a.out}")
    if len(done) == N:
        secs = sum(float(r["seconds"]) for r in done.values())
        log(f"time: {secs / 60:.1f} min in total, {secs / N:.0f} s per call")
        for r in sorted(done.values(), key=lambda r: int(r["order"])):
            if r["agrees"] != "yes":
                log(f"  differs: {r['path']}:{r['line']} key {r['key_symbol']}, checker {r['checker_answer']}  {r['note']}")


if __name__ == "__main__":
    main()
