"""Meridian's data pipeline as two Airflow DAGs, built from pipeline_spec.py; every setting comes from settings.toml
([orchestration] for both, [dags.update] and [dags.rebuild] for each), nothing is hard-coded.

meridian_update: the "Update graph" click (a trigger, from the UI or the REST API) and daily.
    anything_new -> collect -> history | scan -> resolve -> owners -> join -> graph -> validate -> report -> publish
    anything_new (meridian.status) skips the rest when nothing changed since the last published run, so repeat clicks
    cost nothing. No bias here: it runs weekly.
meridian_rebuild: weekly. Recomputes the last published run from scratch under its own data root, with bias, then
    compares every table with the update's (meridian.compare): same -> done; different -> alerted, and the rebuild is
    published.
    fresh -> collect -> ... -> validate -> bias -> compare -> publish, and gx beside bias: the Great Expectations audit
    with Data Docs, in its own environment, never gating (meridian.gx)

Each task runs one command in the pipeline's own environment ([orchestration] python), from Data-Pipeline/. Parts are
idempotent ("same run, same output"), so a retry just reruns the part. A task that fails after its retries sends an
alert ([alerts], through meridian.alert). One run at a time per DAG (max_active_runs = 1): a click during a run queues
one more, and its anything_new drops it if that run already took every push.

A trigger whose config names a run_id, or has "force": true, skips anything_new: that run is made even if nothing is
new (a rerun after a code fix). For a run whose snapshot exists, collect replays its SHAs and snapshot_at.

The run: the trigger's config {"run_id": "...", "prev_run": "..."}, else whatever run collect makes from the inputs
(update) or the last published run (rebuild). prev_run: the config, else [pipeline] prev_run or the last published run
(update; a forced rerun of the published run uses its own prev_run), or the published run's own prev_run (rebuild, to
give the same bytes). Never the run itself.
"""
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import ShortCircuitOperator

HERE = Path(__file__).resolve().parents[1]          # Data-Pipeline/
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from meridian import alert, publish                 # noqa: E402  (needs the path above)
from meridian.common import data_root, load_config, load_settings, log   # noqa: E402
from pipeline_spec import DAGS, GATES, command       # noqa: E402

PIPELINE, ORCH, SPECS = load_settings("pipeline"), load_settings("orchestration"), load_settings("dags")
GX_PYTHON = load_settings("gx").get("python") or ORCH["python"]   # the GX audit's own environment
MAIN = data_root()
REBUILD = MAIN / SPECS["rebuild"]["data_root"]
HANDOFF = load_config(HERE / PIPELINE["config"])["run"]["handoff_dir"]

# Jinja expressions, rendered when a task runs: published(key) reads the live latest.json. The update's collect makes
# whatever run the inputs give ('new': repos may follow a branch), and its last line, run_id=<id>, is the run after it.
# The rebuild's fresh reads latest.json once and its last line, run_id=<id> prev_run=<id>, is the run after it: an
# update publishing mid-rebuild can't switch the run under it
COLLECT = "dag_run.conf.get('run_id') or 'new'"
FRESH = ("dag_run.conf.get('run_id') or published('run_id') or params.run_id",
         "dag_run.conf.get('prev_run') or published('prev_run') or params.prev_run")
RUN = {"update": "dag_run.conf.get('run_id') or (ti.xcom_pull(task_ids='collect') or '').split('=')[-1]",
       "rebuild": "(ti.xcom_pull(task_ids='fresh') or '').split(' ')[0].split('=')[-1]"}
# A trigger that names a run ({"run_id": ...}) or says {"force": true} wants that run made: anything_new doesn't get a
# say (a rerun after a code fix, or of an older run: collect replays an existing snapshot's SHAs and snapshot_at)
FORCE = "{{ dag_run.conf.get('run_id') or ('force' if dag_run.conf.get('force') else '') }}"
# A rerun of the published run (forced) keeps its prev_run: matched against itself it would lose its history
PREV = {"update": "dag_run.conf.get('prev_run') or params.prev_run or "
                  "(published('prev_run') if published('run_id') == r else published('run_id'))",
        "rebuild": "(ti.xcom_pull(task_ids='fresh') or '').split(' ')[-1].split('=')[-1]"}
FIRST = {("update", "collect"): (COLLECT, "''"), ("rebuild", "fresh"): FRESH}   # the tasks that name the run


def on_failure(context):
    """Alert once a task has failed for good (after its retries)."""
    ti = context["task_instance"]
    alert.send(f"meridian: {ti.dag_id}.{ti.task_id} failed (try {ti.try_number})",
               [str(context.get("exception") or ""), f"log: {ti.log_url}"], "failure")


def gate(cmd, go, stop, force=""):
    """Run a gate's command: go on at exit code go, skip the rest of the DAG at stop, fail at anything else.
    force (rendered from the trigger's config): go on without asking, for a run someone asked for by name."""
    if force:
        log(f"forced by the trigger's config ({force}): not asking {cmd}")
        return True
    p = subprocess.run(cmd, shell=True, cwd=HERE, capture_output=True, text=True)
    log(f"{p.stdout}\n{p.stderr}")
    if p.returncode not in (go, stop):
        raise RuntimeError(f"exit {p.returncode}: {cmd}")
    return p.returncode == go


def templates(run, prev):
    """(run, '--prev-run <p>' or '', p or '') as templates; never the run itself as its prev."""
    sets = f"{{% set r = {run} %}}{{% set p = {prev} %}}"
    return (f"{{{{ {run} }}}}", sets + "{{ '--prev-run ' ~ p if p and p != r else '' }}",
            sets + "{{ p if p and p != r else '' }}")


def build(name, description):
    spec = SPECS[name]
    with DAG(
        dag_id=spec["dag_id"],
        description=description,
        schedule=spec.get("schedule") or None,
        start_date=datetime(2026, 10, 1),            # the team snapshot's date; the run's own time is run.snapshot_at
        catchup=False,
        max_active_runs=1,                           # two runs would write the same out/<run_id>/ files
        max_active_tasks=ORCH.get("max_active_tasks", 4),
        params={"run_id": PIPELINE["run_id"], "prev_run": PIPELINE.get("prev_run", "")},
        user_defined_macros={"published": lambda key: publish.latest(MAIN).get(key) or ""},
        default_args={
            "retries": ORCH.get("retries", 2),
            "retry_delay": timedelta(minutes=ORCH.get("retry_delay_minutes", 5)),
            "on_failure_callback": on_failure,
        },
        tags=["meridian"],
    ) as dag:
        tasks = {}
        for task, upstream in DAGS[name].items():
            run, prev_arg, prev = templates(*FIRST.get((name, task), (RUN[name], PREV[name])))
            cmd = command(name, task, run, PIPELINE["config"], ORCH["python"], prev_arg, prev, MAIN.as_posix(),
                          REBUILD.as_posix(), HANDOFF, GX_PYTHON)
            timeout = timedelta(minutes=ORCH.get("timeout_minutes", {}).get(task, 60))
            # validate: 0, its errors are data
            retries = ORCH.get("task_retries", {}).get(task, ORCH.get("retries", 2))
            if task in GATES:
                force = FORCE if task == "anything_new" else ""   # compare is never skipped: it is the weekly check
                tasks[task] = ShortCircuitOperator(task_id=task, python_callable=gate, execution_timeout=timeout,
                                                   retries=retries,
                                                   op_kwargs={"cmd": cmd, "go": GATES[task][0], "stop": GATES[task][1],
                                                              "force": force})
            else:
                tasks[task] = BashOperator(task_id=task, bash_command=cmd, cwd=str(HERE), execution_timeout=timeout,
                                           retries=retries,
                                           env={"PYTHONUNBUFFERED": "1"},
                                           append_env=True)   # keep MERIDIAN_DATA, the alert secrets, PATH
            for up in upstream:
                tasks[up] >> tasks[task]
    return dag


update = build("update", "Meridian: what changed since the last published run -> a validated block, published")
rebuild = build("rebuild", "Meridian weekly: the last published run from scratch, compared with the updates")
