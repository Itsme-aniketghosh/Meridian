"""Meridian's data pipeline as two Airflow DAGs, built from pipeline_spec.py; every setting comes from settings.toml
([orchestration] for both, [dags.update] and [dags.rebuild] for each), nothing is hard-coded.

meridian_update: the "Update graph" click (a trigger, from the UI or the REST API) and daily.
    anything_new -> collect -> history | scan -> resolve -> owners -> join -> graph -> validate -> report -> publish
    anything_new (meridian.status) skips the rest when nothing changed since the last published run, so repeat clicks
    cost nothing. No bias here: it runs weekly.
meridian_rebuild: weekly. Recomputes the last published run from scratch under its own data root, with bias, then
    compares every table with the update's (meridian.compare): same -> done; different -> alerted, and the rebuild is
    published.
    fresh -> collect -> ... -> validate -> bias -> compare -> publish

Each task runs one command in the pipeline's own environment ([orchestration] python), from Data-Pipeline/. Parts are
idempotent ("same run, same output"), so a retry just reruns the part. A task that fails after its retries sends an
alert ([alerts], through meridian.alert). One run at a time per DAG (max_active_runs = 1): a click during a run queues
one more, and its anything_new drops it if that run already took every push.

The run: the trigger's config {"run_id": "...", "prev_run": "..."}, else [pipeline] run_id (update) or the last
published run (rebuild). prev_run: the config, else [pipeline] prev_run or the last published run (update), or the
published run's own prev_run (rebuild, to give the same bytes). Never the run itself.
"""
import subprocess, sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import ShortCircuitOperator

HERE = Path(__file__).resolve().parents[1]          # Data-Pipeline/
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from meridian import alert, publish                 # noqa: E402  (needs the path above)
from meridian.common import data_root, load_config, load_settings   # noqa: E402
from pipeline_spec import DAGS, GATES, command       # noqa: E402

PIPELINE, ORCH, SPECS = load_settings("pipeline"), load_settings("orchestration"), load_settings("dags")
MAIN = data_root()
REBUILD = MAIN / SPECS["rebuild"]["data_root"]
HANDOFF = load_config(HERE / PIPELINE["config"])["run"]["handoff_dir"]

# Jinja expressions, rendered when a task runs: published(key) reads the live latest.json
RUN = {"update": "dag_run.conf.get('run_id') or params.run_id",
       "rebuild": "dag_run.conf.get('run_id') or published('run_id') or params.run_id"}
PREV = {"update": "dag_run.conf.get('prev_run') or params.prev_run or published('run_id')",
        "rebuild": "dag_run.conf.get('prev_run') or published('prev_run') or params.prev_run"}


def on_failure(context):
    """Alert once a task has failed for good (after its retries)."""
    ti = context["task_instance"]
    alert.send(f"meridian: {ti.dag_id}.{ti.task_id} failed (try {ti.try_number})",
               [str(context.get("exception") or ""), f"log: {ti.log_url}"], "failure")


def gate(cmd, go, stop):
    """Run a gate's command: go on at exit code go, skip the rest of the DAG at stop, fail at anything else."""
    p = subprocess.run(cmd, shell=True, cwd=HERE, capture_output=True, text=True)
    print(p.stdout, p.stderr, sep="\n", flush=True)
    if p.returncode not in (go, stop):
        raise RuntimeError(f"exit {p.returncode}: {cmd}")
    return p.returncode == go


def build(name, description):
    spec, sets = SPECS[name], f"{{% set r = {RUN[name]} %}}{{% set p = {PREV[name]} %}}"
    run, prev = f"{{{{ {RUN[name]} }}}}", sets + "{{ p if p and p != r else '' }}"
    prev_arg = sets + "{{ '--prev-run ' ~ p if p and p != r else '' }}"
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
            cmd = command(name, task, run, PIPELINE["config"], ORCH["python"], prev_arg, prev, MAIN.as_posix(),
                          REBUILD.as_posix(), HANDOFF)
            timeout = timedelta(minutes=ORCH.get("timeout_minutes", {}).get(task, 60))
            if task in GATES:
                tasks[task] = ShortCircuitOperator(task_id=task, python_callable=gate, execution_timeout=timeout,
                                                   op_kwargs={"cmd": cmd, "go": GATES[task][0], "stop": GATES[task][1]})
            else:
                tasks[task] = BashOperator(task_id=task, bash_command=cmd, cwd=str(HERE), execution_timeout=timeout,
                                           env={"PYTHONUNBUFFERED": "1"},
                                           append_env=True)   # keep MERIDIAN_DATA, the alert secrets, PATH
            for up in upstream:
                tasks[up] >> tasks[task]
    return dag


update = build("update", "Meridian: what changed since the last published run -> a validated block, published")
rebuild = build("rebuild", "Meridian weekly: the last published run from scratch, compared with the updates")
