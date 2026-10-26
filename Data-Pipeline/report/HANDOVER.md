# Handover to Utkarsh: what's left on `report-redteam`

Aniket's session wrote the report, red-teamed the pipeline and fixed what came up, all on branch `report-redteam`. **Don't merge it yet.** It needs the Airflow run below, and your review of the orchestration changes, which are in your area.

## What's on the branch

- `Data-Pipeline/report/`: [REPORT.md](REPORT.md), mapped point by point to the course guidelines, and Appendices A to E. Charts are in `figures/`, with the scripts that draw them (`figures_quality.py`, `figures_scenarios.py`, `gantt.py`).
- `Data-Pipeline/run_demo.py`, the TA demo:
  - `short`: about 10 s, no team data, no Docker.
  - `long`: everything, with Airflow if Docker is up.
  - `download`: gets the data. Free RAM and disk are checked before each heavy step.
- Fixes from the red team ([Appendix E](appendix-redteam.md)): knowledge-base queries, validate/writer/alert, bias, DAG and DVC, privacy, plus a code-style and housekeeping pass. The unit tests pass on Windows.

## 1. Run Airflow on your Mac (the main thing left)

On Aniket's Windows laptop the run never finished. history and resolve hit their 60-minute timeout on every try, because of the slow Docker folder mount ([Appendix A](appendix-airflow.md)). On a Mac it should take about 12 minutes.

```
cd Data-Pipeline
cp .env.example .env              # then set AIRFLOW_ADMIN_PASSWORD and POSTGRES_PASSWORD: compose now refuses to start without them
docker compose up -d --build
docker compose exec airflow-scheduler airflow dags unpause meridian_update
docker compose exec airflow-scheduler airflow dags trigger meridian_update -c '{"run_id": "70dd1dabfed544b8", "force": true}'
```

If your Postgres volume was created with the old default password, set `POSTGRES_PASSWORD=airflow` in `.env`.

When the run is done:
1. **Export the tries for the Gantt.** gantt.py expects `report/airflow_run.csv` with columns `task_id,state,start_date,end_date,try_number,meridian_run`. From the REST API:
   ```
   curl -s -u "$AIRFLOW_ADMIN_USER:$AIRFLOW_ADMIN_PASSWORD" \
     "http://localhost:8080/api/v1/dags/meridian_update/dagRuns/<dag_run_id>/taskInstances" \
   | python -c "import csv,json,sys; w=csv.writer(sys.stdout); w.writerow(['task_id','state','start_date','end_date','try_number','meridian_run']); [w.writerow([t['task_id'],t['state'],t['start_date'],t['end_date'],t['try_number'],'70dd1dabfed544b8']) for t in json.load(sys.stdin)['task_instances']]" > report/airflow_run.csv
   python report/gantt.py
   ```
2. **Take screenshots** of the Graph view and the Gantt view (DAG, then the run, then Gantt). Put them in `report/figures/`.
3. **Rewrite [Appendix A](appendix-airflow.md)** with the real wall time and the measured parallel saving. The current text is the Windows attempt. Then drop the "estimated" wording in REPORT.md row 9.
4. **Run `meridian_rebuild` once.** Check that compare finds 0 differences, and screenshot the GX Data Docs page (`published/gx/<run_id>/index.html`). Both are rubric items with no evidence yet.

## 2. Tests that only run in Docker or with team data

```
# Airflow half of test_dag, including the new template-rendering test (passes in CI's airflow job since 99fb73c; rerun locally if you change the DAG)
docker compose run --rm --no-deps -e CONNECTION_CHECK_MAX_COUNT=0 -w /opt/meridian/Data-Pipeline airflow-scheduler python -m unittest tests.test_dag -v

# GX audit tests, in the image's GX environment
docker compose run --rm --no-deps -w /opt/meridian/Data-Pipeline airflow-scheduler /opt/meridian-gx-venv/bin/python -m unittest tests.test_gx -v

# Team-run tests, after the Airflow run has rewritten out/70dd…
MERIDIAN_RUN_ID=70dd1dabfed544b8 python -m unittest tests.test_golden tests.test_scan tests.test_resolve -v
```

The golden check matched 20 of 24 files on Windows, using outputs from before the v1.2 golden and the join fix. On a fresh Mac run:
- `resolution`, `call_edge` and `blast_radius` should match.
- `call_site` may still differ, from your post-freeze join fix (`b277e65`). If it does, it's your call: refreeze the golden, or explain the difference in TESTING.md.

## 3. Rerun bias with the new code

`bias.py` now does Wilson intervals, a held-out check of the mitigation and a people-level slice. Several numbers in BIAS.md and Appendix B are marked "needs the rerun".

```
python -m meridian.bias --run-id 70dd1dabfed544b8 --config meridian.toml
python report/figures_quality.py ../out/70dd1dabfed544b8     # needs matplotlib
```

Then fill in the numbers marked "rerun" in [BIAS.md](../BIAS.md) and [Appendix B](appendix-quality.md) section 5. Adopt the 730-day lookback only if the held-out interval stays above 0.

## 4. Behaviour changes in your area: please review

| Change | Where |
|---|---|
| A forced rerun of the published run keeps its prev_run | `dags/meridian_dag.py` PREV |
| The rebuild picks its run once (in fresh, read through xcom) | `meridian_dag.py`, `pipeline_spec.py` |
| Publish refuses to roll back the live run, and refuses a partial run unless `--allow-partial` is passed | `meridian/publish.py` |
| When compare differs, the rebuild's `out/` overwrites main's | `publish.py` |
| `bias.json` ships beside the block; `config_hash` is written into latest.json | `publish.py` |
| DVC commit/push is skipped for any run other than `[pipeline] run_id` | `publish.py` |
| compare depends on report only; rebuild publish depends on compare and bias | `pipeline_spec.py`, `test_dag.py` |
| validate is never retried (data errors don't change on retry) | `settings.toml [orchestration] task_retries` |
| stale_after_hours 24 → 20; collect timeout 30 → 90 min | `settings.toml` |
| anything_new: a new repo or a config edit counts as new | `meridian/status.py` |
| `dvc.yaml`: missing deps added (join.py for resolve, the manifest, common.py, config) | `dvc.yaml` |
| validate deletes old `anomalies.json`/`stats.json` first; missing tables are refused | `meridian/validate.py`, `writer.py` |
| Compose: UI bound to 127.0.0.1; no default passwords | `docker-compose.yaml` |

`meridian.toml` only got a comment change, and the run_id hashes the parsed config, so 70dd stays 70dd.

## 5. Team decisions

- **LICENSE:** we added MIT, copyright "the Meridian team". Confirm it, or change it.
- **The 730-day lookback for a person's team:** it changes `meridian.toml` and `owners.py`, so it means a new team run_id.
- **DVC remote:** the GCS bucket is still a placeholder, so `dvc pull` works for nobody yet.
- **Slack alerts:** no channel is configured. A test webhook in `.env` plus one forced warning would give the report a real alert screenshot.

## Known open items

These are listed in [Appendix E](appendix-redteam.md):
- the bias flag rule isn't corrected for multiple comparisons
- P1's precision on the replay is 0.36
- `blocked_by` is empty on real data
- outcome tables aren't in `schema.sql` yet
