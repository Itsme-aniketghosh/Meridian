# S4 refresh job: run log, Fri Nov 20 2026 (packet days 1 and 2)

The job image, and the Spark-only update run locally end to end. Branch `utkarsh-refresh`, cut from `rochan-s1-terraform` at `b275174` (D4's `deploy.toml` and `deploy_config.py` are only there). Python 3.11.17 (`.venv`), Docker 29.9.2, macOS.

**Data root:** `~/Desktop/Meridian-refresh-data`, outside the repo. It holds copies (APFS clones) of the Spark mirror and the `jira`, `szz`, `scan`, `resolve` and `diffs` caches from the main data root. It's a separate root because `publish` prunes old runs' folders and mirror refs under its own root, so the main root's `published/` and `refs/runs/` were never touched. **Store:** `local:~/Desktop/Meridian-refresh-data/store`, a folder that stands in for the GCS buckets. Nothing went to GCP.

**S5's monitor:** `Model-Deployment/monitor/{__init__.py,drift.py,monitor.toml}` were copied from `origin/vishwa-monitor` (`ac64cb8`) into the working tree, untracked, for these runs. They're Vishwa's and aren't committed here. They come in with her PR.

## Summary

| # | What | Verdict |
|---|---|---|
| 0 | Dry run, laptop and image | PASS |
| 1 | Image build | PASS: 1 min 26 s cold, 2.05 GB |
| 2 | Imports in the image (every part, `model.data`, `model.refresh`, `monitor.drift`, git 2.47.3) | PASS |
| 3 | Run 1, full job | **FAIL**, at validate: 8 data errors from the migration-side floors. Alert path exercised (logged; no webhook set) |
| 4 | Fix: Spark-only settings | Validate on run 1's block: 0 errors, 3 warnings, PASS |
| 5 | Run 2, full job | **PASS**: 142 s, block `d18aa548f8ca5f98`, D2 report, retrain = true |
| 6 | Run 3, fresh data root, block pulled from the store | **PASS**: nothing new, 0.5 s |
| 7 | Tests, `Model-Deployment/tests` | PASS: 40 tests (16 new in `test_refresh.py`, Rochan's 24) |
| — | Warnings to look at | WARN: 2 new validate warnings with Spark alone (below) |

## 0. Dry run

```
.venv/bin/python Model-Deployment/refresh/job.py --data-root /tmp/x --store local:/tmp/y --dry-run
docker run --rm meridian-refresh --dry-run --store local:/work/store
```

Every step is printed in order: pull, anything_new, collect, history, scan, resolve, owners, join, graph, validate, publish, table, drift, push, retrain, done. The first dry run on the laptop stopped at drift ("monitor/drift.py isn't here") before S5's files were copied in, which was the expected check. After the copy: PASS on both.

## 1–2. Image

```
docker build -f Model-Deployment/refresh/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) -t meridian-refresh .
```

The build exited 0 in 1:25.7 the first time. The rebuild after the settings fix used the cache. The image is 2.05 GB, mostly LightGBM, SHAP, MLflow and Optuna from the model's pins, kept for the retrain step. The import check inside the image printed `IMPORTS-OK`. **PASS.**

## 3. Run 1: FAIL at validate

Started 2026-11-20 (UTC), HEAD `b275174`.

```
.venv/bin/python Model-Deployment/refresh/job.py --data-root ~/Desktop/Meridian-refresh-data --store local:~/Desktop/Meridian-refresh-data/store
```

| Step | ms | Note |
|---|---:|---|
| pull | 0 | empty store: `last_run` null |
| anything_new | 3,596 | "nothing published yet": new |
| collect | — | Jira delta pulled: 2,416 tickets changed since the last pull. Run `a2d09b7c292dea78` |
| history | 527,227 | 46,226 first-parent commits, 61,723 tickets. SZZ cache cold for the new commits |
| scan, resolve | 109, 88 | Spark has no tracked old names: nothing to scan |
| owners, join, graph | 2,275 · 3,345 · 596 | |
| validate | exit 4 | **8 errors** |

The errors: `rows` for candidate, resolution, call_site, call_edge, module_import and blast_radius ("0 rows, expected at least 1"), and `coverage` for call_sites_resolved and call_sites_owned ("0 of 0, floor 0.95"). They're the migration side's floors in `settings.toml [validate.rows]` and `[validate.coverage]`, measured on the Django runs. A Spark-only run fills none of those tables by construction. The Spark, ticket and SZZ tables passed.

The job did what it should: D5 line with `severity: ERROR` and `outcome: failed`, `meridian.alert` called ("refresh job failed at update: exit 4 …"; only logged, since `MERIDIAN_SLACK_WEBHOOK` isn't set locally), no publish, nothing pushed, exit 1.

## 4. Fix: the Spark-only settings

`job.spark_settings()` writes `settings.spark.toml` without those 6 row floors and 2 coverage floors, and the job points `MERIDIAN_SETTINGS` at it. Nothing else changes: `tickets_mapped_jira` (0.50) and every other floor, limit and section stay as they are. A test checks this, and the job stops if `settings.toml`'s layout changes.

```
MERIDIAN_DATA=... MERIDIAN_SETTINGS=.../settings.spark.toml .venv/bin/python -m meridian.validate --run-id a2d09b7c292dea78 --config .../meridian.spark.toml
```

1,045,878 rows in 30 tables · 0 errors · 3 warnings. **PASS.**

## 5. Run 2: PASS

2026-11-20T22:11:56Z to 22:14:18Z. Same command as run 1.

| Step | ms |
|---|---:|
| pull | 0 |
| anything_new | 971 |
| collect | (in update) |
| history | 51,502 |
| scan · resolve | 61 · 89 |
| owners · join · graph | 2,551 · 3,668 · 648 |
| validate | 12,479 (0 errors, 3 warnings) |
| update, parts' sum | 70,998 |
| table (`model.data`) | 3,590 |
| drift | in done |
| push | 87 |
| **done, whole job** | **142,160** |

- **Block** `d18aa548f8ca5f98`: Spark at master `094e460b44f705f0ff92b87583310638604a2acb`, snapshot_at `2026-11-20T22:11:57Z`, 46,227 first-parent commits, 61,723 tickets, 1,045,931 rows. 361.6 MB in the store under `block/`, with `latest.json` written after the file.
- **D2 report** `monitor/reports/d18aa548f8ca5f98.json`: 650 commits from 2026-10-21 to 2026-11-20 against 26,050 train rows. PSI 0.351 on `la` (median 29 → 87), 75% of features drifted. **retrain: true**. `decay` is null until S5's decay check is in.
- **Retrain step:** logged as "due, not wired yet". It's the packet's Tue Nov 24 item.
- **Refresh speed (PLAN §6, ≤ 10 min, new block to monitoring report):** 142 s with warm caches on a laptop. Run 1's history took 527 s with the SZZ cache cold for the new commits. **PASS on a laptop**. Not measured in Cloud Run yet.

## 6. Run 3: nothing new, PASS

A fresh data root with only the Spark mirror, and the same store.

pull 100 ms (`last_run` d18aa548f8ca5f98, `latest.json` and the 361 MB block copied into `published/`) → anything_new 444 ms ("data 0 days old · 0 new commits", exit 99) → done, "nothing new", exit 0. Nothing collected, nothing pushed.

## 7. Tests

```
.venv/bin/python -m unittest discover -s Model-Deployment/tests
```

Ran 40 tests, OK. `test_refresh.py` covers:
- the Spark-only config and settings
- `model.toml` with only the run_id moved
- the store
- D2's keys
- the steps in the DAG's order
- the pointer pushed after its file, then the report
- nothing new ending the run
- a failed part alerting and exiting 1 with nothing pushed
- the last block pulled first
- D5's keys, with no `@` on any line

The run tests skip when S5's `drift.py` isn't in the tree.

## Warnings

1. `ticket.created_at`: 289 invalid dates (SPARK-5xx, year 0010–0012). The team run `c5118b019d642a9b` has the same warning. Not new.
2. `person_alias.email`: 1 of 3,875 missing. **New with Spark alone**: the team run's other datasets hide it. To check with Chaitali (identity).
3. `file_owner.n_commits`: 20.6% outside the IQR fences 0..0. **New with Spark alone**: Spark's author-mode owners leave the quartiles at 0, so the fence is degenerate. Warning only.

## Open for the next days

- **Cloud Run sizing:** the job's disk is in memory. The Spark mirror is 659 MB, the checkout and caches more, and the block 361 MB, against `[jobs.refresh] memory = "4Gi"`. Measure it on Mon Nov 23, then raise the memory or mount the cache bucket (with Rochan).
- **A cold cloud run** has no mirror and no Jira or SZZ cache: a full Jira pull (61,723 tickets) and a cold SZZ won't fit the 1 h timeout. S10's cache in `buckets.cache` is needed before the first cloud run, not after it. Move it to Mon Nov 23.
- **Every block retrains** under the 50% rule (Vishwa's finding). This run shows it too: 75% drifted. The thresholds are signed off today.
- **D2's owner is S5.** `job.d2()` wraps `monitor.drift`'s report until S5 writes D2 itself.
- **No `--prev-run`** yet: the first Spark-only block has no earlier Spark-only run to compare with. From the second cloud run on, the job can pass the last block's run as prev, once its `out/` is kept in the cache bucket.

---

# Sat Nov 21 2026: history.pkl for the serve image (Rachna's #148 handoff)

#148 (S2) scores a push from `Model-Deployment/serve/history.pkl` (`python -m serve.precompute --block …`), which has to be in the serve image's build context. CI has no block, so the refresh job makes it. Branch `utkarsh-history-pkl`, from main `99231cf`.

| # | What | Verdict |
|---|---|---|
| 1 | #148's `model/data.py` refactor (`History` class) gives the same feature table | **PASS**: byte-identical on both blocks |
| 2 | The job: `history_pkl` step after publish; push puts `history/<run_id>.pkl` and `history.pkl` after the block, before `latest.json` | Done |
| 3 | Tests: 20 in `test_refresh.py` (4 new), 65 in `Model-Deployment/tests`; flake8 as CI runs it | **PASS** |
| 4 | Run 4: the whole job with #148's serve code, on the local data root | **PASS**: 277 s, block `68c476cf891853a2`, history.pkl 7.6 MB in 7.7 s |
| 5 | That history.pkl scores pushes like a block replay | **PASS**: 30 of 30 pushes, 0 feature rows differ, max risk gap 1.1e-16 |
| 6 | Job image on main: copies `serve/*.py`, imports | **PASS**. `precompute.py` isn't on main yet, so the job skips the step and logs it |

## 1. The feature table under #148

`python -m model.data --config <model.toml> --published <published>`, from #148's tree (`cea3772`, a scratch worktree) and from main, same machine:

| Block | #148 | main | Committed |
|---|---|---|---|
| `c5118b019d642a9b` (the training block) | `9c0ab22048fff931ac89e27c20112c56` | `9c0ab220…` | `9c0ab220…` |
| `d18aa548f8ca5f98` (Nov 20) | `187cfd8a1a9692d2b4ece33a8ee00346` | `187cfd8a…` | n/a |

`features.schema.json` is identical too. The refactor changes no feature.

## 2. The step

- `history_pkl` runs `python -m serve.precompute --block <data root>/published/<run_id>.sqlite --run-id <run_id> --out <work>/history.pkl` from `Model-Deployment/`. It uses the endpoint's own code, so the job and `/score` can't drift.
- The D5 line carries `history_md5` and `history_mb`. While `serve/precompute.py` isn't in the tree, the step logs "skipped" and the job goes on.
- Push order: block file → `history/<run_id>.pkl` → `history.pkl` → `latest.json` → D2 report. `history.pkl` is the name `deploy.yml` reads.
- Image: `COPY Model-Deployment/serve/*.py`, plus its allowlist line. Never `history.pkl` itself.

## 4. Run 4

2026-11-22, from #148's tree with this branch's `job.py`. Data root `~/Desktop/Meridian-refresh-data`, local store.

| Step | ms |
|---|---:|
| pull (last run d18aa548f8ca5f98) | 1 |
| anything_new | 1,853 (published over 20 h ago: new) |
| history · scan · resolve | 98,789 · 132 · 183 |
| owners · join · graph · validate | 4,349 · 6,823 · 1,161 · 23,061 |
| **history_pkl** | **7,742**: 7.6 MB, md5 `dac5da24f2c18f6fc1b7992fcfd4e5cc` |
| table | 6,441 |
| drift | 625 new commits, retrain = true (PSI 0.358 on `la`, 75% drifted) |
| push | 145 |
| **done** | **277,346**, exit 0 |

277 s against run 2's 142 s: history took 99 s against 52 s, on a busier laptop. It's under the 10-minute bar either way.

## 5. Does it score like the block?

`serve.scorer.Scorer(history=<store>/block/history.pkl)` against `Scorer(block=<68c476cf block>)`, on the 30 newest first-parent Spark commits sent as pushes (numstat, subject, author hash):
- 30 of 30 have identical feature rows, and the largest risk difference is 1.1e-16.
- Start: 3.0 s from history.pkl against 4.65 s from the block. The slowest push took 102 ms, cold.
- `/health` is ok, with champion `85b4684a…` and md5 `2a0c9cc6…`.

## For Chaitali (deploy.yml)

Before `docker build`, copy `gs://<buckets.block>/history.pkl` to `Model-Deployment/serve/history.pkl`. The deploy account needs read on the block bucket. Until the job runs in the cloud, one history.pkl from the current block can be put there by hand (through Rochan).
