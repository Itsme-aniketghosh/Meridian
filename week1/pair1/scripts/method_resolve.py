"""Method-call benchmark, step 2: ask Jedi and pyright what each method use is, and score them.

For every use in results/method_sites.csv (calls, and assignments that replace the method),
both tools are asked where the method name leads,
the same way as in Q2 (Jedi goto with follow_imports, pyright textDocument/definition):
  right      an answer lands on a Django definition of that method (results/method_defs.csv).
             For is_authenticated / is_anonymous, the User and AnonymousUser versions both count:
             both are the old API
  wrong      answers, but none of them on a definition of that method
  no_answer  nothing back, an error, or a timeout

Each benchmark runs against its own snapshot worktree. Reuses the language-server client from
q2_pyright.py.

Writes:
  results/method_resolution.csv           both tools' answers for every call
  results/method_resolution_summary.json  counts per benchmark and tool, agreement, timing

Usage: python scripts/method_resolve.py [path-to-django]
"""
import csv
import json
import math
import os
import queue
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict

import jedi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from q2_pyright import LanguageServer, from_uri, to_uri  # noqa: E402

REPO = os.path.abspath(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django"))
RESULTS = ("right", "wrong", "no_answer")


def score(answers, method, defs):
    """answers: list of (file, line). Right if any lands on a definition of this method."""
    if not answers:
        return "no_answer"
    return "right" if any((f, l) in defs[method] for f, l in answers) else "wrong"


def ask_jedi(root, sites):
    project = jedi.Project(root)
    out, scripts = {}, {}
    for s in sites:
        path = os.path.join(root, s["file"])
        if path not in scripts:
            with open(path, encoding="utf-8") as fh:
                scripts[path] = jedi.Script(code=fh.read(), path=path, project=project)
        t0 = time.perf_counter()
        try:
            names, error = scripts[path].goto(int(s["line"]), int(s["col"]), follow_imports=True), ""
        except Exception as exc:
            names, error = [], type(exc).__name__
        seconds = time.perf_counter() - t0
        # sorted: Jedi doesn't always list several answers in the same order
        answers = sorted({(os.path.relpath(str(n.module_path), root), n.line) for n in names if n.module_path and n.line})
        out[key(s)] = (answers, error, seconds)
    return out


def ask_pyright(root, sites):
    exe = shutil.which("pyright-langserver") or os.path.join(os.path.dirname(sys.executable), "pyright-langserver")
    server = LanguageServer([exe, "--stdio"])
    t0 = time.perf_counter()
    server.request("initialize", {
        "processId": os.getpid(),
        "rootUri": to_uri(root),
        "workspaceFolders": [{"uri": to_uri(root), "name": "django"}],
        "capabilities": {"textDocument": {"definition": {"linkSupport": False}}},
    }, timeout=600)
    server.notify("initialized", {})
    startup = time.perf_counter() - t0

    out = {}
    by_file = defaultdict(list)
    for s in sites:
        by_file[s["file"]].append(s)
    for rel, items in sorted(by_file.items()):
        path = os.path.join(root, rel)
        uri = to_uri(path)
        with open(path, encoding="utf-8") as fh:
            server.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": "python", "version": 1, "text": fh.read()}})
        for s in items:
            t1 = time.perf_counter()
            try:
                result, error = server.request("textDocument/definition", {
                    "textDocument": {"uri": uri},
                    "position": {"line": int(s["line"]) - 1, "character": int(s["col"])},
                }), ""
            except queue.Empty:
                result, error = None, "timeout"
            except Exception as exc:
                result, error = None, type(exc).__name__
            seconds = time.perf_counter() - t1
            locations = result if isinstance(result, list) else ([result] if result else [])
            answers = []
            for loc in locations:
                target = loc.get("uri") or loc.get("targetUri")
                rng = loc.get("range") or loc.get("targetSelectionRange") or loc.get("targetRange")
                answers.append((os.path.relpath(from_uri(target), root), rng["start"]["line"] + 1))
            out[key(s)] = (sorted(set(answers)), error, seconds)
        server.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    try:
        server.request("shutdown", None, timeout=10)
        server.notify("exit", None)
    except Exception:
        server.proc.kill()
    return out, startup


def key(s):
    return (s["benchmark"], s["file"], int(s["line"]), int(s["col"]))


def show(answers, error):
    return " | ".join(f"{f}:{l}" for f, l in answers) or error or "(nothing)"


def p95(values):
    """Nearest-rank p95, so it stays right for tiny N (with 2 values it's the larger one)."""
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def main():
    with open("results/method_sites.csv", newline="") as fh:
        sites = list(csv.DictReader(fh))
    defs = defaultdict(lambda: defaultdict(set))   # benchmark -> method -> {(file, line)}
    with open("results/method_defs.csv", newline="") as fh:
        for d in csv.DictReader(fh):
            defs[d["benchmark"]][d["method"]].add((d["file"], int(d["line"])))

    rows, summary = [], {"benchmarks": {}}
    for bench in sorted({s["benchmark"] for s in sites}):
        mine = [s for s in sites if s["benchmark"] == bench]
        root = f"{REPO}-{bench}"
        print(f"{bench}: {len(mine)} uses, snapshot {mine[0]['snapshot']}")
        t0 = time.perf_counter()
        jedi_out = ask_jedi(root, mine)
        jedi_total = time.perf_counter() - t0
        print(f"  Jedi done in {jedi_total:.1f}s; starting pyright (the first run may download it)...")
        t0 = time.perf_counter()
        pyright_out, startup = ask_pyright(root, mine)
        pyright_total = time.perf_counter() - t0

        counts = {"jedi": Counter(), "pyright": Counter()}
        agree = Counter()
        for s in mine:
            j_ans, j_err, j_sec = jedi_out[key(s)]
            p_ans, p_err, p_sec = pyright_out[key(s)]
            j_res = score(j_ans, s["method"], defs[bench])
            p_res = score(p_ans, s["method"], defs[bench])
            counts["jedi"][j_res] += 1
            counts["pyright"][p_res] += 1
            agree[(j_res == "right", p_res == "right")] += 1
            rows.append([bench, s["file"], s["line"], s["col"], s["method"], s["kind"], s["receiver"],
                         j_res, show(j_ans, j_err), round(j_sec, 4),
                         p_res, show(p_ans, p_err), round(p_sec, 4)])

        bench_rows = [r for r in rows if r[0] == bench]
        by_kind = {}
        for kind in sorted({r[5] for r in bench_rows}):
            sub = [r for r in bench_rows if r[5] == kind]
            by_kind[kind] = {"uses": len(sub), "jedi_right": sum(1 for r in sub if r[7] == "right"),
                             "pyright_right": sum(1 for r in sub if r[10] == "right")}
        summary["benchmarks"][bench] = {
            "snapshot": mine[0]["snapshot"],
            "uses": len(mine),
            "by_kind": by_kind,
            "jedi": {r: counts["jedi"][r] for r in RESULTS},
            "pyright": {r: counts["pyright"][r] for r in RESULTS},
            "agreement": {"both right": agree[(True, True)], "only jedi": agree[(True, False)],
                          "only pyright": agree[(False, True)], "neither": agree[(False, False)]},
            "seconds": {
                "jedi_total": round(jedi_total, 2),
                "jedi_median_per_use": round(statistics.median(r[9] for r in bench_rows), 4),
                "jedi_p95_per_use": round(p95([r[9] for r in bench_rows]), 4),
                "pyright_startup": round(startup, 2),
                "pyright_total": round(pyright_total, 2),
                "pyright_median_per_use": round(statistics.median(r[12] for r in bench_rows), 4),
                "pyright_p95_per_use": round(p95([r[12] for r in bench_rows]), 4),
            },
        }
        b = summary["benchmarks"][bench]
        print(f"  Jedi     right {b['jedi']['right']} of {len(mine)}, wrong {b['jedi']['wrong']}, "
              f"no answer {b['jedi']['no_answer']}")
        print(f"  pyright  right {b['pyright']['right']} of {len(mine)}, wrong {b['pyright']['wrong']}, "
              f"no answer {b['pyright']['no_answer']}")
        print(f"  by kind: {b['by_kind']}")
        print(f"  agreement: {b['agreement']}")

    total = Counter()
    for r in rows:
        total[("uses", r[5])] += 1
        total[("jedi", r[5])] += r[7] == "right"
        total[("pyright", r[5])] += r[10] == "right"
    kinds = sorted({r[5] for r in rows})
    summary["all_uses"] = {
        "uses": len(rows),
        "jedi_right": sum(1 for r in rows if r[7] == "right"),
        "pyright_right": sum(1 for r in rows if r[10] == "right"),
        "by_kind": {k: {"uses": total[("uses", k)], "jedi_right": total[("jedi", k)],
                        "pyright_right": total[("pyright", k)]} for k in kinds},
    }

    with open("results/method_resolution.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["benchmark", "file", "line", "col", "method", "kind", "receiver",
                         "jedi_result", "jedi_answer", "jedi_seconds",
                         "pyright_result", "pyright_answer", "pyright_seconds"])
        writer.writerows(rows)
    with open("results/method_resolution_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")

    a = summary["all_uses"]
    print(f"\nall {a['uses']} uses: Jedi right {a['jedi_right']}, pyright right {a['pyright_right']}; by kind {a['by_kind']}")
    print("\nper use (Jedi / pyright):")
    for r in rows:
        print(f"  {r[0]:8} {r[1]}:{r[2]}  {r[5]:6} {r[6]}.{r[4]}  ->  {r[7]} / {r[10]}")
    print("saved results/method_resolution.csv, method_resolution_summary.json")


if __name__ == "__main__":
    main()
