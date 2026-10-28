# Handover

The team run is `c5118b019d642a9b`: the same snapshot as v1 `70dd1dabfed544b8`, with today's `meridian.toml`. `70dd1dabfed544b8` stays as the frozen v1 record (`golden/70dd1dabfed544b8.json`). Details are in commit `26c46e1`.

## Done

1. Both DAGs passed on c5118b019d642a9b: `meridian_update` in 943 s and `meridian_rebuild` in 902 s, and compare found 0 differences. The Gantt, the Airflow and GX Data Docs screenshots and [Appendix A](../report/appendix-airflow.md) come from that run.
2. With the team data, 342 tests pass (7 skipped); the Airflow (15/15) and GX (7/7) tests pass in their own environments. All 24 outputs match `golden/c5118b019d642a9b.json`.
3. We reran `bias.py` on c5118b019d642a9b after the invalid-dates change (`dvc.lock`, `73baf1a`): 29 of 133 slices flagged, 23 hold under Bonferroni. The 730-day lookback was not adopted (held-out interval 0.0–0.0), and the 0.30 abstain threshold was kept after measuring ([BIAS.md](../BIAS.md)).
4. The orchestration changes are on main since the `report-redteam` merge, and both DAGs ran with them on c5118b019d642a9b.
5. The team settled the lookback (not adopted). The licence and the DVC remote are still open, below. Slack is connected.
6. `settings.toml [pipeline] run_id`, `tests/test_golden.py` `TEAM_RUN`, `run_demo.py` and the demo and collision helpers all name c5118b019d642a9b. `dvc.lock` was recorded in the pipeline image, and the golden file was frozen from it.
7. Slack alerts are done. The team workspace receives validate's alerts through `MERIDIAN_SLACK_WEBHOOK` in `.env` (a fixture block with two planted errors alerted there). Email still has no team mailbox. The webhook was shared in a chat once: replace it in Slack and keep the new one only in `.env`.
8. The SZZ and scan caches are on for the daily update (`settings.toml [cache]`, `[history] szz_workers` 4): warm, the update took 480 s instead of 943 s, with the same 24 files. The rebuild keeps every cache off. The resolve cache was slower and is off.
9. The 289 Jira dates in the years 0010–0012 are invalid, not allowed: validate warns on every run, bias leaves them out of the year slices, and GX's date check fails on them.

## Still open

- The DVC remote: the GCS bucket in `.dvc/config` (`gs://meridian-pipeline-snapshots/dvc`) is still a placeholder, so `dvc pull` works for nobody yet. Until it's real, use a local remote, from the Docker image or a Mac so the hashes match `dvc.lock`:
  ```
  dvc remote add -d --local <name> <path>
  dvc push
  ```
- Rerun `report/figures_quality.py` on the new `bias.json`, so `bias_ticket_mapping.png` drops the 0010–0012 bars, and add the year slicing's difference and ratio back to BIAS.md.
- `LICENSE` is MIT, copyright "the Meridian team". Confirm the licence or change it.
- Jira paging: the full ticket pull in `meridian/collect.py` still pages by `startAt`; only the key listing for deltas pages by key. A ticket deleted mid-pull would shift the offsets.
- The package still lives in `Data-Pipeline/meridian/`, not under `src/`.

Rerun the team-data tests after any change to these:

```
MERIDIAN_RUN_ID=c5118b019d642a9b python -m unittest tests.test_golden tests.test_scan tests.test_resolve -v
```

The pipeline's own known limits are listed in [Appendix D](../report/appendix-testing.md) "Still open".
