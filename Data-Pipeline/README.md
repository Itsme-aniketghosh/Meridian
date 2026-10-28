# Meridian data pipeline

Meridian joins code, git and Jira to find every call site of a dying internal API, who owns it, how risky it is, and which teams wait on each other. This folder is the data pipeline: two Airflow DAGs that turn the raw sources into a validated, published SQLite block that every later stage reads. The course write-up is [report/REPORT.md](report/REPORT.md), and the design is in [UPDATE-PLAN.md](notes/UPDATE-PLAN.md).

## Demo for TAs

From `Data-Pipeline/`, after `pip install -r requirements.txt`. Each step prints the rubric item, the command and the result, and the demo ends with a pass/skip/fail table.

- `python run_demo.py short` takes about 10 seconds, with no team data and no Docker. It covers both DAGs and the DVC stages, the pipeline end to end on the built-in fixture, schema and statistics, four injected anomalies caught with an alert delivered to local Slack and SMTP servers, bias slices with mitigations, the KNOWLEDGE-BASE views and a P1–P9 push check.
- `python run_demo.py long` takes 10 minutes or more. It adds every unit test, the GX suite (if its environment exists), the data download, the team-run tests (team data only), every task's command, and Airflow in Docker. `--airflow-run` also triggers `meridian_update` and waits for it.
- `python run_demo.py download` only gets the data: the raw ticket pulls (already there, `dvc pull`, `--zip <team zip>`, or `--public-pull` for a run of your own), then collect's repo checkouts (about 2.5 GB).

Heavy steps check RAM, disk, network and Docker first, and skip with a reason if something is short (`--force` runs them anyway).

## What runs

```
meridian_update (click, daily)
anything_new ── collect ──┬── history ───────── owners ──┐
                          │                              ├── join ── graph ── validate ── report ── publish
                          └── scan ──── resolve ─────────┘

meridian_rebuild (Sunday): fresh ── collect ── … ── validate ──┬── report ── compare ── publish (only if they differ)
                                                              ├── bias (never gates)
                                                              └── gx   (never gates)
```

Each task is one module, `python -m meridian.<task>`, writing to `out/<run_id>/`. The DAGs are built from [dags/pipeline_spec.py](dags/pipeline_spec.py), which lists each task's inputs and outputs.

| Task | What it does | More |
|---|---|---|
| `anything_new` | `meridian.status`: stops the update when nothing is new | below |
| `collect` | Freezes 22 repos at pinned SHAs and the raw ticket pulls into one `run_id` | [shared-snapshot](shared-snapshot/README.md) |
| `history` | Commits, files, tickets, commit-to-ticket keys, SZZ bug fixes | [CONTRACTS](CONTRACTS.md) |
| `scan` | tree-sitter: every place a tracked old name appears | [SCANNER](SCANNER.md) |
| `resolve` | Jedi: what each name really is, and who calls whom | [RESOLVER](RESOLVER.md) |
| `owners` | Merged identities, teams, an owner team per file | [CONTRACTS](CONTRACTS.md) |
| `join` | Call sites with owner and bug fixes; tickets mapped to code; coverage | [JOIN](JOIN.md) |
| `graph` | Which site waits on which, blast radius, ticket chains | [GRAPH](GRAPH.md) |
| `validate` | Schema, statistics, anomalies; builds `meridian.sqlite`. The only gate | below |
| `report` | What changed since the last published run, and which commit caused it | |
| `publish` | Makes the block live: `published/<run_id>.sqlite` and `published/latest.json` | below |
| `bias` | Slices the pipeline's rules by team, module and repo (Fairlearn) | [BIAS](BIAS.md) |
| `gx` | Great Expectations audit with Data Docs | `meridian/gx.py` |
| `fresh`, `compare` | Rebuild only: start from scratch, then compare with the update | below |

## Folder structure

```
Data-Pipeline/
├── dags/                  Airflow: meridian_dag.py (the two DAGs), pipeline_spec.py (their tasks and tables, as data)
├── meridian/              the parts, one module each: python -m meridian.<part> --run-id <id> --config meridian.toml
├── tests/                 unittest: one file per part, plus the DAG, validation, bias; demo.py, dashboard.py
├── golden/                the hashes tests/test_golden.py checks: the team run's, plus v1's record (TESTING.md)
├── labels/                hand-checked answer keys and gold sets (CSV-POLICY.md)
├── report/                REPORT.md, appendices A–D, the scripts that draw the figures
├── notes/                 UPDATE-PLAN.md, HANDOVER.md, PASS-BARS-2026-10-26.md
├── logs/                  Airflow's task logs (git-ignored)
├── meridian.toml          what the parts compute: hashed into the run_id
├── settings.toml          everything else: run, Airflow, alerts, data checks, bias. Never changes a row
├── schema.sql             every table: columns, types, keys, allowed values
├── dvc.yaml, dvc.lock     data versioning: each part as a stage, the hash of every output
├── Dockerfile, docker-compose.yaml, .env.example     Airflow on one machine
└── requirements.txt       every pinned dependency (requirements-gx.txt: the GX audit's own)
```

The data lives at the repo root (or `$MERIDIAN_DATA`), git-ignored: `snapshots/` (inputs) and `out/<run_id>/` (outputs), of which DVC versions only the raw ticket pulls and the `[pipeline]` run's `out/`, then `published/` (the live block) and `rebuild/`. The course guideline's `data/` is our `snapshots/` and `out/`, and its `scripts/` is `meridian/`.

Nothing is hard-coded: [meridian.toml](meridian.toml) holds what the parts compute (a change means a new `run_id`), and [settings.toml](settings.toml) everything else. Secrets live only in `.env` (from [.env.example](.env.example)), which git ignores.

## Set up

You need Python 3.11 (tree-sitter won't build on 3.14), git, about 5 GB of free disk and 3 GB of free memory.

```
conda create -y -n meridian python=3.11
conda activate meridian
pip install -r requirements.txt          # from the repo root: includes Data-Pipeline/requirements.txt
```

The raw Jira and GitLab pulls (about 230 MB) hold real names, so they aren't in git. Get them with `dvc pull` once the team's remote is set up, or from the team zip ([shared-snapshot/README.md](shared-snapshot/README.md)). Afterwards `snapshots/_cache/jira/SPARK/.pull.json` must exist.

## Run it with Airflow

From `Data-Pipeline/`:

```
cp .env.example .env                     # set the UI password; alert secrets if you want them
docker compose up -d --build             # Postgres, scheduler, web UI at http://localhost:8080
```

In the UI, unpause `meridian_update` and `meridian_rebuild`. Task logs are in the UI and in `logs/`, and schedules in `settings.toml [dags.*]`. Each DAG runs one run at a time.

Docker's folder mount is several times slower on Windows and Mac. To use Docker volumes for `snapshots/` and `out/`, add `-f docker-compose.volumes.yaml` after `-f docker-compose.yaml` to every `docker compose` command. The volumes start empty: put the team zip at the repo root and run `docker compose -f docker-compose.yaml -f docker-compose.volumes.yaml run --rm airflow-scheduler python -m zipfile -e /opt/meridian/meridian-raw-973531f7fd4cfd5e.zip /opt/meridian/snapshots/`, or `... run --rm airflow-scheduler bash -c "cd /opt/meridian/Data-Pipeline && /opt/meridian-venv/bin/dvc pull"`.

### meridian_update

It runs daily at 02:00 UTC and on each "Update graph" click: in the UI, on the dashboard, or with `python -m meridian.status --update` (Airflow's REST API, with the UI login in `.env`). A click while a run is queued adds nothing, and `anything_new` ends a run when nothing is new.

Collect makes whatever run the inputs give, unless the trigger config `{"run_id": "...", "prev_run": "..."}` names one. Naming a run, or `{"force": true}`, skips the check. Run ids must be 16 hex characters, or the task fails before any command runs.

`publish` refuses a run with a data error, a partial run (a tracker was down), and a rollback to an older snapshot, so the last good block stays live. After publishing it deletes old runs' `snapshots/` and `out/`, except the live run, its previous run and the frozen team run. With `[dvc] push = true` it also runs `dvc commit` and `dvc push`, but only for the run named in `settings.toml [pipeline] run_id`: it skips other runs and rebuilds, since `dvc.yaml` tracks that run only.

### meridian_rebuild

Sundays at 04:00 UTC it recomputes the last published run from scratch under `rebuild/`, caches off, and `compare` checks every table against the update's, byte for byte. The rebuild is the truth. If they differ, an alert names the part where the difference started, `rebuild/out/<run_id>/compare.json` keeps the rows, and the rebuild is published as `published/<run_id>.rebuild.sqlite`. Publish waits on `compare` only; `bias` and `gx` never gate. On the team run there were 0 differences ([Appendix A](report/appendix-airflow.md)).

### Freshness and timing

```
python -m meridian.status --config meridian.toml          # data 2 days old · 14 new commits · run <id>   (--json for all of it)
```

It exits 0 when something is new, 99 when not. New commits in collect's mirrors, a new repo, a changed `meridian.toml` or no publish yet all count. So does a publish older than `[status] stale_after_hours` (20), but only when some repo follows a branch. With every ref pinned, as in the team run, collect would make the same run again. The dashboard shows the same line with an Update graph button: `python -m tests.dashboard --run-id <id> --config meridian.toml --serve 8765`, then http://127.0.0.1:8765/.

The update ran in Docker on a Mac in 943 s. Running `scan` and `resolve` beside `history` saved 20%, measured from the Airflow Gantt. The bottleneck is `history`'s SZZ step ([Appendix A](report/appendix-airflow.md)).

## Run it without Airflow

Each task is one command, run from `Data-Pipeline/` in the order above. Only collect checks the config against the run_id, but every part computes from it:

```
python -m meridian.collect --run-id c5118b019d642a9b --config meridian.toml
python -m meridian.history --run-id c5118b019d642a9b --config meridian.toml
...                                                   # scan, resolve, owners, join, graph
python -m meridian.validate --run-id c5118b019d642a9b --config meridian.toml
python -m meridian.bias --run-id c5118b019d642a9b --config meridian.toml
python -m meridian.report --run-id c5118b019d642a9b --config meridian.toml
python -m meridian.publish --run-id c5118b019d642a9b --config meridian.toml     # -> published/, if validate passed
```

Or let DVC run whatever changed: `dvc repro`. `python -m tests.demo --run-id c5118b019d642a9b --config meridian.toml` prints each part's tables, and `python -m tests.dashboard` with the same arguments writes one HTML page.

## Reproducibility and DVC

Collect's last line must say `run_id=c5118b019d642a9b`. The same code SHAs, raw pulls and `meridian.toml` give the same `run_id` on every machine, and the same bytes from every part. v1, `70dd1dabfed544b8`, had the same snapshot under the `meridian.toml` before Oct 27, which lacked the owner surname rule, `donotreply` and join's third match step and still pinned SQLite. [Its golden file](golden/70dd1dabfed544b8.json) stays as the record. `tests/test_golden.py` checks all 24 outputs against [golden/c5118b019d642a9b.json](golden/c5118b019d642a9b.json), and all 24 match ([TESTING.md](../TESTING.md) has the one known exception). Don't pass `--run-id new` for the team run: that makes a run of your own.

- Inputs: `snapshots/_cache/jira.dvc` and `gitlab.dvc` pin the raw pulls. The repo clones aren't tracked, because collect rebuilds them byte for byte from the pinned SHAs.
- Outputs: [dvc.yaml](dvc.yaml) declares each part as a stage with what it reads and writes. `dvc commit` records every output's hash in `dvc.lock`, which git tracks, so each commit pins the data its code produced and `dvc diff` shows what changed. Commit `dvc.lock` from Linux or Mac only.
- Remote: `.dvc/config` names the team's GCS bucket as `gcs` (`dvc-gs` is in `requirements.txt`). Until GCP is set up, use `dvc remote add -d --local mine /path/to/store`, then `dvc push`.

## Data quality, alerts and errors

`meridian.validate` loads the block against [schema.sql](schema.sql), writes per-column statistics to `stats.json`, then runs the checks in `settings.toml [validate]`. Errors fail the task (too few rows, bad formats, out-of-range numbers, low coverage). Warnings only alert (missing values, odd dates, outliers, drift). On `c5118b019d642a9b`: 1,788,375 rows in 25 tables, 0 errors and 1 warning. Apache Jira dates 289 Spark tickets in the years 0010–0012. A date before `[validate.dates] earliest` or after the snapshot is invalid: every run warns, and no part uses it as a date. The weekly GX audit ran 308 expectations, and only that date check failed ([Appendix B](report/appendix-quality.md)).

A failed task (after its retries), a data error or a warning sends one Slack or email message, per `settings.toml [alerts]`. A broken channel never breaks the run. A test and `run_demo.py` step 8 deliver a real alert to local Slack-style and SMTP servers (`tests/alert_sinks.py`). The team's Slack is connected: `MERIDIAN_SLACK_WEBHOOK` in `.env` (never in git) points at it, and validate's alerts arrive there. To see them, [join the workspace](https://join.slack.com/t/meridian-flg4799/shared_invite/zt-4ekm8ksh1-WYMNCqG_ECL5jr17~Gzaxg). Email works the same way with an SMTP host in `[alerts]`. Parts log through Python's logging. A part that can't go on exits non-zero with the reason; Airflow retries, then alerts. Parts are idempotent, so retries are safe.

## Bias

`meridian.bias` slices the three deciding rules (owner rule, ticket mapping, resolver) by module, team, component, issue type, year and repo with Fairlearn's `MetricFrame`. A slice of 30 or more rows is flagged when its whole 95% interval is more than 0.10 below overall. Findings and mitigations: [BIAS.md](BIAS.md).

## Tests

```
python -m unittest discover -s tests -t .      # from Data-Pipeline/
```

342 tests, 19 skipped by design on a plain Windows machine (11 need the team data, 5 Airflow, 2 GX, 1 runs off Windows only). CI runs them and flake8 (lines up to 120) on Linux and Windows on every push. More in [TESTING.md](../TESTING.md).
