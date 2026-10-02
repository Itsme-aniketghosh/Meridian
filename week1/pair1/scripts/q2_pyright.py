"""Q2 step 3: ask pyright (via its language server) what each aliased _() call is, and score it.

right      pyright lands on the line that defines the old function
wrong      pyright lands anywhere else
no_answer  pyright returns nothing, errors, or times out

Then merges with results/q2_jedi.csv into results/alias_resolution.csv.
Usage: python scripts/q2_pyright.py [path-to-django]
"""
import csv
import json
import os
import queue
import shutil
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from urllib.parse import quote, unquote, urlparse

REPO = os.path.abspath(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django"))
TRANSLATION = os.path.join("django", "utils", "translation", "__init__.py")
PASS_BAR = 0.95


def to_uri(path):
    return "file://" + quote(os.path.abspath(path))


def from_uri(uri):
    return unquote(urlparse(uri).path)


class LanguageServer:
    """Minimal client for a language server talking JSON-RPC over stdin/stdout."""

    def __init__(self, cmd):
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL)
        self.write_lock = threading.Lock()
        self.id_lock = threading.Lock()
        self.next_id = 0
        self.waiting = {}
        threading.Thread(target=self._read_loop, daemon=True).start()

    def _send(self, message):
        body = json.dumps(message).encode()
        with self.write_lock:
            self.proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self.proc.stdin.flush()

    def _read_loop(self):
        out = self.proc.stdout
        while True:
            headers = {}
            while True:
                line = out.readline()
                if not line:
                    return
                line = line.decode().strip()
                if not line:
                    break
                name, value = line.split(":", 1)
                headers[name.lower()] = value.strip()
            message = json.loads(out.read(int(headers["content-length"])))

            if "id" in message and "method" in message:
                # The server is asking us something. Answer so it doesn't wait.
                if message["method"] == "workspace/configuration":
                    items = message.get("params", {}).get("items", [])
                    result = [{} for item in items]
                else:
                    result = None
                self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
            elif "id" in message:
                box = self.waiting.get(message["id"])
                if box is not None:
                    box.put(message)
            # Notifications (logs, diagnostics) are ignored.

    def request(self, method, params, timeout=60):
        with self.id_lock:
            self.next_id += 1
            rid = self.next_id
            box = queue.Queue()
            self.waiting[rid] = box
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            message = box.get(timeout=timeout)
        finally:
            self.waiting.pop(rid, None)
        if "error" in message:
            raise RuntimeError(message["error"].get("message", "error"))
        return message.get("result")

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})


def main():
    exe = shutil.which("pyright-langserver")
    if exe is None:
        sys.exit("pyright-langserver not found. Is the (meridian) environment on?")

    with open("results/alias_calls.csv", newline="") as fh:
        calls = list(csv.DictReader(fh))
    with open("results/sites_other.csv", newline="") as fh:
        def_lines = {r["symbol"]: int(r["line"]) for r in csv.DictReader(fh)}

    by_file = defaultdict(list)
    for c in calls:
        by_file[c["file"]].append(c)

    print("starting pyright (first run may download Node.js, please wait)...")
    server = LanguageServer([exe, "--stdio"])
    t_start = time.perf_counter()
    server.request("initialize", {
        "processId": os.getpid(),
        "rootUri": to_uri(REPO),
        "workspaceFolders": [{"uri": to_uri(REPO), "name": "django"}],
        "capabilities": {"textDocument": {"definition": {"linkSupport": False}}},
    }, timeout=600)
    server.notify("initialized", {})
    startup = time.perf_counter() - t_start
    print(f"pyright ready after {startup:.1f}s")

    rows, times = [], []
    t_calls = time.perf_counter()
    for rel, items in sorted(by_file.items()):
        path = os.path.join(REPO, rel)
        uri = to_uri(path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        server.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "python", "version": 1, "text": text}})

        for c in items:
            expected = c["expected_symbol"]
            t0 = time.perf_counter()
            try:
                result = server.request("textDocument/definition", {
                    "textDocument": {"uri": uri},
                    "position": {"line": int(c["line"]) - 1, "character": int(c["col"])},
                })
                error = ""
            except queue.Empty:
                result, error = None, "timeout"
            except Exception as exc:
                result, error = None, type(exc).__name__
            seconds = time.perf_counter() - t0
            times.append(seconds)

            locations = result if isinstance(result, list) else ([result] if result else [])
            def_file = def_line = ""
            if locations:
                first = locations[0]
                target = first.get("uri") or first.get("targetUri")
                rng = first.get("range") or first.get("targetSelectionRange") or first.get("targetRange")
                def_file = os.path.relpath(from_uri(target), REPO)
                def_line = rng["start"]["line"] + 1
                right = def_file == TRANSLATION and def_line == def_lines.get(expected)
                outcome = "right" if right else "wrong"
            else:
                outcome = "no_answer"
            rows.append([c["file"], c["line"], c["col"], expected, outcome,
                         def_file, def_line, len(locations), error, round(seconds, 4)])

        server.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    call_time = time.perf_counter() - t_calls
    try:
        server.request("shutdown", None, timeout=10)
        server.notify("exit", None)
    except Exception:
        server.proc.kill()

    with open("results/q2_pyright.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "line", "col", "expected_symbol", "result",
                         "pyright_def_file", "pyright_def_line", "n_answers", "error", "seconds"])
        writer.writerows(rows)

    n = len(rows)
    counts = Counter(r[4] for r in rows)
    print(f"\ncalls: {n}")
    for outcome in ("right", "wrong", "no_answer"):
        print(f"  {outcome:10} {counts[outcome]}")
    print(f"right: {counts['right']} of {n} = {counts['right'] / n:.3f}  (pass bar {PASS_BAR})")
    print(f"verdict: {'PASS' if counts['right'] / n >= PASS_BAR else 'FAIL'}")
    print(f"time: startup {startup:.1f}s, calls {call_time:.1f}s total, "
          f"median {statistics.median(times) * 1000:.1f} ms per call, "
          f"p95 {sorted(times)[int(0.95 * n) - 1] * 1000:.1f} ms")

    print("\nright by expected symbol:")
    for sym in sorted({r[3] for r in rows}):
        sub = [r for r in rows if r[3] == sym]
        print(f"  {sym:15} {sum(r[4] == 'right' for r in sub)} of {len(sub)}")

    bad = [r for r in rows if r[4] != "right"]
    if bad:
        print("\nwhere pyright landed instead:",
              dict(Counter(f"{r[5]}:{r[6]}" if r[5] else (r[8] or "(nothing)") for r in bad).most_common(8)))
        print("\nfirst 10 not right:")
        for r in bad[:10]:
            print(f"  {r[0]}:{r[1]}:{r[2]}  expected {r[3]} -> {r[4]}: {r[5]}:{r[6]} {r[8]}")

    # Merge with Jedi into the deliverable
    if not os.path.exists("results/q2_jedi.csv"):
        print("\nresults/q2_jedi.csv not found; run q2_jedi.py, then this again to merge.")
        return
    with open("results/q2_jedi.csv", newline="") as fh:
        jedi = {(r["file"], r["line"], r["col"]): r for r in csv.DictReader(fh)}

    merged, agree = [], Counter()
    for r in rows:
        j = jedi.get((r[0], str(r[1]), str(r[2])), {})
        j_res = j.get("result", "missing")
        jedi_answer = j.get("jedi_full_name", "")
        if not jedi_answer and j:
            jedi_answer = f"{j.get('jedi_def_file')}:{j.get('jedi_def_line')}"
        merged.append([r[0], r[1], r[2], r[3], j_res, jedi_answer, r[4],
                       f"{r[5]}:{r[6]}" if r[5] else r[8]])
        agree[(j_res == "right", r[4] == "right")] += 1

    with open("results/alias_resolution.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "line", "col", "expected_symbol",
                         "jedi_result", "jedi_answer", "pyright_result", "pyright_answer"])
        writer.writerows(merged)

    print("\nboth tools, per call:")
    print(f"  both right    {agree[(True, True)]}")
    print(f"  only Jedi     {agree[(True, False)]}")
    print(f"  only pyright  {agree[(False, True)]}")
    print(f"  neither       {agree[(False, False)]}")
    print("wrote results/alias_resolution.csv")


if __name__ == "__main__":
    main()
