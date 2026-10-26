# Appendix A: Airflow run and Gantt

We ran `meridian_update` in Docker Desktop on an Apple-silicon Mac, triggered with `{"run_id": "70dd1dabfed544b8", "force": true}`. Every task succeeded on its first try and the run was published in 1,268 s.

![](figures/airflow_gantt.png)

*Each bar is one task's try, and history (orange) runs beside scan and resolve (green), as the DAG says.*

| Task | Time | What it did |
|---|---:|---|
| anything_new | 0 s | Forced by the trigger |
| collect | 1 s | The snapshot already existed: checked and replayed, `run_id=70dd1dabfed544b8` |
| scan | 12 s | Call sites in Django and Spark |
| history | 1,152 s | Commits, tickets, SZZ, through the folder mount |
| resolve | 325 s | Jedi, in parallel with history |
| owners, join, graph | 14 s, 37 s, 2 s | |
| validate | 54 s | 0 errors, 1 known warning (the 289 Jira dates in year 0010) |
| report, publish | 1 s, 1 s | `published/70dd1dabfed544b8.sqlite`, `latest.json` |

The tasks add up to 1,599 s one after another. The DAG took 1,268 s, so running scan and resolve under history saved 331 s (21%). history is the longest branch and every other branch finishes inside it, so it caps the saving.

![](figures/airflow_ui_graph_update.jpg)

![](figures/airflow_ui_gantt_update.jpg)

*The same run in the Airflow UI: the graph view, then the Gantt view.*

## Why it takes 1,268 s

The same parts take about 7 minutes natively on the Mac. In Docker every git and file read goes through Docker Desktop's folder mount, several times slower on macOS and Windows, and history reads the most (`git log --numstat`, blame for SZZ). A Linux host or a Docker volume (`-f docker-compose.volumes.yaml`) removes that cost. The SZZ cache also brings a warm history from 312 s to 74 s natively ([Appendix D](appendix-testing.md)).

An earlier run on Aniket's Windows laptop never finished:

| What we saw | Cause | Fix |
|---|---|---|
| history failed after 1 s on the first try | Not found. On the Mac the same 1 s failure came from `.git/objects/info/alternates` holding an absolute host path, which the container can't see (3 tries each). On the Windows laptop that link was already relative (`../../../../../_cache/repos/<repo>.git/objects`), so it wasn't that, and the retry got past it | The link is now relative (`collect.borrow_relative`), and collect repairs an older snapshot's link. Test: `test_checkouts_work_wherever_the_data_root_is_mounted` |
| Later tries killed at the 60-minute timeout | The Windows folder mount, slower still than the Mac's | `docker-compose.volumes.yaml`, or a longer timeout. history's is now 180 minutes (`settings.toml`), because the weekly rebuild runs it cold |

That failed run still showed the error handling at work: timeouts, retries 5 minutes apart, a log per try, and the last good block stayed live.

## The weekly rebuild

`meridian_rebuild` (run `scheduled__2026-10-18T04:00:00+00:00`) rebuilds 70dd from scratch in its own data folder, with every cache off, and compares the result with the update's outputs. Every task succeeded:

| Task | Time | Note |
|---|---:|---|
| fresh, collect, scan | 0 s, 1 s, 13 s | collect replays 70dd's SHAs and snapshot time |
| resolve | 502 s | Cold: no resolver cache |
| history | 706 s on try 3 | Try 1 hit the old 60-minute timeout (cold SZZ through the mount, with heavy git work on the same disk). With the timeout at 180 minutes, try 3 ran on a quiet machine |
| owners, join, graph, validate, report | 8, 26, 1, 36, 1 s | |
| bias ∥ gx | 12 s, 17 s | Side by side, after validate. GX results are in [Appendix B §4](appendix-quality.md#4-the-weekly-gx-audit) |
| compare | 1 s | 0 differences between the rebuild and the update |
| publish | skipped | Nothing to correct, so nothing was republished, as designed |

So on the real data, inside Airflow, the from-scratch run equals the incremental update byte for byte.

![](figures/airflow_ui_graph_rebuild.jpg)

*The rebuild run: every task green, and publish pink because compare found nothing to correct.*

## Rerun it

```
docker compose up -d --build                                    # from Data-Pipeline/, with .env from .env.example
# UI at http://localhost:8080: trigger meridian_update with {"run_id": "70dd1dabfed544b8", "force": true}
python report/gantt.py                                          # redraws the chart from report/airflow_run.csv
```

`gantt.py` reads the per-task tries exported from the Airflow API into `report/airflow_run.csv`. The copy in git still holds the earlier Windows attempt, so export the Mac run's tries over it before redrawing.
