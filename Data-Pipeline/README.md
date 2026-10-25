# Meridian data pipeline

Meridian joins code, git and Jira to find every call site of a dying internal API, who owns it, how risky it is, and which teams wait on each other. This folder is the data pipeline: two Airflow DAGs from the raw sources to a validated, published SQLite block (the knowledge base every later stage reads). `meridian_update` runs on the "Update graph" click and daily; `meridian_rebuild` recomputes everything weekly and checks the updates against it ([UPDATE-PLAN.md](UPDATE-PLAN.md)).

```
meridian_update (click, daily)
anything_new ── collect ──┬── history ───────── owners ──┐
                          │                              ├── join ── graph ── validate ── report ── publish
                          └── scan ──── resolve ─────────┘

meridian_rebuild (Sunday): fresh ── collect ── … ── validate ── bias ── compare ── publish (only if they differ)
```

| Task | Does | Writes (`out/<run_id>/`) | Doc |
|---|---|---|---|
| `collect` | Freezes the inputs: 22 repos at pinned SHAs, the raw Jira and GitLab pulls, one `run_id` | `run` | [shared-snapshot](shared-snapshot/README.md) |
| `history` | Commits, files, tickets as of the snapshot, commit → ticket keys, SZZ bug fixes | 8 tables | [CONTRACTS](CONTRACTS.md) |
| `scan` | tree-sitter: every place a tracked old name appears, with role and scope | `candidate` … | [SCANNER](SCANNER.md) |
| `resolve` | Jedi: what each name really is; who calls whom | `resolution`, `call_edge` | [RESOLVER](RESOLVER.md) |
| `owners` | Merged identities, teams, an owner team per file | `person` … `file_owner` | [CONTRACTS](CONTRACTS.md) |
| `join` | Call sites with owner, bug fixes, status, stable IDs; tickets → code; coverage | `call_site`, `ticket_map`, `coverage` | [JOIN](JOIN.md) |
| `graph` | Which site waits on which, blast radius, ticket chains | `blocked_by`, `blast_radius`, `chain` | [GRAPH](GRAPH.md) |
| `validate` | Schema, statistics, anomalies; alerts | `meridian.sqlite`, `stats.json`, `anomalies.json` | below |
| `bias` | Slices the pipeline's rules by team, module and repo (Fairlearn) | `bias.json` | [BIAS](BIAS.md) |
| `report` | What changed since the last published run, and which commit caused it (`meridian.report`) | | |
| `publish` | Only after validate, and no data error: copies the block to `published/<run_id>.sqlite`, points `published/latest.json` at it; with `[dvc] push`, `dvc commit` and `dvc push` | `published/` | below |
| `anything_new` | `meridian.status`: new commits since the published SHAs, or a publish older than a day; else the update stops | | below |
| `compare` | Rebuild only: every table byte for byte against the update's; a difference alerts and publishes the rebuild | | below |

## Folder structure

```
Data-Pipeline/
├── dags/                  Airflow: meridian_dag.py (the two DAGs), pipeline_spec.py (their tasks and tables, as data)
├── meridian/              the parts, one module each: python -m meridian.<part> --run-id <id> --config meridian.toml
├── tests/                 unittest: one file per part, plus the DAG, validation, bias; demo.py, dashboard.py
├── labels/                hand-checked answer keys and gold sets (CSV-POLICY.md)
├── logs/                  Airflow's task logs (git-ignored)
├── meridian.toml          what the parts compute: hashed into the run_id
├── settings.toml          everything else: run, Airflow, alerts, data checks, bias. Never changes a row
├── schema.sql             every table: columns, types, keys, allowed values
├── dvc.yaml, dvc.lock     data versioning: each part as a stage, the hash of every output
├── Dockerfile, docker-compose.yaml, .env.example     Airflow on one machine
└── requirements.txt       every pinned dependency
```

The data itself lives outside this folder, at the repo root (or under `$MERIDIAN_DATA`): `snapshots/` (inputs), `out/<run_id>/` (outputs), `published/` (the live block and `latest.json`) and `rebuild/` (the weekly rebuild's own `out/`). All are git-ignored; DVC versions `snapshots/` and `out/`.

## Settings: three places, nothing hard-coded

| File | Holds | In git | Changes the `run_id` |
|---|---|---|---|
| [meridian.toml](meridian.toml) | What the parts compute: datasets, pinned refs, team rules, thresholds | yes | **yes**: a change means a new team run |
| [settings.toml](settings.toml) | The run to process, Airflow (schedule, retries, timeouts, parallelism), alert recipients and SMTP, data checks, bias slicing, DVC push | yes | no |
| `.env` (from [.env.example](.env.example)) | Secrets and machine-specific values: the Slack webhook, SMTP login, the Airflow UI login, ports | **no** | no |

`settings.toml` names the environment variable that holds each secret (`[alerts] slack_webhook_env`), so no secret is ever in git.

## Set up

Python 3.11 (tree-sitter won't build on 3.14), and git.

```
conda create -y -n meridian python=3.11
conda activate meridian
pip install -r requirements.txt          # from the repo root: includes Data-Pipeline/requirements.txt
```

**The raw data.** The Jira and GitLab pulls are not in git (real names, 230 MB). Either:
- `dvc pull` once the team's DVC remote is set up (below), or
- the team zip: download `meridian-raw-973531f7fd4cfd5e.zip` (link in [shared-snapshot/README.md](shared-snapshot/README.md), team only), check `sha256 = d422ba26…f2d523`, and unzip it into `snapshots/` at the repo root. `snapshots/_cache/jira/SPARK/.pull.json` must exist.

About 5 GB free disk (repo clones, outputs, DVC cache) and 3 GB free memory.

## Run it

**With Airflow (Docker).** From `Data-Pipeline/`:

```
cp .env.example .env                     # set the UI password; alert secrets if you want them
docker compose up -d --build             # Postgres, scheduler, web UI at http://localhost:8080
```

In the UI, unpause `meridian_update` and `meridian_rebuild`. Task logs are in the UI and in `Data-Pipeline/logs/`. Ids and schedules are in `settings.toml [dags.update]` and `[dags.rebuild]`; both run one at a time (`max_active_runs = 1`).

- **`meridian_update`**: the "Update graph" click and daily at 02:00 UTC. Trigger it in the UI, with `python -m meridian.status --update` (Airflow's REST API, the UI login from `.env`), or with the dashboard's button (below). It processes `[pipeline] run_id`, or the trigger's config `{"run_id": "...", "prev_run": "..."}`; prev_run defaults to the last published run. Its first task, `anything_new`, stops the run when nothing changed since the last publish, so repeat clicks cost nothing; a click while a run is queued adds nothing. It publishes only if validate passed: a failed run leaves the last good block live.
- **`meridian_rebuild`**: Sunday 04:00 UTC. Recomputes the last published run from scratch under `rebuild/` (`fresh` wipes its `out/<run_id>/` and links the snapshot and the previous run in), runs bias, then `compare`s every table with the update's. 0 differences: done. Otherwise: an alert, and the rebuild is published as `published/<run_id>.rebuild.sqlite`.

**Staleness.** `python -m meridian.status --config meridian.toml` prints `data 2 days old · 14 new commits · run <id>` (`--json` for all of it): the published run's age, and per repo the commits between its published SHA and the tip of its ref in the local mirror (`[status] mirrors`, collect's bare clones; never fetched). Exit 0 = something new, 99 = nothing new. The dashboard shows the same line with an **Update graph** button: `python -m tests.dashboard --run-id <id> --config meridian.toml --serve 8765`, then http://127.0.0.1:8765/.

**Without Airflow.** Each task is one command, from `Data-Pipeline/`, in the order above:

```
python -m meridian.collect --run-id 70dd1dabfed544b8 --config meridian.toml
python -m meridian.history --run-id 70dd1dabfed544b8 --config meridian.toml
...                                                   # scan, resolve, owners, join, graph
python -m meridian.validate --run-id 70dd1dabfed544b8 --config meridian.toml
python -m meridian.bias --run-id 70dd1dabfed544b8 --config meridian.toml
python -m meridian.publish --run-id 70dd1dabfed544b8 --config meridian.toml     # -> published/, if validate passed
```

or let DVC run what changed: `dvc repro`. Then look at the data: `python -m tests.demo --run-id 70dd1dabfed544b8 --config meridian.toml` (each part's tables in the terminal) and `python -m tests.dashboard ...` (one HTML page).

**Reproducibility.** Collect's last line must say `run_id=70dd1dabfed544b8`: the same code SHAs, raw pulls and `meridian.toml` give the same `run_id` on every machine, and every part gives the same bytes for the same run (`call_site.jsonl` sha256 starts `faff6dfd75931183`). Never pass `--run-id new`: that makes a run of your own.

## Data versioning (DVC)

- **Inputs:** `snapshots/_cache/jira.dvc` and `gitlab.dvc` pin the raw pulls (`dvc add`). The repo clones aren't tracked: collect rebuilds them byte for byte from the pinned SHAs in `meridian.toml`.
- **Outputs:** [dvc.yaml](dvc.yaml) declares each part as a stage, with the code, settings and tables it depends on and the tables it writes. With `[dvc] push = true`, the DAGs' `publish` task runs `dvc commit --force`, which writes every output's hash into `dvc.lock`, then `dvc push`. Git tracks `dvc.lock`, so every commit pins the exact data its code produced; `dvc diff` shows what changed between two commits.
- **Remote:** `.dvc/config` names the team's GCS bucket as remote `gcs` (from `settings.toml`, a placeholder until GCP is set up; needs `pip install dvc-gs`). Until then, keep a copy anywhere with `dvc remote add -d --local mine /path/to/store`, then `dvc push`. Set `[dvc] push = true` for publish to push after every published run.

## Data quality: schema, statistics, anomalies

`meridian.validate` runs after `graph`, with thresholds in `settings.toml [validate]`:

1. **Schema.** The block loads with the writer against [schema.sql](schema.sql): names, types, NOT NULL, allowed values, one `run_id`, no duplicate key. Any violation stops here.
2. **Statistics.** Per table and column: rows, missing, distinct, min / max / mean, most common values → `stats.json`, so the next run compares against it.
3. **Checks.** Errors (fail the task, alert): too few rows, bad formats (UTC times, 16-hex IDs, 40-hex SHAs, `owner/name` repos, POSIX paths, sorted JSON lists), out-of-range numbers, coverage below its floor. Warnings (alert, run goes on): missing values above a column's allowance, implausible dates, outliers beyond IQR fences (on a log scale for long-tailed counts), row-count drift from the previous run.

On run `70dd1dabfed544b8`: 1,788,242 rows in 25 tables, 0 errors. It found 289 Spark tickets dated in the years 0010–0012; Apache Jira itself has those dates, so they are reported, not fixed.

## Alerts, logging, errors

- **Alerts** (`meridian/alert.py`): a failed task (after its retries), a data error or a data warning sends one message to Slack and/or email, as `settings.toml [alerts]` says (`on`, `email_to`, SMTP server; secrets from `.env`). Nothing configured: logged only. A broken channel is logged and never breaks the run.
- **Logging:** every part logs counts as it goes to stdout; Airflow keeps each task's log (UI and `logs/`).
- **Errors:** a part that can't go on stops with the reason and a non-zero exit (a missing input table, a snapshot not at its pinned SHA, a bad row, a setting the code doesn't support), so Airflow retries it (`[orchestration] retries`) and then alerts. Parts are idempotent, so a retry is safe.

## Bias

`meridian.bias` slices the pipeline's three deciding rules (owner rule, ticket mapping, resolver) by module, team, component, issue type, year and repo with Fairlearn's `MetricFrame`, flags slices more than 0.10 below overall (N ≥ 30), and scores mitigations. Findings, mitigation and trade-offs: [BIAS.md](BIAS.md).

## Pipeline flow

`history` and `scan → resolve` run side by side: they share nothing until `join` (`[orchestration] max_active_tasks`). Measured on the team run (Oct 23–24), each task on its own:

| Task | Time | | Task | Time |
|---|---:|---|---|---:|
| collect (snapshot exists) | ~10 s | | owners | 15 s |
| history | 430 s | | join | 40 s |
| scan | 140 s | | graph | 2 s |
| resolve | 295 s | | validate | 165 s |
| | | | bias | 35 s |

In sequence that is about 1,130 s. In the DAG the two branches overlap (history + owners 445 s, scan + resolve 435 s), so a run takes about 700 s: **−38%**. The bottleneck is `history`'s SZZ step; it already runs one process per CPU (`[history] szz_workers`). Airflow's Gantt view (DAG → a run → Gantt) shows the same split on every run.

## Tests

```
python -m unittest discover -s tests -t .      # from Data-Pipeline/
```

One test file per part, plus publish, compare, status and the Update graph trigger (`test_publish.py`), the DAGs' structure (`test_dag.py`; the Airflow half runs in the image: `docker compose run --rm airflow-scheduler python -m unittest tests.test_dag`), validation and alerts (`test_validate.py`) and bias (`test_bias.py`). Edge cases are fixtures: renames, splits, parse failures, missing values, schema violations, broken alert channels. Details and the team-run tests: [TESTING.md](../TESTING.md).
