"""D5 score: is ast_resolved right on the packages, and does the resolver find every real url() use?

    python -m tests.score_packages --run-id 973531f7fd4cfd5e --config meridian.toml     # from DATA PIPELINE/

Reads out/<run_id>/candidate.jsonl and resolution.jsonl, and the package repos in snapshots/<run_id>/.

The answer key is each package's own url() migration commit: meridian.toml pins its parent (`<commit>^1`), and a
line that commit changed is a real use. Some migrations weren't finished (graphene-django's never was, and others
left tests and examples behind), so a line the commit didn't touch is a real use too when it is the import, or a
url(...) call, in a file that imports url from django.conf.urls. That rule is plain text, so it doesn't lean on Jedi.

    precision  resolved rows that are real uses / resolved rows                  (bar: >= 0.98, PIPELINE-PLAN.md)
    recall     real uses that are resolved / real uses (import and call rows only)
"""
import json, re
from collections import defaultdict
from pathlib import Path

from meridian.common import data_root, git, handoff, load_config, log, parser, read_jsonl

DJANGO_URL = re.compile(r"^\s*from\s+django\.conf\.urls\s+import\s+[^#\n]*\burl\b", re.M)


def changed_lines(repo_dir, parent, child):
    """{path: {line}}: lines on the parent's side that the child commit changed."""
    out = git(repo_dir, "diff", "-U0", "--no-color", parent, child, "--", "*.py")  # deaf to diff.noprefix and friends
    changed, path = defaultdict(set), None
    for line in out.split("\n"):
        if line.startswith("--- "):
            path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
            start, count = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            changed[path].update(range(start, start + count))
    return changed


def score(run_id, cfg, candidates, rows, root=None):
    """Per package and in total: candidates, resolved, real among resolved, real uses, real uses resolved."""
    snap = Path(root or data_root()) / "snapshots" / run_id
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    refs = {r["repo"]: r["ref"] for r in cfg["datasets"]["django_packages"]["repos"]}
    cands = {(c["repo"], c["path"], c["line"], c["col"]): c for c in candidates}
    table = {}
    for repo in sorted(r for r in manifest["repos"] if r in refs):
        repo_dir = snap / "repos" / repo.replace("/", "__")
        changed = changed_lines(repo_dir, manifest["repos"][repo], refs[repo].removesuffix("^1"))
        imports_url = {}
        t = dict(candidates=0, resolved=0, resolved_real=0, real=0, real_resolved=0, resolved_off_commit=0)
        for r in rows:
            key = (r["repo"], r["path"], r["line"], r["col"])
            if r["repo"] != repo or key not in cands:
                continue                                    # alias rows and other repos
            c = cands[key]
            if r["path"] not in imports_url:
                imports_url[r["path"]] = bool(DJANGO_URL.search((repo_dir / r["path"]).read_text(encoding="utf-8", errors="replace")))
            on_commit = r["line"] in changed.get(r["path"], ())
            by_text = imports_url[r["path"]] and c["role"] in ("import", "call")
            real_use = c["role"] in ("import", "call") and (on_commit or by_text)
            resolved = r["detection"] == "ast_resolved"
            t["candidates"] += 1
            t["resolved"] += resolved
            t["resolved_real"] += resolved and (on_commit or by_text)
            t["resolved_off_commit"] += resolved and not on_commit
            t["real"] += real_use
            t["real_resolved"] += real_use and resolved
        table[repo] = t
    total = {k: sum(t[k] for t in table.values()) for k in next(iter(table.values()))}
    return table, total


def show(table, total):
    log(f"{'package':<42} {'cands':>6} {'resolved':>9} {'right':>6} {'not on commit':>14} {'real uses':>10} {'found':>6}")
    for repo, t in [*table.items(), ("ALL PACKAGES", total)]:
        log(f"{repo:<42} {t['candidates']:>6} {t['resolved']:>9} {t['resolved_real']:>6} {t['resolved_off_commit']:>14} "
            f"{t['real']:>10} {t['real_resolved']:>6}")
    log(f"precision {total['resolved_real']} of {total['resolved']} = {total['resolved_real'] / total['resolved']:.3f}"
        f"  (on the commit alone: {total['resolved'] - total['resolved_off_commit']} of {total['resolved']})")
    log(f"recall    {total['real_resolved']} of {total['real']} = {total['real_resolved'] / total['real']:.3f}")


def main(argv=None):
    a = parser(__doc__).parse_args(argv)
    cfg = load_config(a.config)
    out = handoff(cfg, a.run_id)
    show(*score(a.run_id, cfg, read_jsonl(out / "candidate.jsonl"), read_jsonl(out / "resolution.jsonl")))


if __name__ == "__main__":
    main()
