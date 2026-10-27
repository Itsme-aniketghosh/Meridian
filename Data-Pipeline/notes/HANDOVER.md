# Handover

The team run is `c5118b019d642a9b`: the same snapshot as v1 `70dd1dabfed544b8`, with today's `meridian.toml`. `70dd1dabfed544b8` stays as the frozen v1 record (`golden/70dd1dabfed544b8.json`). Details are in commit `26c46e1`.

## Done

1. **Airflow run.** `meridian_update` (943 s) and `meridian_rebuild` (902 s) both passed on c5118b019d642a9b; compare found 0 differences. The Gantt, the Airflow and GX Data Docs screenshots and [Appendix A](../report/appendix-airflow.md) come from that run.
2. **Docker and team-data tests.** 339 tests pass with the team data; the Airflow (15/15) and GX (7/7) tests pass in their own environments. All 24 outputs match `golden/c5118b019d642a9b.json`.
3. **Bias rerun.** `bias.py` was rerun on c5118b019d642a9b: 32 of 136 slices flagged, 26 hold under Bonferroni. The 730-day lookback was not adopted (held-out interval 0.0–0.0, [BIAS.md](../BIAS.md)).
4. **Orchestration changes.** On main since the `report-redteam` merge; both DAGs ran with them on c5118b019d642a9b.
5. **Team decisions.** The lookback is settled (not adopted). The licence, the DVC remote and Slack are still open, below.
6. **New team run.** `settings.toml [pipeline] run_id`, `tests/test_golden.py` `TEAM_RUN`, `run_demo.py` and the demo and collision helpers all name c5118b019d642a9b. `dvc.lock` was recorded in the pipeline image, and the golden file was frozen from it.

## Still open

- **DVC remote.** The GCS bucket in `.dvc/config` (`gs://meridian-pipeline-snapshots/dvc`) is still a placeholder, so `dvc pull` works for nobody yet. Until it's real, use a local remote, from the Docker image or a Mac so the hashes match `dvc.lock`:
  ```
  dvc remote add -d --local <name> <path>
  dvc push
  ```
- **Slack alerts.** No channel is configured, so no real alert has gone out. Set `MERIDIAN_SLACK_WEBHOOK` in `.env` to a test webhook, force one warning, rerun, and screenshot the alert for the report:
  ```
  docker compose exec airflow-scheduler airflow dags trigger meridian_update -c '{"run_id": "c5118b019d642a9b", "force": true}'
  ```
- **Licence.** `LICENSE` is MIT, copyright "the Meridian team". Confirm it, or change it.
- **Jira paging by key.** The full ticket pull in `meridian/collect.py` still pages by `startAt`; only the key listing for deltas pages by key. A ticket deleted mid-pull would shift the offsets.
- **`src/` layout.** The package still lives in `Data-Pipeline/meridian/`, not under `src/`.

Rerun the team-data tests after any change to these:

```
MERIDIAN_RUN_ID=c5118b019d642a9b python -m unittest tests.test_golden tests.test_scan tests.test_resolve -v
```

The pipeline's own known limits are listed in [Appendix D](../report/appendix-testing.md) "Still open".
