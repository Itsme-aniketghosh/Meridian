"""Resolver benchmark: go-to-definition on every call site of a deprecated method, with Jedi or pyright (LSP textDocument/definition).
Run: python3 code_resolve.py <async_setup_platforms|async_get_registry> <jedi|pyright>  (worktrees made by the commands in the report)
-> code_resolve_<symbol>_<tool>.csv. right = resolves to the deprecated definition; wrong = resolves elsewhere; none = no answer."""
import csv, json, os, re, subprocess, sys, time, statistics
from code_specs import SC, HERE
SYM, TOOL = sys.argv[1], sys.argv[2]
CFG = {"async_setup_platforms": (f"{SC}/wt_setup_platforms", r"\.async_setup_platforms\(", lambda f, l: f == "homeassistant/config_entries.py" and "def async_setup_platforms" in l),
       "async_get_registry": (f"{SC}/wt_get_registry", r"async_get_registry\(", lambda f, l: re.fullmatch(r"homeassistant/helpers/(entity|device|area)_registry\.py", f) and "def async_get_registry" in l)}
W, PAT, OK = CFG[SYM]
hits = subprocess.run(["git", "-C", W, "grep", "-n", "-P", PAT, "--", "homeassistant/components"], capture_output=True, text=True).stdout.splitlines()
sites = []
for h in hits:
    path, line, code = h.split(":", 2); col = code.index(SYM + "(")
    sites.append((path, int(line), col, code.strip()))
def deflines(f, ln):
    try: return open(f"{W}/{f}").read().splitlines()[ln - 1]
    except Exception: return ""
rows = []
if TOOL == "jedi":
    import jedi
    proj = jedi.Project(W)
    for path, line, col, code in sites:
        t = time.time()
        try:
            res = jedi.Script(path=f"{W}/{path}", project=proj).goto(line, col + 1, follow_imports=True)
            ans = [(os.path.relpath(str(d.module_path), W) if d.module_path else "?", d.line or 0) for d in res]
        except Exception as e: ans = [(f"error {type(e).__name__}", 0)]
        rows.append((path, line, code, ans, time.time() - t))
else:
    LS = f"{SC}/pyenv/bin/pyright-langserver"
    p = subprocess.Popen([LS, "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    mid = [0]
    def send(msg):
        b = json.dumps(msg).encode(); p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b); p.stdin.flush()
    def recv():
        hdr = b""
        while not hdr.endswith(b"\r\n\r\n"): hdr += p.stdout.read(1)
        n = int(re.search(rb"Content-Length: (\d+)", hdr).group(1)); return json.loads(p.stdout.read(n))
    def request(method, params):
        mid[0] += 1; send({"jsonrpc": "2.0", "id": mid[0], "method": method, "params": params})
        while True:
            m = recv()
            if m.get("id") == mid[0] and "method" not in m: return m.get("result")
            if "method" in m and "id" in m:  # server->client request (workspace/configuration etc.)
                send({"jsonrpc": "2.0", "id": m["id"], "result": [{"diagnosticMode": "openFilesOnly", "typeCheckingMode": "off", "pythonPath": f"{SC}/pyenv/bin/python"}] * len(m.get("params", {}).get("items", [1])) if m["method"] == "workspace/configuration" else None})
    uri = lambda f: "file://" + f"{W}/{f}"
    request("initialize", {"processId": os.getpid(), "rootUri": "file://" + W, "capabilities": {"workspace": {"configuration": True}}, "workspaceFolders": [{"uri": "file://" + W, "name": "w"}]})
    send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    opened = set()
    for path, line, col, code in sites:
        t = time.time()
        if path not in opened:
            send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri(path), "languageId": "python", "version": 1, "text": open(f"{W}/{path}").read()}}}); opened.add(path)
        res = request("textDocument/definition", {"textDocument": {"uri": uri(path)}, "position": {"line": line - 1, "character": col + 1}}) or []
        res = res if isinstance(res, list) else [res]
        ans = [(os.path.relpath((r.get("uri") or r.get("targetUri"))[7:], W), (r.get("range") or r.get("targetSelectionRange"))["start"]["line"] + 1) for r in res]
        rows.append((path, line, code, ans, time.time() - t))
    p.kill()
out = []
for path, line, code, ans, sec in rows:
    right = any(OK(f, deflines(f, ln)) for f, ln in ans)
    out.append({"file": path, "line": line, "verdict": "right" if right else ("none" if not ans else "wrong"), "answer": ";".join(f"{f}:{ln}" for f, ln in ans)[:200], "seconds": round(sec, 3), "code": code[:110]})
w = csv.DictWriter(open(f"{HERE}/code_resolve_{SYM}_{TOOL}.csv", "w", newline=""), fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
from collections import Counter
print(SYM, TOOL, "sites", len(out), dict(Counter(r["verdict"] for r in out)), "median s", round(statistics.median(r["seconds"] for r in out), 3), "total s", round(sum(r["seconds"] for r in out), 1))
