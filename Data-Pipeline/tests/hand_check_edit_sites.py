"""Freeze the Django answer key (D4): hand-check every edit line, one at a time, timed.

    python -m tests.hand_check_edit_sites --run-id <run_id> --config meridian.toml --initials XX   # from Data-Pipeline/

Reads week1/pair1/results/edit_sites.csv (the 265 lines) and week1/pair1/results/hand_check_30.csv (the 30 already
checked in week 1, carried over). Opens each remaining line in VS Code at the snapshot's django/django checkout and
shows only the file and line, never the scanner's answer. Saves after every answer to
labels/django_edit_sites_265.csv. Type q at any prompt to stop: the next run resumes where you left off.

y: the line imports, calls or aliases ugettext / ugettext_lazy / ugettext_noop / ungettext / ungettext_lazy in code.
n: a definition, a comment, a string, or none of the five names on that line.
"""
import argparse, csv, json, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from meridian.common import data_root

WEEK1 = HERE.parent / "week1" / "pair1" / "results"
OUT = HERE / "labels" / "django_edit_sites_265.csv"
COLUMNS = ["path", "line", "kind", "symbol", "is_edit_line", "note", "seconds", "checked_by", "checked_in"]


def load_key():
    with open(WEEK1 / "edit_sites.csv", newline="", encoding="utf-8") as f:
        return [{"path": r["file"], "line": int(r["line"]), "kind": r["kind"], "symbol": r["symbol"]}
                for r in csv.DictReader(f)]


def load_week1():
    with open(WEEK1 / "hand_check_30.csv", newline="", encoding="utf-8") as f:
        return {(r["file"], int(r["line"])): r for r in csv.DictReader(f)}


def load_progress():
    if not OUT.exists():
        return {}
    with open(OUT, newline="", encoding="utf-8") as f:
        return {(r["path"], int(r["line"])): r for r in csv.DictReader(f) if r["is_edit_line"]}


def save(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: (r["path"], int(r["line"]))):
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    tmp.replace(OUT)


def ask(prompt, allowed=None):
    while True:
        answer = input(prompt).strip()
        if answer.lower() == "q":
            return None
        if allowed is None or answer.lower() in allowed:
            return answer.lower() if allowed else answer


def summary(rows):
    done = [r for r in rows.values() if r["is_edit_line"]]
    yes = sum(r["is_edit_line"] == "yes" for r in done)
    seconds = sum(float(r["seconds"] or 0) for r in done)
    new = [r for r in done if r["checked_in"] != "week1"]
    out = {"checked": len(done), "of": len(rows), "yes": yes, "no": len(done) - yes,
           "total_minutes_all": round(seconds / 60, 1),
           "minutes_this_key_new_lines": round(sum(float(r["seconds"] or 0) for r in new) / 60, 1),
           "new_lines_checked": len(new)}
    print(json.dumps(out, indent=2))
    if len(done) == len(rows):
        print(f"\nAll {len(rows)} checked. Key frozen in {OUT.relative_to(HERE.parent)}")
    else:
        print(f"\n{len(rows) - len(done)} left. Run the same command again to resume.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--initials", required=True, help="2-3 letters, e.g. RS (no names: CSV-POLICY.md)")
    a = p.parse_args(argv)
    if not (2 <= len(a.initials) <= 3 and a.initials.isalpha()):
        sys.exit("--initials must be 2-3 letters")
    repo = data_root() / "snapshots" / a.run_id / "repos" / "django__django"
    if not repo.exists():
        sys.exit(f"no checkout at {repo}. Run meridian.collect for {a.run_id} first")

    key, week1, progress = load_key(), load_week1(), load_progress()
    rows = {}
    for k in key:
        pos = (k["path"], k["line"])
        row = dict(k, is_edit_line="", note="", seconds="", checked_by="", checked_in="")
        if pos in progress:
            row.update({c: progress[pos][c] for c in ("is_edit_line", "note", "seconds", "checked_by", "checked_in")})
        elif pos in week1:
            w = week1[pos]
            row.update(is_edit_line="yes" if w["is_call_site"] == "y" else "no", note=w["note"],
                       seconds=w["seconds"], checked_by=a.initials.upper(), checked_in="week1")
        rows[pos] = row
    save(rows)

    todo = [pos for pos, r in rows.items() if not r["is_edit_line"]]
    print(f"{len(rows)} lines in the key · {len(rows) - len(todo)} already checked · {len(todo)} to go")
    if not todo:
        return summary(rows)
    code = shutil.which("code")
    if not code:
        print("(VS Code's `code` command wasn't found: open each file yourself at the line shown)")
    print("y = imports, calls or aliases an old name in code · n = definition, comment, string, or not there")
    print("q at any prompt stops; progress is saved")
    if ask("\nPress Enter to start the timer (or q to quit)... ") is None:
        return summary(rows)

    for i, pos in enumerate(todo, 1):
        t0 = time.perf_counter()
        print(f"\n[{i}/{len(todo)}] {pos[0]}:{pos[1]}")
        if code:
            subprocess.run([code, "-g", f"{repo / pos[0]}:{pos[1]}"])
        answer = ask("  Real line to edit? (y/n, q to stop): ", {"y", "n"})
        if answer is None:
            break
        note = ask("  Note (Enter to skip): ")
        if note is None:
            note = ""
        rows[pos].update(is_edit_line="yes" if answer == "y" else "no", note=note,
                         seconds=round(time.perf_counter() - t0, 1), checked_by=a.initials.upper(),
                         checked_in="day4")
        save(rows)
        print(f"  saved ({rows[pos]['seconds']}s)")
    summary(rows)


if __name__ == "__main__":
    main()
