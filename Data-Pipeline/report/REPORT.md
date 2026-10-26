# Meridian data pipeline report

Team run `70dd1dabfed544b8`: Django, 20 Django packages, Spark with Apache Jira, and GitLab issues. 1,788,242 rows in 25 tables, 0 schema violations, 0 data errors.

This report follows the course's submission guidelines section by section: the key components (§2 there, [§3](#3-key-components) here), bias detection (§3, [§4](#4-bias-detection-and-mitigation)), the additional guidelines (§4, [§5](#5-additional-guidelines)) and the evaluation criteria (§5, [§6](#6-evaluation-criteria-checklist)).

## 1. What Meridian is

Meridian finds every call site of an internal API that is being retired, and says who owns each one, how risky it is and which teams wait on each other ([docs/02-product.md](../../docs/02-product.md)). Every view and push check reads one SQLite block, the knowledge base ([KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md)). This pipeline builds that block from code, git history and tickets, then checks, slices, versions and publishes it.

No model is trained in this step. The pipeline's "models" are three rules that decide what each team sees: the owner rule, the ticket-to-code mapping and the resolver. The tests, quality checks and bias slicing apply to them.

## 2. The pipeline at a glance

```
meridian_update (click, daily 02:00 UTC)
anything_new ── collect ──┬── history ───────── owners ──┐
                          │                              ├── join ── graph ── validate ── report ── publish
                          └── scan ──── resolve ─────────┘

meridian_rebuild (Sunday 04:00 UTC): fresh ── collect ── … ── validate ──┬── report ── compare ── publish (only if they differ)
                                                                        ├── bias (never gates)
                                                                        └── gx (never gates)
```

| Stage | Parts | What they produce |
|---|---|---|
| Acquisition | `collect` | 22 repos at pinned SHAs, raw Jira and GitLab pulls, one `run_id` |
| Preprocessing | `history`, `scan`, `resolve`, `owners` | commits, tickets and links, bug-inducing commits (SZZ), call-site candidates, resolutions, identities and teams |
| Features | `join`, `graph` | call sites with owner, bug fixes and stable IDs; ticket-to-code map; blast radius, blocks, chains |
| Quality | `validate`, `gx`, `bias` | the block `meridian.sqlite`, `stats.json`, `anomalies.json`, Data Docs, `bias.json` |
| Output | `report`, `publish`, `compare` | what changed, the live block and `latest.json`, rebuild vs update |

[CONTRACTS.md](../CONTRACTS.md) lists each part's inputs and outputs.

## 3. Key components

### 3.1 Data acquisition

`meridian/collect.py` clones the 22 repos and checks each one out at the SHA pinned in `meridian.toml` (Django at the parent of its `ugettext` conversion commit, each package at the parent of its `url()` migration). It pulls Spark's Apache Jira and GitLab issues as raw JSON, cut at the snapshot time. The `run_id` is a hash of the code SHAs, the raw pulls and `meridian.toml`, so the same inputs give the same `run_id` on every machine. The raw pulls are pinned with DVC; the clones are rebuilt byte for byte from their SHAs. Every dependency is pinned in `requirements.txt`, and network retries and timeouts live in `settings.toml`.

### 3.2 Preprocessing and feature building

One module per part, each a command: `python -m meridian.<part> --run-id <id> --config meridian.toml`. Each reads only its declared inputs and writes one sorted `.jsonl` per table, so it can be rerun alone.

- `history`: commits and changed files, Jira and GitLab tickets with their events and links, ticket keys found in commit messages, and SZZ (each bug fix traced back to the commit that caused it).
- `scan`: tree-sitter finds every line that names a tracked symbol: imports, calls, aliases.
- `resolve`: Jedi confirms each candidate really is the tracked symbol, follows aliases such as `_ = ugettext`, and drops look-alikes.
- `owners`: merges identities (mailmap, name and email rules, bots removed) and maps people to teams, one rule per dataset.
- `join`, `graph`: call sites with an owner team, bug-fix count and a content-hash ID that survives moves and renames; tickets mapped to code by commit key, stack trace or path; blast radius, blocks and chains.

Every value that changes a row (pinned refs, team rules, thresholds) lives in `meridian.toml` and is hashed into the `run_id`. Nothing is hard-coded.

### 3.3 Test modules

293 unit tests (`unittest`), one file per part plus cross-part ones: the DAG against its spec, validation, bias, push checks, cache equivalence and the golden hashes. Edge cases are fixtures: renames, file splits, parse failures, missing values, empty and corrupt tables, schema violations, broken alert channels. All pass. The 14 skipped need Airflow, Great Expectations or the team data, and pass in those environments. With the team data, the answer keys pass: 265 of 265 Django edit lines, 542 of 542 aliased calls, and the packages' precision and recall. [Appendix D](appendix-testing.md) says what else we test and how.

### 3.4 Orchestration (Airflow DAGs)

Two DAGs in `dags/meridian_dag.py`, built from `dags/pipeline_spec.py` and checked against it (`test_dag`). `meridian_update` covers the whole workflow, from fetching data to publishing the block; `anything_new` stops it in about 2 s when no repo or tracker moved. `meridian_rebuild` recomputes the published run from scratch every week and compares it with the update. In Docker on a Mac, on the real data, the update ran in 1,268 s with every task on its first try, and the rebuild matched it with **0 differences** ([Appendix A](appendix-airflow.md)).

### 3.5 Data versioning (DVC)

`dvc.yaml` declares each part as a stage, with the code, settings and tables it depends on. `dvc.lock` is in git and holds the hash of every output, so each version of the data is tied to a commit of the code. `snapshots/_cache/{jira,gitlab}.dvc` pin the raw pulls. With `[dvc] push = true`, `publish` runs `dvc commit` and `dvc push`. The team's remote is a GCS bucket, still a placeholder (§7).

### 3.6 Tracking and logging

Every part logs its counts through Python's logging. Airflow keeps one log per task try (UI and `logs/`), and `report.py` writes what changed in each run, commit by commit. `alert.py` sends a failed task, a data error or a data warning to Slack and/or email as `settings.toml [alerts]` says, and logs every alert. No real channel is configured yet, so alerts so far were logged only.

### 3.7 Schema and statistics

`meridian.validate` loads the block with the writer against `schema.sql`: types, NOT NULL, allowed values, keys, one `run_id`. Any violation fails the task. It then writes per-column statistics (rows, missing, distinct, min, max, mean, top values) to `stats.json`, which the next run compares against, so quality is checked over time. A Great Expectations suite repeats the generic checks weekly in its own environment, with Data Docs: 308 expectations, 0 failed beyond the 289 known bad Jira dates ([Appendix B §2, §4](appendix-quality.md#2-schema-and-statistics)).

### 3.8 Anomaly detection and alerts

Errors fail the task and alert: too few rows, bad formats (UTC times, 16-hex IDs, 40-hex SHAs, paths), out-of-range numbers, coverage below its floor, schema violations. Warnings alert and the run goes on: missing values above a limit, implausible dates, IQR outliers, run-over-run drift, a column collapsing to one value. On 70dd: 0 errors and one warning, the 289 Spark tickets dated in the years 0010–0012, as Apache Jira has them ([Appendix B §3](appendix-quality.md#3-anomalies)).

### 3.9 Pipeline flow optimisation

From the Airflow Gantt chart: the tasks add up to 1,599 s one after another, and the DAG took 1,268 s. Running `scan → resolve` beside `history` saved 331 s (21%). `history` is the bottleneck, and every other branch finishes inside it. Its SZZ step already runs one process per CPU, and its cache takes a warm `history` from 312 s to 74 s. Most of the 1,268 s is Docker's folder mount on a Mac: the same parts take about 7 minutes natively ([Appendix A](appendix-airflow.md)).

## 4. Bias detection and mitigation

### 4.1 Detecting bias

We slice the three deciding rules by the groups they serve, since there are no demographic features in code and tickets. Personal attributes are left out for privacy; the only people-level slice is commit count.

| Rule | Metric | Slices |
|---|---|---|
| Owner rule (Spark) | does the owner team predict the next author's team; share of files owned | module, the next author's team, the next author's prior commits |
| Ticket-to-code mapping (Spark Jira) | share of tickets with a code target | component, issue type, year filed, component since 2020 |
| Resolver (Django, packages) | share of candidates kept as call sites | repo |

### 4.2 Slicing with Fairlearn

`meridian.bias` uses Fairlearn's `MetricFrame`. A slice is judged when it has at least 30 rows, and flagged when its whole Wilson 95% interval sits more than 0.10 below the overall value, so a small slice can't be flagged by noise. Result: **136 slices judged, 32 flagged**. Under a Bonferroni correction for testing 136 slices, 26 of the 32 still hold.

- **Owner rule:** 0.496 accuracy on next authors with a team. `core/` is flagged at 0.228, and so is the Project Infra team (0.000 on N = 30). A quarter of next authors (24.7%) have no team at all, and `examples/`, `mllib/` and `common/` are mostly unowned.
- **Ticket mapping:** 0.608 overall. It depends mostly on age, since tickets filed before 2014 point at files that are gone, and partly on where the code lives: Kubernetes, Spark Docker and EC2 code sits in repos outside the snapshot.
- **Resolver:** 0.981 kept, and 0 of 12 judged repos flagged. It treats every repo alike.

### 4.3 Mitigation

Each candidate fix for the owner rule is scored on the same files, then checked on held-out data. The best variant is picked at a cut one window earlier and scored on the current cut, with a paired bootstrap interval.

| Variant | Accuracy, next authors with a team | Next author unassigned | Result |
|---|---:|---:|---|
| As configured: teams from 180 days | 0.496 | 24.7% | kept |
| Lower `min_share` (threshold adjustment) | 0.493 | 24.6% | no effect: the unowned files have authors with no team |
| Fall back to the last toucher's team | 0.395 | 23.2% | more files owned, less accurate: rejected |
| Teams from 730 days | 0.545 | 16.0% | held out it scores the same as 180 days (0.502 vs 0.505, delta 0.0): not adopted |

What is applied: every slice in `bias.json` carries its N, interval and flag, so a view shows "low coverage" next to a flagged module or component, and "too few to judge" under N = 30. An unowned module is then not read as nobody's work, and an unmapped component is not read as idle. For ticket mapping no fuzzy matching is allowed, so the fix is coverage: add the Kubernetes and Docker repos to the next snapshot.

### 4.4 Trade-offs

Falling back to the last toucher trades accuracy for coverage. The 730-day lookback gives more people a team (16.0% unassigned vs 24.7%), but its accuracy gain doesn't hold out of sample, and a team from two years ago can be stale. Both are on record in `bias.json` on every run. The full process is in [BIAS.md](../BIAS.md), with charts in [Appendix B §5](appendix-quality.md#5-bias-data-slicing).

## 5. Additional guidelines

- **Folder structure.** `Data-Pipeline/` holds `dags/`, `meridian/` (the parts), `tests/`, `golden/`, `labels/`, `logs/`, `dvc.yaml`, `schema.sql` and the README. The guidelines' `data/` is the repo root's `snapshots/` (inputs) and `out/` (outputs, versioned with DVC); their `scripts/` is `meridian/`.
- **README.** [README.md](../README.md) covers environment setup, how to run the pipeline (Docker and Airflow, or each part by hand), the code structure, and data versioning with DVC.
- **Reproducibility.** Nothing reads the clock after collect, IDs are content hashes, and output is sorted. The golden check matches all 24 output files, and the weekly rebuild equals the update. CI runs the tests on Linux and Windows. `python run_demo.py short` runs the whole pipeline on a built-in fixture in seconds, with no team data and no Docker.
- **Code style.** Modular parts with shared helpers in `meridian/common.py`; flake8 (PEP 8, 120 columns) is clean and runs in CI.
- **Error handling and logging.** A part that can't go on stops with the reason and a non-zero exit: a missing input, a snapshot not at its pinned SHA, a corrupt file, an unsupported setting. Airflow retries twice, 5 minutes apart, with a timeout per task, then alerts. publish runs only after validate passes and refuses a rollback, so a failed run leaves the last good block live.

## 6. Evaluation criteria checklist

| # | Criterion | Where | Status |
|---|---|---|---|
| 1 | Documentation | §5, [README](../README.md), [CONTRACTS.md](../CONTRACTS.md), module docstrings | ✅ |
| 2 | Modular code | §3.2, §5 | ✅ |
| 3 | Airflow DAGs | §3.4, [Appendix A](appendix-airflow.md) | ✅ |
| 4 | Tracking and logging | §3.6 | ✅ (no real alert channel yet) |
| 5 | DVC | §3.5 | ✅ (remote is a placeholder) |
| 6 | Flow optimisation | §3.9, [Appendix A](appendix-airflow.md) | ✅ |
| 7 | Schema and statistics | §3.7, [Appendix B](appendix-quality.md) | ✅ |
| 8 | Anomalies and alerts | §3.8, [Appendix B](appendix-quality.md) | ✅ |
| 9 | Bias detection and mitigation | §4, [BIAS.md](../BIAS.md) | ✅ |
| 10 | Test modules | §3.3, [Appendix D](appendix-testing.md) | ✅ |
| 11 | Reproducibility | §5, [Appendix D](appendix-testing.md) | ✅ |
| 12 | Error handling and logging | §5, [Appendix D](appendix-testing.md) | ✅ |

## 7. What the data shows, and known limits

What the block gets right on real data ([Appendix C](appendix-real-data.md)):
- **Finding the work is exact.** On Django `ugettext*`, 265 of 265 hand-checked edit lines with 0 false hits, and 542 of 542 aliased calls. The real conversion commit edits all 267 of the block's lines.
- **The push check catches real new uses.** Since P1 runs the resolver at push time, it fires only on true new calls: 4 of 4 on the 21-commit replay (was 4 of 11), and 2 of 2 on 9 held-out Django commits.
- **Spark tickets carry signal.** 60.7% of 59,491 tickets map to code. The risk check P9 flags 32% of bug-inducing commits and 9% of the rest.

Known limits:
- **Cross-team features are proven on a fixture only.** No source has both a ticket tracker and tracked code (Django has no tracker, Spark no tracked symbol, GitLab is Ruby), so ticket-to-call-site links, blocks and chains are quiet on real data. The resolver also doesn't follow method calls, so `blocked_by` is empty. All 43 knowledge-base queries and P1–P9 pass on the `acme/shop` fixture.
- **Two weak signals were tried and not adopted**, each against a bar written down before it ran ([notes/PASS-BARS-2026-10-26.md](../notes/PASS-BARS-2026-10-26.md)): a "bug without a reproduction" check doesn't predict Spark's vague bugs, and timing evidence doesn't lift the GitLab cross-team rule.
- **Operations:** the DVC remote is a placeholder until GCP is set up, and no alert has reached a real channel.

## Appendices

- [A · Airflow run and Gantt](appendix-airflow.md): both DAGs in Docker on the real data, and where the time goes.
- [B · Data quality and bias](appendix-quality.md): tests, schema, statistics, anomalies, the GX audit, bias and mitigation.
- [C · The real data, feature by feature](appendix-real-data.md): what each proposed feature gets from the real block.
- [D · What we test and how](appendix-testing.md): the test levels, the scenario and fault tests, and what is still open.
