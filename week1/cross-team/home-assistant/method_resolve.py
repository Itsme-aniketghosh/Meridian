"""Strict method-call benchmark on HA, step 2 (Pair 1's method_resolve.py, applied to HA).

Every use in method_sites.csv goes to Jedi (goto, follow_imports) and pyright (textDocument/definition),
the same calls and the same pinned versions as Pair 1 (jedi 0.19.2, pyright 1.1.414), using Pair 1's
language-server client (week1/pair1/scripts/q2_pyright.py).
  right      an answer lands on ConfigEntries.async_setup_platforms (homeassistant/config_entries.py).
             Stricter than Pair 1, who took any definition of the name: zwave_me has its own
             module-level async_setup_platforms, and landing there is wrong.
  wrong      answers, but none on that definition
  no_answer  nothing back, an error, or a timeout

Writes method_resolution.csv and method_resolution_summary.json
Usage: python3 method_resolve.py   (from this folder, with data/code/pyenv)
"""
import csv
import json
import math
import os
import queue
import statistics
import sys
import time
from collections import Counter, defaultdict

import jedi

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../pair1/scripts"))
from q2_pyright import LanguageServer, from_uri, to_uri  # noqa: E402

ROOT = os.path.abspath("data/code/wt_method_strict")
PYRIGHT = os.path.abspath("data/code/pyenv/bin/pyright-langserver")
TARGET = "homeassistant/config_entries.py"


def target_defs():
    with open("method_defs.csv", newline="") as fh:
        return {(d["file"], int(d["line"])) for d in csv.DictReader(fh) if d["file"] == TARGET}


def score(answers, defs):
    if not answers:
        return "no_answer"
    return "right" if any(a in defs for a in answers) else "wrong"


def key(s):
    return (s["file"], int(s["line"]), int(s["col"]))


def ask_jedi(sites):
    project, scripts, out = jedi.Project(ROOT), {}, {}
    for s in sites:
        path = os.path.join(ROOT, s["file"])
        if path not in scripts:
            with open(path, encoding="utf-8") as fh:
                scripts[path] = jedi.Script(code=fh.read(), path=path, project=project)
        t0 = time.perf_counter()
        try:
            names, error = scripts[path].goto(int(s["line"]), int(s["col"]), follow_imports=True), ""
        except Exception as exc:
            names, error = [], type(exc).__name__
        seconds = time.perf_counter() - t0
        answers = sorted({(os.path.relpath(str(n.module_path), ROOT), n.line) for n in names if n.module_path and n.line})
        out[key(s)] = (answers, error, seconds)
    return out


def ask_pyright(sites):
    server = LanguageServer([PYRIGHT, "--stdio"])
    t0 = time.perf_counter()
    server.request("initialize", {
        "processId": os.getpid(), "rootUri": to_uri(ROOT),
        "workspaceFolders": [{"uri": to_uri(ROOT), "name": "ha"}],
        "capabilities": {"textDocument": {"definition": {"linkSupport": False}}},
    }, timeout=600)
    server.notify("initialized", {})
    startup = time.perf_counter() - t0
    out, by_file = {}, defaultdict(list)
    for s in sites:
        by_file[s["file"]].append(s)
    for rel, items in sorted(by_file.items()):
        path = os.path.join(ROOT, rel)
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
                }, timeout=120), ""
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
                answers.append((os.path.relpath(from_uri(target), ROOT), rng["start"]["line"] + 1))
            out[key(s)] = (sorted(set(answers)), error, seconds)
        server.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    try:
        server.request("shutdown", None, timeout=10)
        server.notify("exit", None)
    except Exception:
        server.proc.kill()
    return out, startup


def show(answers, error):
    return " | ".join(f"{f}:{l}" for f, l in answers) or error or "(nothing)"


def p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def main():
    with open("method_sites.csv", newline="") as fh:
        sites = list(csv.DictReader(fh))
    defs = target_defs()
    print(f"{len(sites)} uses; right = {sorted(defs)}")
    t0 = time.perf_counter()
    jedi_out = ask_jedi(sites)
    jedi_total = time.perf_counter() - t0
    print(f"Jedi done in {jedi_total:.1f}s; starting pyright...")
    t0 = time.perf_counter()
    pyright_out, startup = ask_pyright(sites)
    pyright_total = time.perf_counter() - t0

    rows, agree = [], Counter()
    for s in sites:
        j_ans, j_err, j_sec = jedi_out[key(s)]
        p_ans, p_err, p_sec = pyright_out[key(s)]
        j_res, p_res = score(j_ans, defs), score(p_ans, defs)
        agree[(j_res == "right", p_res == "right")] += 1
        rows.append([s["file"], s["line"], s["col"], s["kind"], s["receiver"], s["bucket"],
                     j_res, show(j_ans, j_err), round(j_sec, 4), p_res, show(p_ans, p_err), round(p_sec, 4)])

    def counts(col, sub):
        c = Counter(r[col] for r in sub)
        return {k: c[k] for k in ("right", "wrong", "no_answer")}
    summary = {
        "snapshot": sites[0]["snapshot"], "uses": len(rows),
        "jedi": counts(6, rows), "pyright": counts(9, rows),
        "by_bucket": {b: {"uses": sum(r[5] == b for r in rows), "jedi": counts(6, [r for r in rows if r[5] == b]),
                          "pyright": counts(9, [r for r in rows if r[5] == b])} for b in sorted({r[5] for r in rows})},
        "by_receiver": {rc: {"uses": sum(r[4] == rc for r in rows), "jedi_right": sum(r[4] == rc and r[6] == "right" for r in rows),
                             "pyright_right": sum(r[4] == rc and r[9] == "right" for r in rows)} for rc in sorted({r[4] for r in rows})},
        "agreement": {"both right": agree[(True, True)], "only jedi": agree[(True, False)],
                      "only pyright": agree[(False, True)], "neither": agree[(False, False)]},
        "seconds": {"jedi_total": round(jedi_total, 2), "jedi_median_per_use": round(statistics.median(r[8] for r in rows), 4),
                    "jedi_p95_per_use": round(p95([r[8] for r in rows]), 4), "pyright_startup": round(startup, 2),
                    "pyright_total": round(pyright_total, 2), "pyright_median_per_use": round(statistics.median(r[11] for r in rows), 4),
                    "pyright_p95_per_use": round(p95([r[11] for r in rows]), 4)},
    }
    with open("method_resolution.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["file", "line", "col", "kind", "receiver", "bucket", "jedi_result", "jedi_answer", "jedi_seconds",
                    "pyright_result", "pyright_answer", "pyright_seconds"])
        w.writerows(rows)
    with open("method_resolution_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    print(json.dumps(summary, indent=2))
    print("not right for either tool:")
    for r in rows:
        if r[6] != "right" or r[9] != "right":
            print(f"  {r[0]}:{r[1]}  {r[4]}  jedi {r[6]} ({r[7][:60]})  pyright {r[9]} ({r[10][:60]})")
    print("saved method_resolution.csv, method_resolution_summary.json")


if __name__ == "__main__":
    main()
