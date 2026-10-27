# Appendix A: Airflow run and Gantt

We ran the daily DAG (Airflow's graph of tasks), `meridian_update`, in Docker Desktop on an Apple-silicon Mac with `{"run_id": "70dd1dabfed544b8", "force": true}`. Every task passed on its first try, and the run was published after 1,268 s.

![](figures/airflow_gantt.png)

*Each bar is one try of one task, and history (orange) runs alongside scan and resolve (green).*

| Task | Time | What it did |
|---|---:|---|
| anything_new | 0 s | Forced by the trigger |
| collect | 1 s | Snapshot already there, checked and replayed, `run_id=70dd1dabfed544b8` |
| scan | 12 s | Call sites in Django and Spark |
| history | 1,152 s | Commits, tickets and SZZ (tracing each bug fix back to the commit that caused it), through the folder mount |
| resolve | 325 s | Jedi, in parallel with history |
| owners, join, graph | 14 s, 37 s, 2 s | |
| validate | 54 s | 0 errors, 1 known warning (the 289 Jira dates in year 0010) |
| report, publish | 1 s, 1 s | `published/70dd1dabfed544b8.sqlite`, `latest.json` |

Back to back the tasks take 1,599 s, so running scan and resolve beside history saved 331 s (21%). history is the longest branch, so it caps the saving.

![](figures/airflow_ui_graph_update.jpg)

![](figures/airflow_ui_gantt_update.jpg)

*The same run in the Airflow UI, graph view and then Gantt view.*

## Why it takes 1,268 s

Natively the same parts take about 7 minutes. In Docker every git and file read goes through the folder mount, several times slower on macOS and Windows, and history reads the most (`git log --numstat`, blame for SZZ). A Linux host or a Docker volume (`-f docker-compose.volumes.yaml`) removes that cost. A warm SZZ cache also brings history from 312 s to 74 s natively ([Appendix D](appendix-testing.md)).

An earlier run on a Windows laptop never finished:

| What we saw | Cause | Fix |
|---|---|---|
| history failed after 1 s on the first try | Never found. On the Mac the same failure (3 tries each) was an absolute host path in `.git/objects/info/alternates`. On Windows that link was already relative (`../../../../../_cache/repos/<repo>.git/objects`), and the retry got past it | The link is now relative (`collect.borrow_relative`), and collect repairs it in older snapshots. Test: `test_checkouts_work_wherever_the_data_root_is_mounted` |
| Later tries killed at the 60-minute timeout | The Windows folder mount, slower still than the Mac's | `docker-compose.volumes.yaml`, or a longer timeout. history's is now 180 minutes (`settings.toml`), because the weekly rebuild runs it cold |

The error handling still worked: timeouts, retries 5 minutes apart, a log per try, and the last good block stayed live.

## The weekly rebuild

`meridian_rebuild` (run `scheduled__2026-10-18T04:00:00+00:00`) rebuilds 70dd from scratch in its own data folder with every cache off, then compares the result with the update's outputs. Every task passed:

| Task | Time | Note |
|---|---:|---|
| fresh, collect, scan | 0 s, 1 s, 13 s | collect replays 70dd's SHAs and snapshot time |
| resolve | 502 s | Cold, no resolver cache |
| history | 706 s on try 3 | Try 1 hit the old 60-minute timeout (cold SZZ through the mount, other git work on the disk). Try 3 ran on a quiet machine with the 180-minute timeout |
| owners, join, graph, validate, report | 8, 26, 1, 36, 1 s | |
| bias ∥ gx | 12 s, 17 s | Side by side, after validate. GX results are in [Appendix B §4](appendix-quality.md#4-the-weekly-gx-audit) |
| compare | 1 s | 0 differences between the rebuild and the update |
| publish | skipped | Nothing to correct, so nothing republished, as designed |

On the real data, the from-scratch rebuild matches the incremental update byte for byte.

![](figures/airflow_ui_graph_rebuild.jpg)

*The rebuild run, all green except publish, which is pink because compare found nothing to correct.*

## Rerun it

```
docker compose up -d --build                                    # from Data-Pipeline/, with .env from .env.example
# UI at http://localhost:8080: trigger meridian_update with {"run_id": "70dd1dabfed544b8", "force": true}
python report/gantt.py                                          # redraws the chart from report/airflow_run.csv
```

`report/airflow_run.csv` holds the Mac run's per-task tries (`mac_forced_70dd`), exported from the Airflow API.
