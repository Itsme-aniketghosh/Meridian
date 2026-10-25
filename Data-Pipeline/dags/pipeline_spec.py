"""The pipeline as data: which part runs after which, the tables each reads and writes (CONTRACTS.md), and the two DAGs
made of them. The Airflow DAGs (meridian_dag.py) are built from this, and tests/test_dag.py checks it without Airflow
installed: every table a part reads is written by a part upstream of it, and publish only ever follows validate.
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
}

PARTS = list(STEPS)   # each one is `python -m meridian.<part>`
CORE = {p: STEPS[p][0] for p in ("history", "scan", "resolve", "owners", "join", "graph", "validate")}

# Each DAG: task -> upstream tasks. Order = a valid run order. Besides the parts: anything_new (meridian.status),
# report (meridian.report: what changed, and which commit), publish, compare (meridian.<task>), and fresh (shell).
DAGS = {
    "update": {"anything_new": (), "collect": ("anything_new",), **CORE, "report": ("validate",), "publish": ("report",)},
    "rebuild": {"fresh": (), "collect": ("fresh",), **CORE, "bias": ("validate",), "compare": ("bias",),
                "publish": ("compare",)},
}

# Gates stop the rest of their DAG by their command's exit code: task -> (go on, stop). Any other code fails the task.
GATES = {"anything_new": (0, 99),   # meridian.status: 0 = something new since the last published run, 99 = nothing
         "compare": (1, 0)}         # meridian.compare: 1 = the rebuild differs (alerted): publish it; 0 = same: done


def command(dag, task, run_id, config, python, prev_arg="", prev="", main="", rebuild="", handoff="out/{run_id}"):
    """The shell command for one task of one DAG. Every part is the same one command (CONTRACTS.md). prev_arg is
    '--prev-run <run_id>' or '', prev the bare run_id or '' (in the DAG, templates that render to them). main and
    rebuild are the two data roots. The rebuild's tasks run under its own root (MERIDIAN_DATA), except publish, which
    puts the rebuild where the views read."""
    if task == "fresh":   # from scratch: its out/<run_id> wiped; the snapshot and the previous run's out/ linked in
        out = f"{rebuild}/{handoff.format(run_id=run_id)}"
        prev_out = handoff.format(run_id="$p")
        return (f'rm -rf {out} && mkdir -p {out} && ln -sfn {main}/snapshots {rebuild}/snapshots && p="{prev}"'
                f' && if [ -n "$p" ]; then ln -sfn {main}/{prev_out} {rebuild}/{prev_out}; fi')
    if task == "anything_new":
        return f"{python} -m meridian.status --config {config}"
    cmd = f"{python} -m meridian.{task} --run-id {run_id} --config {config}"
    if prev_arg and task in ("join", "validate", "report", "publish"):
        cmd += f" {prev_arg}"
    if task == "compare":
        cmd += f" --against-root {main}"
    if dag == "rebuild":
        cmd = f"{cmd} --from-root {rebuild}" if task == "publish" else f"MERIDIAN_DATA={rebuild} MERIDIAN_NO_CACHE=1 {cmd}"
    return cmd
