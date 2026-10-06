"""Strict method-call benchmark on HA, step 3 (Pair 1's method_receivers.py, applied to HA).

For every use, each tool is asked what the receiver is (the `config_entries` before the dot):
Jedi with infer, pyright with hover. If a tool knew the receiver was a ConfigEntries and still gave
no answer, our setup would be broken; if it didn't know, the miss is the tool's real limit.

Writes method_receivers.csv
Usage: python3 method_receivers.py
"""
import csv
import os
import sys
from collections import Counter

import jedi

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../pair1/scripts"))
from q2_pyright import LanguageServer, to_uri  # noqa: E402

ROOT = os.path.abspath("data/code/wt_method_strict")
PYRIGHT = os.path.abspath("data/code/pyenv/bin/pyright-langserver")


def knows(type_text):
    words = set(type_text.replace("|", " ").replace(",", " ").replace(":", " ").split())
    return "ConfigEntries" in words


def main():
    with open("method_resolution.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    project = jedi.Project(ROOT)
    server = LanguageServer([PYRIGHT, "--stdio"])
    server.request("initialize", {"processId": os.getpid(), "rootUri": to_uri(ROOT),
                                  "workspaceFolders": [{"uri": to_uri(ROOT), "name": "ha"}],
                                  "capabilities": {}}, timeout=600)
    server.notify("initialized", {})
    out, opened = [], set()
    for r in rows:
        path = os.path.join(ROOT, r["file"])
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
        line, rcol = int(r["line"]), int(r["col"]) - 2   # last character of the receiver
        try:
            inferred = jedi.Script(code=code, path=path, project=project).infer(line, rcol)
            j_type = ", ".join(sorted({n.name for n in inferred})) or "(unknown)"
        except Exception as exc:
            j_type = f"(error {type(exc).__name__})"
        uri = to_uri(path)
        if uri not in opened:
            server.notify("textDocument/didOpen", {"textDocument": {
                "uri": uri, "languageId": "python", "version": 1, "text": code}})
            opened.add(uri)
        hover = server.request("textDocument/hover", {"textDocument": {"uri": uri},
                                                      "position": {"line": line - 1, "character": rcol}})
        contents = (hover or {}).get("contents", {})
        text = contents.get("value", "") if isinstance(contents, dict) else str(contents)
        p_type = text.replace("```python", "").replace("```", "").strip().split("\n")[0] or "(nothing)"
        out.append([r["file"], r["line"], r["kind"], r["receiver"], r["jedi_result"], j_type, r["pyright_result"], p_type])
    server.request("shutdown", None, timeout=10)
    server.notify("exit", None)

    with open("method_receivers.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["file", "line", "kind", "receiver", "jedi_result", "jedi_thinks_receiver_is", "pyright_result", "pyright_hover"])
        w.writerows(out)
    for tool, res, typ in (("Jedi", 4, 5), ("pyright", 6, 7)):
        t = Counter((o[res], knows(o[typ])) for o in out)
        print(f"{tool}: knew the receiver -> right {t[('right', True)]}, no answer {t[('no_answer', True)]}, wrong {t[('wrong', True)]};"
              f"  didn't know -> right {t[('right', False)]}, no answer {t[('no_answer', False)]}, wrong {t[('wrong', False)]}")
    print("misses:")
    for o in out:
        if o[4] != "right" or o[6] != "right":
            print(f"  {o[0]}:{o[1]}  jedi {o[4]} thinks {o[5][:40]!r}  pyright {o[6]} hover {o[7][:70]!r}")
    print("saved method_receivers.csv")


if __name__ == "__main__":
    main()
