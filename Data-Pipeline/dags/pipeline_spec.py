"""The pipeline as data: which part runs after which, the tables each reads and writes (CONTRACTS.md), and the two DAGs
made of them. The Airflow DAGs (meridian_dag.py) are built from this, and tests/test_dag.py checks it without Airflow
installed: every table a part reads is written by a part upstream of it, and publish only ever follows validate.
"""
import os
import re

# part: (upstream parts, tables it reads, tables it writes). Order = a valid run order.
STEPS = {
    "collect": ((), (), ("run",)),
    "history": (("collect",), ("run",), ("git_commit", "commit_file", "ticket", "ticket_event", "ticket_link",
                                         "commit_ticket", "bug_inducing", "file_bug_fixes")),
    "scan": (("collect",), ("run",), ("candidate", "parse_failure", "module_import", "deprecation")),
    "resolve": (("scan",), ("candidate",), ("resolution", "call_edge")),
    "owners": (("history",), ("run", "git_commit", "commit_file", "ticket", "commit_ticket"),
               ("person", "person_alias", "team_map", "file_owner")),
    "join": (("history", "resolve", "owners"),
             ("candidate", "resolution", "parse_failure", "file_owner", "file_bug_fixes",
              "ticket", "commit_ticket", "commit_file", "git_commit", "run"),
             ("call_site", "ticket_map", "coverage")),
    "graph": (("join",), ("call_site", "call_edge", "resolution", "ticket_map", "ticket"),
              ("blocked_by", "blast_radius", "chain")),
    "validate": (("graph",), ("*",), ()),          # every table; writes stats.json, anomalies.json, meridian.sqlite
    "bias": (("validate",), ("run", "git_commit", "commit_file", "commit_ticket", "ticket", "person", "person_alias",
                             "ticket_map", "candidate", "resolution"), ()),   # writes bias.json
}

PARTS = list(STEPS)   # each one is `python -m meridian.<part>`
CORE = {p: STEPS[p][0] for p in ("history", "scan", "resolve", "owners", "join", "graph", "validate")}

# Each DAG: task -> upstream tasks. Order = a valid run order. Besides the parts: anything_new (meridian.status),
# report (meridian.report: what changed, and which commit), publish, compare (meridian.<task>), and fresh (shell).
# gx (meridian.gx, the weekly Great Expectations audit) runs beside bias and nothing waits for it: it never gates.
DAGS = {
    "update": {"anything_new": (), "collect": ("anything_new",), **CORE, "report": ("validate",),
               "publish": ("report",)},
    "rebuild": {"fresh": (), "collect": ("fresh",), **CORE, "bias": ("validate",), "gx": ("validate",),
                "report": ("validate",), "compare": ("report",),
                "publish": ("compare",)},
}

# Gates stop the rest of their DAG by their command's exit code: task -> (go on, stop). Any other code fails the task.
GATES = {"anything_new": (0, 99),   # meridian.status: 0 = something new since the last published run, 99 = nothing
         "compare": (3, 0)}         # meridian.compare: 3 = the rebuild differs (alerted): publish it; 0 = same: done
ALERTED = 4   # a part's exit code when it sent its own alert, then failed: the DAG's on_failure doesn't send another


def checked_run(v):
    """v if it's a run_id (16 hex) or empty, else ValueError: the trigger's config goes into shell commands, rm -rf's
    among them."""
    if v and not re.fullmatch(r"[0-9a-f]{16}", str(v)):
        raise ValueError(f"not a run_id: {v!r}")
    return v or ""


def alerted(exception):
    """True if the task's exception says exit code ALERTED: BashOperator's "...a non-zero exit code 4.", gate()'s
    "exit code 4: <cmd>"."""
    return bool(re.search(rf"\bexit code {ALERTED}\b", str(exception or "")))


def command(dag, task, run_id, config, python, prev_arg="", prev="", main="", rebuild="", handoff="out/{run_id}",
            gx_python=""):
    """The shell command for one task of one DAG. Every part is the same one command (CONTRACTS.md). prev_arg is
    '--prev-run <run_id>' or '', prev the bare run_id or '' (in the DAG, templates that render to them). main and
    rebuild are the two data roots. The rebuild's tasks run under its own root (MERIDIAN_DATA), except publish, which
    puts the rebuild where the views read. gx runs in its own environment (gx_python) and puts its Data Docs there
    too."""
    if task == "fresh":   # from scratch: all of its out/ wiped (every past week's too, and the last prev link: rm
        # doesn't follow it; ln -sfn onto a real dir would link inside it); the snapshot and the previous run's out/
        # linked in. Its last line names the run once, for the later tasks (xcom): an update publishing meanwhile
        # can't switch it
        if not rebuild or os.path.realpath(rebuild) in (os.path.realpath("."), os.path.realpath(main)):
            raise ValueError(f"[dags.rebuild] data_root must be a folder of its own, not {rebuild!r} "
                             f"(main is {main!r})")
        top = handoff.split("/")[0]   # out/
        out = f"{rebuild}/{handoff.format(run_id=run_id)}"
        prev_out = handoff.format(run_id="$p")
        return (f'rm -rf {rebuild}/{top} && mkdir -p {out} && ln -sfn {main}/snapshots {rebuild}/snapshots'
                f' && p="{prev}" && if [ -n "$p" ]; then ln -sfn {main}/{prev_out} {rebuild}/{prev_out}; fi'
                f' && echo "run_id={run_id} prev_run=$p"')
    if task == "anything_new":
        return f"{python} -m meridian.status --config {config}"
    cmd = (f"{gx_python if task == 'gx' and gx_python else python} -m meridian.{task} --run-id {run_id} "
           f"--config {config}")
    if prev_arg and task in ("join", "validate", "report", "publish"):
        cmd += f" {prev_arg}"
    if task == "compare":
        cmd += f" --against-root {main}"
    if task == "gx" and main:
        cmd += f" --publish-root {main}"
    if dag == "rebuild":
        cmd = (f"{cmd} --from-root {rebuild}" if task == "publish"
               else f"MERIDIAN_DATA={rebuild} MERIDIAN_NO_CACHE=1 {cmd}")
    return cmd
