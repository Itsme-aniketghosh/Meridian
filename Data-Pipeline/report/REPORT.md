# Meridian · Data pipeline report

Team run `70dd1dabfed544b8` · Django, 20 Django packages, Spark + Apache Jira, GitLab issues · 1,788,242 rows in 25 tables.

## 1. What the pipeline is for

Meridian finds every call site of an internal API that is being retired, and says who owns it, how risky it is, and which teams wait on each other ([docs/01-problem.md](../../docs/01-problem.md), [docs/02-product.md](../../docs/02-product.md)). Every view and every push check reads one SQLite block, the knowledge base ([KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md)). This pipeline builds that block from raw code, git history and tickets, then validates, slices, versions and publishes it.

There is no trained model in this step. The pipeline's "models" are three rules: the owner rule, the ticket-to-code mapping and the resolver. The bias checks, tests and quality checks apply to those rules.

## 2. Pipeline at a glance

```
meridian_update (click, daily 02:00 UTC)
anything_new ── collect ──┬── history ───────── owners ──┐
                          │                              ├── join ── graph ── validate ── report ── publish
                          └── scan ──── resolve ─────────┘

meridian_rebuild (Sunday 04:00 UTC): fresh ── collect ── … ── validate ── bias ── compare ── publish (only if they differ)
                                                                    └── gx (beside bias, never gates)
```

| Stage | Module | Output tables |
|---|---|---|
| Acquisition | `meridian/collect.py`: 22 repos at pinned SHAs, raw Jira and GitLab pulls, one `run_id` | `run` |
| Preprocessing | `history.py` (commits, tickets, SZZ), `scan.py` (tree-sitter), `resolve.py` (Jedi), `owners.py` (identities, teams) | 18 tables |
| Feature building | `join.py` (call sites with owner, bug fixes, status, stable IDs), `graph.py` (blocked_by, blast radius, chains) | `call_site`, `ticket_map`, `coverage`, `blocked_by`, `blast_radius`, `chain` |
| Quality | `validate.py` (schema, stats, anomalies), `gx.py` (Great Expectations, weekly), `bias.py` (Fairlearn) | `stats.json`, `anomalies.json`, `bias.json` |
| Output | `report.py` (what changed), `publish.py` (block + DVC), `compare.py` (rebuild vs update) | `published/` |

## 3. Against the submission guidelines

The guidelines grade 12 criteria (their §5). Each row says where the evidence is.

| # | Criterion | Where it is | Status |
|---|---|---|---|
| 1 | Documentation | [README.md](../README.md) (setup, run, structure, DVC, reproducibility), [CONTRACTS.md](../CONTRACTS.md) (every part's inputs and outputs), [TESTING.md](../../TESTING.md); every module opens with a docstring saying what it reads, writes and why. Folders: `Data-Pipeline/{dags,meridian,tests,golden,labels,logs,report}`, `dvc.yaml`; the README maps the guidelines' `data/` and `scripts/` to ours | ✅ |
| 2 | Modular code | One module per part, each a CLI `python -m meridian.<part> --run-id … --config meridian.toml`, idempotent, reading only its declared inputs; shared helpers in `meridian/common.py`. flake8 (PEP 8, 120 columns) runs clean in CI | ✅ |
| 3 | Airflow DAGs | Two DAGs in `dags/meridian_dag.py`, built from `dags/pipeline_spec.py` and checked against it (`test_dag`). `meridian_update` ran end to end in Docker, every task first try, 1,268 s; the weekly `meridian_rebuild` too, with 0 differences from the update. [Appendix A](appendix-airflow.md) | ✅ |
| 4 | Tracking and logging | Every part logs its counts through Python's logging, which Airflow keeps as one log per try (`logs/`); `report.py` writes what changed per run; `alert.py` logs every alert and sends to Slack and/or email once a channel is set | ✅ (no real channel yet, §6) |
| 5 | DVC | `dvc.yaml` (one stage per part), `dvc.lock` in git, `snapshots/_cache/{jira,gitlab}.dvc` pin the raw pulls; `publish` runs `dvc commit` + `dvc push` when `settings.toml` `[dvc] push = true` | ✅ (remote a placeholder, §6) |
| 6 | Flow optimisation (Gantt) | Measured on the Airflow Gantt: the tasks add up to 1,599 s one after another, the DAG took 1,268 s, 21% saved by running scan → resolve beside history. history is the bottleneck: SZZ uses one process per CPU, and its cache takes a warm run from 312 s to 74 s. [Appendix A](appendix-airflow.md) | ✅ |
| 7 | Schema and statistics | `schema.sql` (types, keys, allowed values) enforced by the writer; `stats.json` per table and column, compared run over run (drift); a Great Expectations suite with Data Docs in the weekly rebuild: 308 expectations, 0 failed beyond the 289 known bad Jira dates. [Appendix B](appendix-quality.md) | ✅ |
| 8 | Anomalies and alerts | `validate.py`: errors fail the task (row minimums, formats, ranges, coverage floors, schema violations); warnings alert and the run goes on (missing values, implausible dates, IQR outliers, drift, constant columns). [Appendix B](appendix-quality.md) | ✅ |
| 9 | Bias via slicing, mitigation | `bias.py` with Fairlearn `MetricFrame`, Wilson intervals, 136 slices judged and 32 flagged; mitigations scored on a held-out cut; [BIAS.md](../BIAS.md), charts in [Appendix B](appendix-quality.md) | ✅ |
| 10 | Test modules | 287 unit tests, one file per part, edge cases as fixtures (renames, splits, parse failures, missing values, schema violations, broken alert channels); all pass, plus the Airflow, GX and team-data halves. [Appendix B](appendix-quality.md) | ✅ |
| 11 | Reproducibility | Same inputs give the same `run_id` and the same bytes: golden hashes 24 of 24; the weekly rebuild equals the update (0 differences, in Airflow); CI on Linux and Windows; Docker Compose for Airflow. [Appendix F](appendix-robustness.md) | ✅ |
| 12 | Error handling | A part that can't go on stops with the reason and a non-zero exit; Airflow retries (2 × 5 min) and timeouts, then alerts; publish only after validate passes, so a failed run leaves the last good block live; 27 injected faults ([Appendix E](appendix-redteam.md)) and 42 pipeline scenarios ([Appendix F](appendix-robustness.md)) | ✅ |

The key components of the guidelines' §2 map onto the same parts: acquisition is `collect.py` (pinned SHAs, raw pulls, pinned `requirements.txt`), preprocessing is `history`, `scan`, `resolve` and `owners`, and feature building is `join` and `graph` (§2 above). The guidelines ask about demographic slices; our rules work on code and tickets, so the slices are module, team, component, ticket type, year and experience (BIAS.md says why).

## 4. Design decisions

Determinism comes first. Nothing reads the wall clock after collect, IDs are content hashes, output is sorted, and thresholds are hashed into the `run_id`. So the weekly full rebuild is a real test: it must equal the incremental update. If they differ, compare alerts and the rebuild is published as the truth.

`validate` is the gate before publish. The Great Expectations (GX) suite repeats its generic checks weekly in its own environment and never blocks a run, so a GX upgrade can't break the pipeline.

Settings live in three files. `meridian.toml` holds anything that changes a row and is hashed into the `run_id`. `settings.toml` holds how the pipeline runs and can never change a row. `.env` holds the secrets. No secret is in git.

There is no fuzzy matching. Ticket-to-code links come only from keys, paths, stack frames and exact symbols, so every reported link can be checked.

## 5. Does the pipeline carry the signal we need?

Finding the work: yes, exactly.
- On Django `ugettext*`, the block finds 265 of 265 hand-checked edit lines with 0 false hits, plus the 2 in `code.sample`.
- It resolves 542 of 542 aliased `_()` calls.
- The real conversion commit `c651331b34` edits all 267 of the block's lines, and the push check flags the only 4 new calls it adds.
- On the 20 Django packages, the 20 `url()` migration commits edit 1,160 of the block's 1,236 lines.

Spark tickets and history: yes.
- 60.7% of 59,491 Jira tickets map to code: 33,351 by commit key, plus 2,524 more by stack trace and 263 more by path in the text (36,138 in all).
- SZZ (it traces each bug fix back to the commit that caused it) marks 180 of 1,492 files as fragile (≥ 3 bug fixes).
- P9 flags 32% of bug-inducing commits vs 9% of the rest.

Cross-team features: built, but quiet on our real data.
- No source has both a ticket tracker and tracked code. Django has no tracker, Spark has no tracked symbol, and GitLab is Ruby. So on the real block, 0 tickets map to a call site and 0 call sites carry bug fixes.
- `blocked_by` is empty: the tracked APIs are always called directly, with no receiver.
- With one run there is no burndown or ETA yet.
- The `acme/shop` fixture carries the whole story end to end: all 43 KNOWLEDGE-BASE queries and all P1–P9 push checks pass on it (18 of 18 tests). This was the team's plan from the start (KNOWLEDGE-BASE.md G7, 02-product "Known gaps").

Of 74 proposed features, 24 ✅ work now, 36 🟡 have their tables and queries but are quiet on real data or only partly covered, 6 ❌ are not built, and 8 ⛔ belong to later stages (LLM, gate, auditor).

Tests:
- 287 unit tests pass on macOS (14 skipped by design: they run in their own Airflow, GX and team-data environments, and pass there).
- With the team data, the Django and packages answer keys pass: 6 of 6.
- The golden check matches 24 of 24 files on fresh outputs of the Airflow run.
- 0 schema violations and 0 data errors on 1,788,242 rows.

| Appendix | What |
|---|---|
| [A](appendix-airflow.md) | Airflow run and Gantt |
| [B](appendix-quality.md) | Tests, schema, statistics, anomalies, bias with mitigation |
| [C](appendix-scenarios.md) | Every product situation on the real block, with verdicts and charts |
| [D](appendix-features.md) | Feature checklist: status, tables, evidence, reason |
| [E](appendix-redteam.md) | Red team: what we attacked, found and fixed |
| [F](appendix-robustness.md) | Scenario tests: 42 situations the pipeline must survive, and how it did |

## 6. Known limits

- The DVC remote (GCS) is a placeholder until GCP is set up. Use a local remote until then (README, "Data versioning").
- No alert has gone to a real channel: Slack and email aren't configured. The alert path is tested with mocks and logs every alert.
- Airflow in Docker on a Mac or Windows is slow: the folder mount makes history ~3× slower than native (Appendix A). `docker-compose.volumes.yaml` or a Linux host fixes it.
- None of the four sources has cross-repo chains, so chains and P4 are tested on fixtures only ([docs/02-product.md](../../docs/02-product.md), "Known gaps"). `blocked_by` is empty on real data for the same reason.
- Spark tickets rarely link to each other (5.8% of the newest 500 carry a link), so P3, P5 and chains go quiet on Spark. Commits are well keyed: 41,138 of 50,371 Spark commits (82%) name a ticket.
- P1's precision on the 21-commit replay is 0.36: the push check has no resolver at push time.
- Bias flags aren't corrected for multiple comparisons; under Bonferroni 26 of 32 hold (BIAS.md).
- The owner-rule mitigation (team lookback of 730 days) was scored and is **not adopted**: on the held-out cut it scores the same as 180 days (0.502 vs 0.505). The rule stays at 180 days ([BIAS.md](../BIAS.md)).

Fixed while writing this report:
- `ticket_open`, `stall_ticket_mismatch` and graph.py's collides rule now compare done statuses case-insensitively. By the status rule alone, GitLab open issues go 14,524 → 3,680. The fixed `ticket_open` view also requires a mapping to a call site, so on 70dd it returns 0 GitLab issues.
- `site_now` and the queries that bypassed it are scoped to `asof.repo`/`asof.symbol` (NULL = all): django/django + `ugettext` gives 93 sites, not 1,526 from 25 repos.
- `derive_blast_radius` double-counted functions reachable at two depths and hard-coded the symbol; graph.py was right. Now 209/241/135, matching graph's table.
- `stall_competing_load` only considers teams that own call sites: 8 false Spark flags → 0.
- BIAS.md now mentions Project Infra (0.000, N = 30), and TESTING.md's counts are updated.

## Appendices

- [A · Airflow run and Gantt](appendix-airflow.md)
- [B · Tests, data quality, bias](appendix-quality.md)
- [C · Situations on real data](appendix-scenarios.md)
- [D · Feature checklist and reasons](appendix-features.md)
- [E · Red team](appendix-redteam.md)
- [F · Scenario tests: does it hold up?](appendix-robustness.md)
