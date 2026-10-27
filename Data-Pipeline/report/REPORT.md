# Meridian data pipeline

MLOps data pipeline submission. Team run `70dd1dabfed544b8`: Django, 20 Django packages, Spark with its Apache Jira tickets, and GitLab issues. 1,788,242 rows in 25 tables.

The sections follow the order of the assignment's guidelines. Appendices A to D hold the evidence.

## 1. Overview

Meridian helps a company retire an internal API. When a platform team removes something like `AuthClient.verify_token`, someone has to find every call to it, work out which team owns each one, and spot the calls that are risky or that block another team. Today that's done by hand, with Jira tickets that drift away from what the code says. Meridian does it from the code, git history and tickets ([docs/02-product.md](../../docs/02-product.md)).

This pipeline builds the data behind it: one SQLite file (the "block") that every view and check reads. It's two Airflow DAGs, a daily update and a weekly from-scratch rebuild that checks the update:

```
meridian_update (click, daily 02:00 UTC)
anything_new ── collect ──┬── history ───────── owners ──┐
                          │                              ├── join ── graph ── validate ── report ── publish
                          └── scan ──── resolve ─────────┘

meridian_rebuild (Sunday 04:00 UTC): fresh ── collect ── … ── validate ──┬── report ── compare ── publish (only if they differ)
                                                                        ├── bias (never gates)
                                                                        └── gx (never gates)
```

There's no trained model at this stage. The pipeline's "models" are three rules: who owns a file, which ticket maps to which code, and which name really is the retired API. We test and slice those rules the way you would a model.

## 2. Key components

### 2.1 Data acquisition

`meridian/collect.py` gets everything and freezes it. It clones 22 public repos and checks each one out at a pinned commit (`meridian.toml`). It pulls Spark's tickets from the Apache Jira REST API and GitLab's issues from the GitLab API, and keeps the raw responses. Everything it read goes into a manifest, and the run id is a hash of that manifest. Same inputs, same run id, on any machine.

Dependencies are pinned in `requirements.txt`, and the GX audit has its own `requirements-gx.txt`. The raw ticket pulls have real names in them, so they're not in git. DVC tracks them instead (`snapshots/_cache/*.dvc`, section 2.5). Someone without team access can pull them fresh from the public APIs with `python run_demo.py download --public-pull`. That gives a run of their own with different numbers.

### 2.2 Data preprocessing

Each step is its own module, run as `python -m meridian.<part> --run-id <id> --config meridian.toml`. All settings that change a row live in `meridian.toml`. [CONTRACTS.md](../CONTRACTS.md) lists what each part reads and writes.

| Step | What it does |
|---|---|
| `history` | Commits and changed files from git, tickets as they were at the snapshot, commit-to-ticket links from ticket keys, and SZZ (tracing each bug fix back to the commit that caused it) |
| `scan` | tree-sitter finds every place a tracked name shows up, with its role (import, call, alias) and the function it's in |
| `resolve` | Jedi checks what each name really refers to, so a look-alike `_()` or `url()` gets dropped |
| `owners` | Merges a person's several emails into one identity, gives people teams from Jira components, and gives each file an owner team |
| `join` | The features: call sites with owner, bug-fix count, status and a stable id, tickets mapped to code, and coverage |
| `graph` | Which call site waits on which, how far each call reaches (blast radius), and ticket chains |

Does it work? On Django's `ugettext*` the block finds all 265 lines in a hand-checked answer key, with 0 false hits. It resolves 542 of 542 aliased `_()` calls. The commit where Django actually migrated edits exactly the 267 lines we found. On Spark, 61% of 59,491 tickets map to code.

The weak spot is the cross-team part. None of our public datasets has both a ticket tracker and tracked code, so on real data 0 tickets map to a call site and the "who blocks whom" table is empty. A small hand-built repo (`acme/shop`) runs the whole cross-team story instead, and all 43 product queries pass on it. [Appendix C](appendix-real-data.md) goes feature by feature.

### 2.3 Test modules

292 unit tests (`unittest`), one test file per part, all passing. 14 are skipped on a plain machine because they need Airflow, GX or the team's private data. Those run in their own CI jobs or with the team data. Edge cases are built into the fixtures: renamed and split files, files that don't parse, missing values, schema violations, corrupt JSON, broken alert channels, a repo added to or dropped from the config.

```
python -m unittest discover -s tests -t .        # from Data-Pipeline/
```

CI runs this on Linux and Windows on every push, plus an Airflow job and a GX job. Details are in [Appendix B §1](appendix-quality.md#1-tests) and [TESTING.md](../../TESTING.md).

### 2.4 Pipeline orchestration (Airflow DAGs)

Both DAGs come from one spec, `dags/pipeline_spec.py`, and `test_dag` checks them against it. Each task is one of the commands above. The update starts with `anything_new`, which stops the run when nothing changed since the last publish. `publish` only runs after `validate` passes, so a bad run never replaces the last good block.

We ran both on the real data in Docker on a Mac. The update passed every task first try in 1,268 s. The rebuild then recomputed everything from scratch with caches off, and compare found 0 differences from the update. Screenshots and timings are in [Appendix A](appendix-airflow.md).

### 2.5 Data versioning with DVC

`dvc.yaml` has one stage per part, with the code, settings and tables each one depends on. `dvc.lock` is in git, so every commit pins the exact data its code produced. `dvc repro` reruns only the stages whose inputs changed. The raw Jira and GitLab pulls are pinned with `.dvc` files.

One gap: the remote is a placeholder GCS bucket, so `dvc pull` doesn't work for anyone yet. Until it's set up, the team shares the raw data as a checksummed zip.

### 2.6 Tracking and logging

Every part logs counts as it goes through Python's `logging`. Airflow keeps one log file per task try, in the UI and in `logs/`. `report` writes what changed since the last published run and which commit caused it. `status` prints how stale the data is, for example "data 2 days old · 14 new commits".

### 2.7 Data schema and statistics generation

`schema.sql` defines every table: columns, types, keys and allowed values. The writer refuses any row that breaks it. `validate` then computes statistics per column (rows, missing, distinct values, min, max, mean, common values) into `stats.json`, and the next run compares against them.

Every Sunday a Great Expectations suite re-checks the block on its own and publishes Data Docs. On 70dd it ran 308 expectations and none failed. It never blocks a run; `validate` is the gate. See [Appendix B §2 and §4](appendix-quality.md#2-schema-and-statistics).

### 2.8 Anomaly detection and alerts

`validate` sorts problems into two kinds, with thresholds in `settings.toml [validate]`:

- Errors fail the task and stop the publish: too few rows, bad formats (UTC times, ids, SHAs, paths, ticket keys), numbers out of range, coverage below its floor, schema violations.
- Warnings send an alert and the run goes on: too many missing values, impossible dates, outliers past the IQR fences, a row count that drifted from the last run, a column that collapsed to a single value.

On 70dd there are 0 errors and one warning: 289 Spark tickets dated in the years 0010 to 0012. Apache Jira really has those dates, so we report them and don't fix them.

`alert.py` sends one message to Slack or email for a failed task, a data error or a data warning. We haven't set up a real channel yet, so for now alerts go to the log. The sending code is tested with mocks. See [Appendix B §3](appendix-quality.md#3-anomalies).

### 2.9 Pipeline flow optimization

The Gantt chart from the real Airflow run showed where the time goes. The tasks add up to 1,599 s run one after another, and the DAG took 1,268 s, because `scan` and `resolve` run beside `history`. That's 21% saved.

`history` is the bottleneck: it walks all of Spark's git history for SZZ. It already runs one process per CPU, and a cache brings a warm run from 312 s down to 74 s. Most of what's left is Docker's folder mount on a Mac, which makes git about three times slower than native. A Docker volume or a Linux host fixes that. [Appendix A](appendix-airflow.md) has the chart.

## 3. Data bias detection using data slicing

### 3.1 Detecting bias

We don't have demographic data, and we don't want it. What we can check is whether the pipeline serves some teams worse than others. A team the rules get wrong gets sent the wrong work, or looks idle when it isn't. So we check three rules:

- the owner rule (does a file's owner team predict who edits it next?)
- the ticket mapping (does a ticket reach the code it's about?)
- the resolver (does it keep the real calls in every repo?)

### 3.2 Data slicing for bias analysis

`meridian/bias.py` slices each rule with Fairlearn's `MetricFrame`: by module, team, Jira component, ticket type, year filed and repo. The one people-level slice is how many commits the next author had made before. We don't slice by email domain or any other personal detail.

A slice counts when it has at least 30 rows. It gets flagged when its whole 95% Wilson interval (a confidence range for a rate) sits more than 0.10 below the overall value. 136 slices were judged and 32 flagged. The main findings:

- The owner rule is right 49.6% of the time where it can be scored. A quarter of next authors have no team at all (none of their commits in the last 180 days names a ticket with a Jira component), so the rule can't score them. That's a coverage gap, not a skew.
- Ticket mapping depends mostly on age. Tickets from before 2014 point at files that are gone by our pinned commit. Kubernetes and Spark Docker stay low even on recent tickets, because their code lives in other Apache repos we didn't snapshot.
- The resolver treats every repo the same: 98.1% kept overall, no repo flagged.

### 3.3 Mitigation of bias

We scored each fix on the same files, then checked the best one on held-out data.

- Lowering the owner threshold (min_share 0.30 to 0.20 or 0.10) changed nothing. Files are unowned because their authors have no team, not because shares sit near the line.
- Falling back to the last person who touched the file owns 65% more files but is wrong more often (0.395). That's coverage bought with accuracy, so we didn't take it.
- Taking a person's team from their last 730 days instead of 180 looked best: accuracy 0.496 to 0.545, and unassigned authors 24.7% down to 16.0%. But when we picked the variant on an earlier cut and checked it on the current one, it lost to the current rule (0.502 vs 0.505). So we didn't adopt it.

What we did ship: every slice in `bias.json` carries its size, interval and flag. The views can show "low coverage" next to a flagged team or module, so an unmapped team doesn't look idle. For ticket mapping the fix is more data, not fuzzy matching: add the two missing Apache repos in the next team run.

### 3.4 Documenting bias mitigation

[BIAS.md](../BIAS.md) has every slice, the mitigation table and the held-out check. [Appendix B §5](appendix-quality.md#5-bias-data-slicing) has the charts. The trade-offs, in short:

- The longer lookback gives more people a team, but a two-year-old team can be wrong for someone who moved.
- The flags aren't corrected for testing 136 slices at once. Under a Bonferroni check 26 of the 32 still hold.
- `tests/test_bias.py` pins the slicing and flag rules on a synthetic history.

## 4. Additional guidelines

### 4.1 Folder structure

```
Data-Pipeline/
├── dags/          the two Airflow DAGs and the spec they're built from
├── meridian/      the pipeline code, one module per part (the guidelines' scripts/)
├── tests/         unit tests, one file per part
├── logs/          Airflow task logs (git-ignored)
├── golden/        frozen hashes the reproducibility test checks
├── labels/        hand-checked answer keys
├── report/        this report, charts and the scripts that draw them
├── notes/         planning notes
├── dvc.yaml, dvc.lock, schema.sql, meridian.toml, settings.toml
├── Dockerfile, docker-compose.yaml, requirements.txt
└── README.md
```

The data (the guidelines' `data/`) sits at the repo root, in `snapshots/` for inputs, `out/` for outputs and `published/` for the live block. It's git-ignored and versioned by DVC, because it's several GB and has real names in it.

### 4.2 README documentation

[Data-Pipeline/README.md](../README.md) covers setting up the environment, running the pipeline with and without Airflow, what each folder and module does, and DVC. It also has a "Demo for TAs" section.

### 4.3 Reproducibility

The pipeline never reads the clock after `collect`. Ids are content hashes, output is sorted, and every setting that changes a row is hashed into the run id. So the same snapshot gives the same bytes:

- The 24 output files of 70dd match their frozen hashes (`tests/test_golden.py`).
- The weekly rebuild matched the update byte for byte.
- CI checks a small fixture's hashes on Linux and Windows on every push.

For a TA, `python run_demo.py short` runs in about 10 seconds with no private data and no Docker. It walks through the pipeline on the fixture, one guideline item at a time. `python run_demo.py long` runs everything, Airflow included. Both check free RAM and disk before heavy steps, and skip with a reason rather than crash.

### 4.4 Code style

One module per part, each with a docstring saying what it reads and writes. flake8 (PEP 8, with lines up to 120 characters, set in `setup.cfg`) is clean and runs in CI.

### 4.5 Error handling and logging

- A part that can't go on stops with the reason and a non-zero exit code: a missing input, a snapshot not at its pinned commit, a bad row, a corrupt file.
- Airflow times the task out, retries it twice five minutes apart, then sends an alert.
- Parts are idempotent, so a retry is safe. `publish` refuses a partial run and refuses to roll the live block back to an older run.
- A trigger's run id is checked before it reaches any shell command.

We tested all this by breaking things on purpose: 27 injected faults and 42 scenario tests. [Appendix D](appendix-testing.md) lists what broke and what we fixed.

## 5. Evaluation criteria

| # | Criterion | Where | Status |
|---|---|---|---|
| 1 | Proper documentation | §4.1, §4.2, [README](../README.md) | ✅ |
| 2 | Modular syntax and code | §2.2, §4.4 | ✅ |
| 3 | Pipeline orchestration (Airflow DAGs) | §2.4, [A](appendix-airflow.md) | ✅ |
| 4 | Tracking and logging | §2.6, §4.5 | ✅ (alerts logged; no real Slack or email channel yet) |
| 5 | Data version control (DVC) | §2.5 | ✅ (remote is a placeholder) |
| 6 | Pipeline flow optimization | §2.9, [A](appendix-airflow.md) | ✅ |
| 7 | Schema and statistics generation | §2.7, [B](appendix-quality.md) | ✅ |
| 8 | Anomaly detection and alert generation | §2.8, [B](appendix-quality.md) | ✅ |
| 9 | Bias detection and mitigation | §3, [BIAS.md](../BIAS.md) | ✅ |
| 10 | Test modules | §2.3, [B](appendix-quality.md) | ✅ |
| 11 | Reproducibility | §4.3, [D](appendix-testing.md) | ✅ |
| 12 | Error handling and logging | §4.5, [D](appendix-testing.md) | ✅ |

What still falls short:

- No real alert channel yet, and the DVC remote is a placeholder.
- The cross-team features only show their full value on the fixture, because no public dataset has both tickets and tracked code. With one run there's no burndown yet either.
- The push check's first rule (a new call to the retired API) was right 4 times out of 11 on real commits, under our 0.80 target. It has no resolver at push time, so look-alike calls get through.

## Appendices

- [A. Airflow run and Gantt](appendix-airflow.md): both DAGs in Docker on the real data, and where the time goes.
- [B. Data quality and bias](appendix-quality.md): tests, schema, statistics, anomalies, the GX audit, bias and mitigation.
- [C. The real data, feature by feature](appendix-real-data.md): what each proposed feature gets from the real block, with charts.
- [D. Testing the pipeline against itself](appendix-testing.md): the red team and the scenario tests, what broke and what's still open.
