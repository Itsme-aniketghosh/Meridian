"""The pipeline as data: which part runs after which, and the tables each reads and writes (CONTRACTS.md).
The Airflow DAG (meridian_dag.py) is built from this, and tests/test_dag.py checks it without Airflow installed:
every table a part reads is written by a part upstream of it.
"""

# part: (upstream parts, tables it reads, tables it writes). Order = a valid run order.
STEPS = {
    "collect": ((), (), ("run",)),
    "history": (("collect",), ("run",), ("git_commit", "commit_file", "ticket", "ticket_event", "ticket_link",
                                          "commit_ticket", "bug_inducing", "file_bug_fixes")),
    "scan": (("collect",), ("run",), ("candidate", "parse_failure", "module_import", "deprecation")),
    "resolve": (("scan",), ("candidate",), ("resolution", "call_edge")),
    "owners": (("history",), ("git_commit", "commit_file", "ticket", "commit_ticket"),
               ("person", "person_alias", "team_map", "file_owner")),
    "join": (("history", "resolve", "owners"), ("candidate", "resolution", "parse_failure", "file_owner", "file_bug_fixes",
                                                "ticket", "commit_ticket", "commit_file", "git_commit", "run"),
             ("call_site", "ticket_map", "coverage")),
    "graph": (("join",), ("call_site", "call_edge", "resolution", "ticket_map", "ticket"), ("blocked_by", "blast_radius", "chain")),
    "validate": (("graph",), ("*",), ()),          # every table; writes stats.json, anomalies.json, meridian.sqlite
    "bias": (("validate",), ("run", "git_commit", "commit_file", "commit_ticket", "ticket", "person", "person_alias",
                             "ticket_map", "candidate", "resolution"), ()),   # writes bias.json
    "version": (("bias",), ("*",), ()),            # DVC: records every output in dvc.lock, pushes if [dvc] push
}

PARTS = [p for p in STEPS if p != "version"]   # each one is `python -m meridian.<part>`


def command(part, run_id, config, python, prev_run_arg="", dvc_push=False):
    """The shell command for one task. Every part is the same one command (CONTRACTS.md). prev_run_arg is
    '--prev-run <run_id>' or '' (in the DAG, a template that renders to one of them)."""
    if part == "version":
        return f"{python} -m dvc commit --force --quiet" + (f" && {python} -m dvc push" if dvc_push else "")
    extra = f" {prev_run_arg}" if prev_run_arg and part in ("join", "validate") else ""
    return f"{python} -m meridian.{part} --run-id {run_id} --config {config}{extra}"
