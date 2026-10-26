# Appendix A: Airflow run

We ran `meridian_update` in Docker on a Windows laptop: `docker compose up -d --build`, then a trigger with `{"run_id": "70dd1dabfed544b8", "force": true}`. It did not finish. The two heavy tasks hit their 60-minute timeout on every try, because every git and file read goes through Docker's folder mount on Windows. The README already warns that this mount is several times slower. The full run, with a clean Gantt, is to be redone on a Mac or Linux host: see [HANDOVER.md](HANDOVER.md).

This run still shows the parts of Airflow the guidelines grade: task order, parallel branches, timeouts, retries and logs.

![](figures/airflow_gantt.png)

*Each bar is one try of one task. Green is the scan branch, orange the history branch; hatched bars failed, timed out or were stopped. History and resolve ran side by side as designed, but each try was killed at 3,600 s.*

| Task | Tries | What happened |
|---|---|---|
| anything_new | 1 | Forced by the trigger, 1 s |
| collect | 1 | The snapshot already existed: checked and replayed in 9 s, `run_id=70dd1dabfed544b8` |
| scan | 1 | 128 s (140 s on the team's machine) |
| history | 4 | Try 1: `git rev-list` failed after 1 s (not investigated; the retry got past it). Tries 2 and 3: killed at the 60-minute timeout while `git log --numstat` walked Spark's history through the mount. Try 4: stopped by us |
| resolve | 3 | Tries 1 and 2: resolved Django (294 candidates, 542 alias calls, all resolved) and then timed out finding callers. Try 3: stopped by us |
| owners, join, graph, validate, report, publish | 0 | Never started: their upstream never succeeded, so nothing was published and the last good block stayed live |

On the team's machine the same tasks take 430 s (history) and 295 s (resolve). Here the container had 4.7 GB of memory free, so the bottleneck was file I/O, not memory.

What this run shows works:
- **Parallel branches.** history started 3 s after scan, and resolve right after scan, as the DAG says (`history ∥ scan → resolve`).
- **Timeouts and retries.** Each task was killed at its `execution_timeout` and retried twice, 5 minutes apart (`settings.toml [orchestration]`).
- **Logs.** Each try has its own log under `logs/dag_id=meridian_update/…/attempt=N.log`, with the pipeline's own counts in it.
- **Fail closed.** Nothing downstream ran and publish never ran.

What it does not show: a successful end-to-end Gantt, and the timing comparison (about 700 s in the DAG vs 1,130 s run one after another, from the team's per-task timings). Utkarsh reruns it on a Mac. Two ways to make it work on Windows: add `-f docker-compose.volumes.yaml` so the data sits on a Docker volume, or raise the history and resolve timeouts.

The per-try data is in `report/airflow_run.csv`. `python report/gantt.py` redraws the chart.
