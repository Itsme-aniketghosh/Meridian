# Appendix A: Airflow run

We ran `meridian_update` in Docker on a Mac (Apple silicon, Docker Desktop): `docker compose up -d --build`, then a trigger with `{"run_id": "70dd1dabfed544b8", "force": true}`. Every task succeeded on its first try, and the run was published: wall time **1,268 s**.

![](figures/airflow_gantt.png)

*Each bar is one task's try. Orange is the history branch, green the scan branch, blue the parts that run one after another. history runs side by side with scan and resolve, as the DAG says (`history ∥ scan → resolve`).*

| Task | Time | What it did |
|---|---:|---|
| anything_new | 0 s | Forced by the trigger |
| collect | 1 s | The snapshot already existed: checked and replayed, `run_id=70dd1dabfed544b8` |
| scan | 12 s | Call sites in Django and Spark |
| history | 1,152 s | Commits, tickets, SZZ, through the folder mount |
| resolve | 325 s | Jedi, in parallel with history |
| owners | 14 s | |
| join | 37 s | |
| graph | 2 s | |
| validate | 54 s | 0 errors, 1 known warning (the 289 Jira dates in year 0010) |
| report | 1 s | |
| publish | 1 s | `published/70dd1dabfed544b8.sqlite`, `latest.json` |

**Parallel branches: measured, not estimated.** The tasks add up to 1,599 s one after another, and the DAG took 1,268 s: 331 s (21%) saved by running scan and resolve under history. The saving is capped by history, the longest branch: every other branch finishes inside it.

The Airflow UI for this run:

![](figures/airflow_ui_graph_update.jpg)

![](figures/airflow_ui_gantt_update.jpg)

## Why 1,268 s, and why the Windows run failed

The same parts take about 7 minutes run natively on the Mac. Inside Docker, every git and file read goes through Docker Desktop's folder mount of the repo, which is several times slower on macOS and Windows. history reads the most (`git log --numstat`, blame for SZZ), so it is the bar that grows. This is file I/O, not the pipeline's design: on a Linux host, or with the data on a Docker volume (`-f docker-compose.volumes.yaml`), the mount cost goes away, and the SZZ cache brings a warm history down further (312 s → 74 s natively, Appendix F).

Aniket's earlier run on a Windows laptop never finished, for two reasons, both now understood:

| What he saw | Cause | Fix |
|---|---|---|
| history and resolve failed after 1 s on the first try | Each checkout found its git objects through `.git/objects/info/alternates`, which held an **absolute host path**. Inside the container the repo is at `/opt/meridian`, so the path didn't exist and git couldn't read the checkout. Our first scheduled run on the Mac failed the same way: history and resolve, 3 tries each, about 1 s each | The link is now relative (`collect.borrow_relative`), and collect repairs an older snapshot's link. Test: `test_checkouts_work_wherever_the_data_root_is_mounted` |
| Later tries killed at the 60-minute timeout | The Windows folder mount, slower still than the Mac's | `docker-compose.volumes.yaml`, or a longer timeout. history's is now 180 minutes (`settings.toml`), because the weekly rebuild runs it cold |

That failed run still showed timeouts, retries 5 minutes apart, a log per try, and failing closed: nothing downstream ran, and the last good block stayed live.

## The weekly rebuild

`meridian_rebuild` (run `scheduled__2026-10-18T04:00:00+00:00`) rebuilds 70dd from scratch in its own data folder, with every cache off, then compares it with the update's outputs. Every task succeeded:

| Task | Time | Note |
|---|---:|---|
| fresh, collect, scan | 0 s, 1 s, 13 s | collect replays 70dd's SHAs and snapshot time |
| resolve | 502 s | cold: no resolver cache |
| history | 706 s on try 3 | Try 1 hit the old 60-minute timeout: cold SZZ through the mount, while we ran heavy git comparisons on the same disk. We raised history's timeout to 180 minutes and restarted it. Try 3, on a quiet machine: 706 s |
| owners, join, graph, validate, report | 8, 26, 1, 36, 1 s | |
| bias ∥ gx | 12 s, 17 s | side by side, after validate |
| compare | 1 s | **0 differences** between the rebuild and the update |
| publish | skipped | compare found nothing to correct, so nothing was republished, as designed |

So the from-scratch run equals the incremental update byte for byte, on the real data, inside Airflow.

![](figures/airflow_ui_graph_rebuild.jpg)

*The rebuild run: every task green, publish pink (skipped by compare's short-circuit).*

The weekly GX audit ran 308 expectations over the 25 tables: 0 failed. Its Data Docs are published at `published/gx/70dd1dabfed544b8/index.html`:

![](figures/gx_data_docs.jpg)

*GX's own page marks `ticket` with a cross: its date check finds the 289 Spark tickets dated in the years 0010–0012, as Apache Jira itself has them. Like validate, the audit allows those 289 (`settings.toml [validate.dates] allow`) and counts a failure only above them, so the audit reports 0 failed. One more bad date would alert.*

## Rerun it

```
docker compose up -d --build                                    # from Data-Pipeline/, with .env from .env.example
# UI at http://localhost:8080: trigger meridian_update with {"run_id": "70dd1dabfed544b8", "force": true}
python report/gantt.py                                          # redraws the chart from report/airflow_run.csv
```

The per-task data is in `report/airflow_run.csv`, exported from the Airflow API.
