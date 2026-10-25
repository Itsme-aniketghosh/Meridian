"""Meridian's data pipeline as one Airflow DAG: collect -> history | scan -> resolve -> owners -> join -> graph ->
validate -> bias -> version. Built from pipeline_spec.py; every setting comes from settings.toml, nothing is hard-coded.

Each task runs one part's command (`python -m meridian.<part> --run-id ... --config ...`) in the pipeline's own
environment ([orchestration] python), from Data-Pipeline/. Parts are idempotent ("same run, same output"), so a retry
just reruns the part. A task that fails after its retries sends an alert ([alerts], through meridian.alert).
history runs next to scan -> resolve: the two branches share nothing until join.

The run comes from [pipeline] run_id, or from the trigger's config: {"run_id": "...", "prev_run": "..."}.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator

HERE = Path(__file__).resolve().parents[1]          # Data-Pipeline/
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from meridian import alert                          # noqa: E402  (needs the path above)
from meridian.common import load_settings           # noqa: E402
from pipeline_spec import STEPS, command            # noqa: E402

PIPELINE, ORCH, DVC = load_settings("pipeline"), load_settings("orchestration"), load_settings("dvc")


def on_failure(context):
    """Alert once a task has failed for good (after its retries)."""
    ti = context["task_instance"]
    alert.send(f"meridian: task {ti.task_id} failed (run {context['params'].get('run_id')}, try {ti.try_number})",
               [str(context.get("exception") or ""), f"log: {ti.log_url}"], "failure")


with DAG(
    dag_id=ORCH["dag_id"],
    description="Meridian: code, git and Jira -> call sites, owners, blocks and chains, validated and versioned",
    schedule=ORCH.get("schedule") or None,
    start_date=datetime(2026, 10, 1),                # the team snapshot's date; the run's own time is run.snapshot_at
    catchup=False,
    max_active_runs=1,                               # two runs would write the same out/<run_id>/ files
    max_active_tasks=ORCH.get("max_active_tasks", 4),
    params={"run_id": PIPELINE["run_id"], "prev_run": PIPELINE.get("prev_run", "")},
    default_args={
        "retries": ORCH.get("retries", 2),
        "retry_delay": timedelta(minutes=ORCH.get("retry_delay_minutes", 5)),
        "on_failure_callback": on_failure,
    },
    tags=["meridian"],
) as dag:
    run_id = "{{ dag_run.conf.get('run_id', params.run_id) }}"
    prev_arg = "{% set p = dag_run.conf.get('prev_run', params.prev_run) %}{{ '--prev-run ' ~ p if p else '' }}"
    tasks = {}
    for part, (upstream, _, _) in STEPS.items():
        tasks[part] = BashOperator(
            task_id=part,
            bash_command=command(part, run_id, PIPELINE["config"], ORCH["python"], prev_arg, DVC.get("push", False)),
            cwd=str(HERE),
            env={"PYTHONUNBUFFERED": "1"},
            append_env=True,                         # keep MERIDIAN_DATA, the alert secrets, PATH
            execution_timeout=timedelta(minutes=ORCH.get("timeout_minutes", {}).get(part, 60)),
        )
        for up in upstream:
            tasks[up] >> tasks[part]
