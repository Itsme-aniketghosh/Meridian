"""A terminal walk through the pipeline on one run: each part, what it does, the rows it wrote, and one of them.

    python -m tests.demo --run-id 70dd1dabfed544b8 --config meridian.toml      # from Data-Pipeline/

A part whose tables aren't in out/<run_id>/ yet is run first, with its own command. Sample rows come only from
tables without people in them; the people tables show counts.
"""
import json
import subprocess
import sys
import time

from meridian.common import CONFIG, data_root, handoff, load_config, parser, read_jsonl

PARTS = [  # (part, what it does, its tables, the table to show a row of)
    ("collect", "freezes the inputs: repos at pinned SHAs, raw Jira and GitLab pulls, one run_id", ["run"], "run"),
    ("history", "commits, files they touched, tickets as of snapshot_at, commit -> ticket keys, SZZ bug fixes",
     ["git_commit", "commit_file", "ticket", "ticket_event", "ticket_link", "commit_ticket", "bug_inducing", "file_bug_fixes"],
     "file_bug_fixes"),
    ("scan", "tree-sitter finds every place a tracked old name appears, with its role and scope",
     ["candidate", "parse_failure", "module_import", "deprecation"], "candidate"),
    ("resolve", "Jedi decides what each name really is; who calls whom, up to 3 levels", ["resolution", "call_edge"], "call_edge"),
    ("owners", "merges identities, maps people to teams, gives each file an owner team",
     ["person", "person_alias", "team_map", "file_owner"], "file_owner"),
    ("join", "call sites with owner, bug fixes, status and stable IDs; tickets mapped to code; what we missed",
     ["call_site", "ticket_map", "coverage"], "call_site"),
    ("graph", "which site waits on which, how far each symbol reaches, ticket-to-ticket chains",
     ["blocked_by", "blast_radius", "chain"], "blocked_by"),
]
BOLD, DIM, OFF = ("\033[1m", "\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "", "")


def lines(path):
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def first(path):
    with open(path, encoding="utf-8") as f:
        return json.loads(f.readline() or "null")


def main(run_id, config=CONFIG):
    out = handoff(load_config(config), run_id, data_root())
    print(f"{BOLD}Meridian pipeline · run {run_id}{OFF}  {DIM}{out}{OFF}\n")
    for n, (part, what, tables, show) in enumerate(PARTS, 1):
        print(f"{BOLD}{n}. {part}{OFF}  {what}")
        if missing := [t for t in tables if not (out / f"{t}.jsonl").exists()]:
            print(f"   {DIM}no {', '.join(missing)} yet: running python -m meridian.{part}{OFF}")
            start = time.monotonic()
            subprocess.run([sys.executable, "-m", f"meridian.{part}", "--run-id", run_id, "--config", str(config)], check=True)
            print(f"   {DIM}done in {time.monotonic() - start:.0f} s{OFF}")
        for t in tables:
            print(f"   {t:16} {lines(out / f'{t}.jsonl'):>10,} rows")
        row = first(out / f"{show}.jsonl")
        if row:
            print(f"   {DIM}e.g. {show}: " + ", ".join(f"{k}={str(v)[:60]}" for k, v in row.items() if k != "run_id") + OFF)
        print()
    print(f"{BOLD}What we missed{OFF}  coverage, from join")
    for c in read_jsonl(out / "coverage.jsonl"):
        print(f"   {c['metric']:28} {c['numerator']:>9,} / {c['denominator']:<9,} {DIM}{c['note']}{OFF}")
    dash = out / "dashboard.html"
    print(f"\n{BOLD}Dashboard{OFF}  " + (str(dash) if dash.exists() else
          f"python -m tests.dashboard --run-id {run_id} --config {config}"))


if __name__ == "__main__":
    a = parser(__doc__).parse_args()
    main(a.run_id, a.config)
