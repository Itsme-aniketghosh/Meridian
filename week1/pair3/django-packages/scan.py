"""Find the url() -> path()/re_path() migration in each third-party Django repo.

For every first-parent commit that touches 'django.conf.urls' in a .py file,
count url() usage in the parent tree and in the commit tree, and record the drop.
"""
import json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).parent / "repos"  # clone each repo here as owner_name, e.g. encode_django-rest-framework

# `from django.conf.urls import url`, `..import include, url`, multi-line `( ... url, ...)`
IMPORT_RE = re.compile(r"from\s+django\.conf\.urls\s+import\s+(\([^)]*\)|[^\n#]*)", re.S)
ATTR_RE = re.compile(r"\b(?:django\.conf\.)?urls\.url\(")          # urls.url( / django.conf.urls.url(
CALL_RE = re.compile(r"(?<![\w.])url\(")                          # bare url( call
SHIM_RE = re.compile(r"import\s+[^\n]*\bre_path\s+as\s+url\b")     # from django.urls import re_path as url
PATH_ADD_RE = re.compile(r"^\+.*(?<![\w.])(re_path|path)\(", re.M)
TEST_RE = re.compile(r"(^|/)(tests?|testing|test_\w+\.py|\w+_tests?\.py)(/|$)")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def imports_url(src):
    for m in IMPORT_RE.finditer(src):
        names = re.sub(r"[()\s]", " ", m.group(1)).replace(",", " ").split()
        if "url" in names:
            return True
    return False


def snapshot(repo, sha):
    """Count url() usage at a commit. Returns dict with files/import_lines/call_lines/test_files/shim."""
    out = {"files": 0, "test_files": 0, "import_lines": 0, "call_lines": 0, "shim_files": 0}
    files = git(repo, "grep", "-l", "-E", "conf\\.urls|conf import urls|re_path as url", sha, "--", "*.py").split()
    for f in files:
        path = f.split(":", 1)[1]
        src = git(repo, "show", f)
        if SHIM_RE.search(src):
            out["shim_files"] += 1
        uses_import = imports_url(src)
        attr = len(ATTR_RE.findall(src))
        if not uses_import and not attr:
            continue
        out["files"] += 1
        if TEST_RE.search(path):
            out["test_files"] += 1
        out["import_lines"] += int(uses_import)
        calls = attr + (sum(1 for line in src.splitlines() if CALL_RE.search(line)
                            and not line.lstrip().startswith(("#", "from", "import"))) if uses_import else 0)
        out["call_lines"] += calls
    out["sites"] = out["import_lines"] + out["call_lines"]
    return out


def scan(repo):
    head = git(repo, "rev-parse", "HEAD").strip()
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    shas = git(repo, "log", "--first-parent", "--format=%H", "-G", "django.conf.urls", "HEAD", "--", "*.py").split()
    total_commits = int(git(repo, "rev-list", "--count", "--first-parent", "HEAD").strip() or 0)
    rows, peak = [], None
    cache = {}
    def snap(s):
        if s not in cache:
            cache[s] = snapshot(repo, s)
        return cache[s]
    for sha in reversed(shas):
        parent = git(repo, "rev-parse", f"{sha}^1").strip()
        if not parent:
            continue
        before, after = snap(parent), snap(sha)
        drop = before["sites"] - after["sites"]
        if peak is None or before["sites"] > peak["sites"]:
            peak = {"sha": parent[:10], **before}
        if drop <= 0 and after["shim_files"] <= before["shim_files"]:
            continue
        meta = git(repo, "show", "-s", "--format=%h|%ad|%an|%s", "--date=short", sha).strip().split("|", 3)
        stat = git(repo, "show", "--format=", "--numstat", sha, "--", "*.py").strip().splitlines()
        diff = git(repo, "show", "--format=", "-U0", sha, "--", "*.py")
        deleted = git(repo, "show", "--format=", "--diff-filter=D", "--name-only", sha).split()
        rows.append({
            "sha": meta[0], "date": meta[1], "author": meta[2], "subject": meta[3][:100],
            "before": before["sites"], "after": after["sites"], "drop": drop,
            "files_before": before["files"], "files_after": after["files"],
            "py_files_changed": len(stat),
            "adds_path_calls": len(PATH_ADD_RE.findall(diff)),
            "deleted_files": len(deleted),
            "shim_after": after["shim_files"],
        })
    now = snap(head)
    return {"repo": repo.name, "branch": branch, "head": head[:10], "first_parent_commits": total_commits,
            "touching_commits": len(shas), "peak": peak, "head_counts": now, "drops": rows}


if __name__ == "__main__":
    names = sys.argv[1:] or sorted(p.name for p in ROOT.iterdir() if p.is_dir() and p.name != "rjust_defects4j")
    for n in names:
        res = scan(ROOT / n)
        (ROOT.parent / "results").mkdir(exist_ok=True)
        (ROOT.parent / "results" / f"{n}.json").write_text(json.dumps(res, indent=1))
        p = res["peak"] or {}
        print(f"{n:45} peak={p.get('sites')} files={p.get('files')} head={res['head_counts']['sites']} "
              f"drops={len(res['drops'])}", flush=True)
