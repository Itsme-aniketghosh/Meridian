"""Method-call benchmark, step 3: check that "no answer" is the tools' real limit, not our setup.

For every use in results/method_resolution.csv, each tool is asked what the receiver is (the
thing before the dot): Jedi with infer, pyright with hover. If a tool knew the receiver was a
User, AnonymousUser, or HttpRequest and still gave no answer, the setup would be broken.

Writes:
  results/method_receivers.csv   per use: each tool's result, and what it thinks the receiver is

Usage: python scripts/method_receivers.py [path-to-django]
"""
import csv
import os
import shutil
import sys
from collections import Counter

import jedi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from q2_pyright import LanguageServer, to_uri  # noqa: E402

REPO = os.path.abspath(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/MLOps/data/django"))
CLASSES = ("AbstractBaseUser", "AbstractUser", "User", "AnonymousUser", "HttpRequest", "WSGIRequest")


def knows(type_text):
    """Does the tool's idea of the receiver include a class that has the old method?"""
    words = set(type_text.replace("|", " ").replace(",", " ").replace(":", " ").split())
    return any(c in words for c in CLASSES)


def main():
    with open("results/method_resolution.csv", newline="") as fh:
        rows = list(csv.DictReader(fh))
    exe = shutil.which("pyright-langserver") or os.path.join(os.path.dirname(sys.executable), "pyright-langserver")

    out = []
    for bench in sorted({r["benchmark"] for r in rows}):
        root = f"{REPO}-{bench}"
        project = jedi.Project(root)
        server = LanguageServer([exe, "--stdio"])
        server.request("initialize", {"processId": os.getpid(), "rootUri": to_uri(root),
                                      "workspaceFolders": [{"uri": to_uri(root), "name": "django"}],
                                      "capabilities": {}}, timeout=600)
        server.notify("initialized", {})
        opened = set()
        for r in (r for r in rows if r["benchmark"] == bench):
            path = os.path.join(root, r["file"])
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
            out.append([bench, r["file"], r["line"], r["kind"], r["receiver"],
                        r["jedi_result"], j_type, r["pyright_result"], p_type])
        server.request("shutdown", None, timeout=10)
        server.notify("exit", None)

    with open("results/method_receivers.csv", "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["benchmark", "file", "line", "kind", "receiver",
                         "jedi_result", "jedi_thinks_receiver_is", "pyright_result", "pyright_hover"])
        writer.writerows(out)

    for tool, res, typ in (("Jedi", 5, 6), ("pyright", 7, 8)):
        table = Counter((o[res], knows(o[typ])) for o in out)
        print(f"{tool}: knew the receiver -> right {table[('right', True)]}, no answer {table[('no_answer', True)]}, "
              f"wrong {table[('wrong', True)]};  didn't know -> right {table[('right', False)]}, "
              f"no answer {table[('no_answer', False)]}, wrong {table[('wrong', False)]}")
    print("saved results/method_receivers.csv")


if __name__ == "__main__":
    main()
