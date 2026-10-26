# Meridian data pipeline report

Team run `70dd1dabfed544b8`: Django, 20 Django packages, Spark with Apache Jira, and GitLab issues. 1,788,242 rows in 25 tables.

## 1. What Meridian is

Meridian finds every call site of an internal API that is being retired, and says who owns each one, how risky it is and which teams wait on each other ([docs/02-product.md](../../docs/02-product.md)). Every view and push check reads one SQLite block, the knowledge base ([KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md)). This pipeline builds that block from code, git history and tickets, then checks, slices, versions and publishes it.

## 2. The pipeline

```
meridian_update (click, daily 02:00 UTC)
anything_new ── collect ──┬── history ───────── owners ──┐
                          │                              ├── join ── graph ── validate ── report ── publish
                          └── scan ──── resolve ─────────┘

meridian_rebuild (Sunday 04:00 UTC): fresh ── collect ── … ── validate ──┬── report ── compare ── publish (only if they differ)
                                                                        ├── bias (never gates)
                                                                        └── gx (never gates)
```

`collect` pins 22 repos at fixed SHAs and pulls Jira and GitLab raw. `history`, `scan`, `resolve` and `owners` preprocess, `join` and `graph` build the features, and `validate`, `gx` and `bias` check the block. [CONTRACTS.md](../CONTRACTS.md) lists each part's inputs and outputs.

## 3. Against the evaluation criteria

Rows follow the guidelines' §5; row 9 also covers their §3 on bias.

| # | Criterion | How we meet it | Evidence | Status |
|---|---|---|---|---|
| 1 | Documentation | README: setup, run, code structure, DVC, reproducibility, and where the guidelines' `data/` and `scripts/` live. Every module's docstring says what it reads and writes, and why | [README](../README.md), [CONTRACTS.md](../CONTRACTS.md), [TESTING.md](../../TESTING.md) | ✅ |
| 2 | Modular code | One idempotent CLI module per part (`python -m meridian.<part>`), reading only its declared inputs. flake8 (PEP 8, 120 columns) clean in CI | [CONTRACTS.md](../CONTRACTS.md) | ✅ |
| 3 | Airflow DAGs | Two DAGs built from `dags/pipeline_spec.py` and checked against it by `test_dag`. `meridian_update` ran end to end in Docker in 1,268 s, every task first try; `meridian_rebuild` matched it with 0 differences | [A](appendix-airflow.md) | ✅ |
| 4 | Tracking and logging | Python logging in every part, one Airflow log per try in `logs/`, a what-changed report per run. `alert.py` logs every alert and sends it to Slack or email once a channel is set | [A](appendix-airflow.md), [README](../README.md) | ✅ (no real channel yet) |
| 5 | DVC | `dvc.yaml` has one stage per part, `dvc.lock` is in git, and `.dvc` files pin the raw Jira and GitLab pulls. `publish` runs `dvc commit` and `dvc push` when `settings.toml [dvc] push = true` | [README](../README.md) "Data versioning" | ✅ (remote is a placeholder) |
| 6 | Flow optimisation | The tasks add up to 1,599 s and the DAG took 1,268 s: running scan and resolve beside history saved 21%. history is the bottleneck; SZZ runs one process per CPU, and its cache takes a warm history from 312 s to 74 s | [A](appendix-airflow.md) | ✅ |
| 7 | Schema and statistics | The writer enforces `schema.sql`; `stats.json` per column is compared run over run. Weekly Great Expectations: 308 expectations, 0 failed beyond the 289 known bad Jira dates | [B §2, §4](appendix-quality.md#2-schema-and-statistics) | ✅ |
| 8 | Anomalies and alerts | Errors fail the task (row minimums, formats, ranges, coverage floors, schema violations). Warnings alert and the run goes on (missing values, bad dates, outliers, drift, constant columns) | [B §3](appendix-quality.md#3-anomalies) | ✅ |
| 9 | Bias and mitigation | No model is trained here, so we slice the three rules that decide what teams see (owner rule, ticket mapping, resolver) by module, team, component, ticket type, year and author experience. Fairlearn with Wilson intervals: 136 slices judged, 32 flagged; mitigations scored on a held-out cut | [B §5](appendix-quality.md#5-bias-data-slicing), [BIAS.md](../BIAS.md) | ✅ |
| 10 | Test modules | 287 unit tests, one file per part, edge cases as fixtures (renames, parse failures, missing values, schema violations, broken alerts). All pass; the 14 skipped pass in their own Airflow, GX and team-data environments | [B §1](appendix-quality.md#1-tests) | ✅ |
| 11 | Reproducibility | No clock after collect, content-hash IDs, row-changing settings hashed into the `run_id`. Golden hashes 24 of 24; the rebuild equals the update. CI on Linux and Windows; `run_demo.py short` needs no team data | [D](appendix-testing.md), [CONTRACTS.md](../CONTRACTS.md) | ✅ |
| 12 | Error handling | A part that can't go on exits non-zero with the reason. Airflow retries (2 × 5 min), times out, then alerts. publish waits for validate, so the last good block stays live. Tested with 27 injected faults and 42 scenarios | [D](appendix-testing.md) | ✅ |

## 4. What the data shows

- Finding the work is exact. On Django `ugettext*` the block finds 265 of 265 hand-checked edit lines with 0 false hits, plus 2 in `code.sample`, and resolves 542 of 542 aliased `_()` calls.
- It matches the real fixes. The conversion commit `c651331b34` edits all 267 of the block's lines, and the push check flags the only 4 new calls it adds. The 20 packages' `url()` migration commits edit 1,160 of the block's 1,236 lines.
- 60.7% of 59,491 Spark Jira tickets map to code: 33,351 by commit key, 2,524 more by stack trace, 263 more by path (36,138 in all).
- SZZ, which traces each bug fix back to the commit that caused it, marks 180 of 1,492 Spark files as fragile (3 or more bug fixes). P9 flags 32% of bug-inducing commits and 9% of the rest.
- Cross-team features are built but quiet on real data. No source has both a tracker and tracked code (Django has no tracker, Spark no tracked symbol, GitLab is Ruby), so 0 tickets map to a call site and 0 call sites carry bug fixes. `blocked_by` is empty because the tracked APIs are called with no receiver.
- The `acme/shop` fixture carries the whole story: all 43 knowledge-base queries and P1–P9 pass on it (18 of 18 tests). That was the plan from the start (KNOWLEDGE-BASE.md G7).
- Of 74 proposed features, 25 ✅ work now and 35 🟡 are quiet or partial on real data; 6 ❌ aren't built and 8 ⛔ belong to later stages.
- 0 schema violations and 0 data errors on 1,788,242 rows. The one warning: 289 Spark tickets dated 0010–0012, as Apache Jira has them.

## 5. Known limits

- The DVC remote (GCS) is a placeholder until GCP is set up.
- No alert has reached a real channel, as Slack and email aren't configured; alerts are tested with mocks.
- Docker's folder mount on a Mac or Windows makes history about 3× slower than native; a Docker volume or a Linux host fixes it.
- With one run there is no burndown or ETA yet. No source has cross-repo chains, so chains and P4 are tested on fixtures only, and only 5.8% of the newest 500 Spark tickets link to another.
- P1's precision on the 21-commit replay is 0.36, under the 0.80 bar: the push check has no resolver at push time.
- Bias flags aren't corrected for multiple comparisons; under Bonferroni 26 of the 32 hold. A 730-day team lookback scored the same as 180 days held out (0.502 vs 0.505), so it was not adopted.

## Appendices

- [A · Airflow run and Gantt](appendix-airflow.md): both DAGs in Docker on the real data, and where the time goes.
- [B · Data quality and bias](appendix-quality.md): tests, schema, statistics, anomalies, the GX audit, bias and mitigation.
- [C · The real data, feature by feature](appendix-real-data.md): what each proposed feature gets from the real block, with the evidence.
- [D · Testing the pipeline against itself](appendix-testing.md): the red team and 42 scenario tests, what broke and what is still open.
