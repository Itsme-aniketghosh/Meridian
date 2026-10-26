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

## 3. Requirement checklist (course guidelines)

| # | Requirement | Where it is | Status |
|---|---|---|---|
| 1 | Data acquisition, reproducible, deps pinned | `meridian/collect.py`; pinned `requirements.txt`; repos rebuilt byte for byte from the SHAs in `meridian.toml`; same inputs give the same `run_id` | ✅ |
| 2 | Preprocessing, modular | One module per part, each a CLI `python -m meridian.<part> --run-id … --config meridian.toml`; contracts in [CONTRACTS.md](../CONTRACTS.md) | ✅ |
| 3 | Unit tests, edge cases | `tests/`, one file per part; renames, splits, parse failures, missing values, schema violations and broken alert channels are fixtures. Results: [Appendix B](appendix-quality.md) | ✅ |
| 4 | Airflow DAGs | Two DAGs in `dags/meridian_dag.py`, built from `dags/pipeline_spec.py`; `test_dag.py` checks both against the spec. Run: [Appendix A](appendix-airflow.md) | ✅ |
| 5 | DVC | `dvc.yaml` (one stage per part), `dvc.lock` in git, `snapshots/_cache/{jira,gitlab}.dvc` pin the raw pulls; `publish` runs `dvc commit` + `dvc push` when `settings.toml` `[dvc] push = true` (false for now) | ✅ (remote still a placeholder, see §6) |
| 6 | Tracking and logging | Every part logs counts to stdout; Airflow keeps per-task logs (`logs/`); `report.py` writes what changed per run | ✅ |
| 7 | Schema and statistics | `schema.sql` (types, keys, allowed values) enforced by the writer; `stats.json` per table and column, compared run over run; weekly GX suite with Data Docs | ✅ |
| 8 | Anomaly detection and alerts | `validate.py`: errors fail the task (row minimums, formats, ranges, coverage floors), warnings alert and the run goes on (missing values, implausible dates, IQR outliers, drift); `alert.py` logs every alert and can send Slack and/or email, but neither is configured yet, so alerts so far were only logged | ✅ |
| 9 | Flow optimisation (Gantt) | history ∥ scan→resolve: ~1,130 s in sequence → ~700 s in the DAG, an estimated −38% from per-task timings until the Airflow Gantt confirms it; SZZ uses one process per CPU. Gantt: [Appendix A](appendix-airflow.md) | ✅ |
| 10 | Bias via slicing, mitigation, docs | `bias.py` with Fairlearn `MetricFrame`; findings and scored mitigations in [BIAS.md](../BIAS.md); charts in [Appendix B](appendix-quality.md) | ✅ |
| 11 | Folder structure, README | `Data-Pipeline/{dags,meridian,tests,golden,labels,logs}`, `dvc.yaml`, [README.md](../README.md) (setup, run, structure, DVC) | ✅ |
| 12 | Reproducibility | Golden hashes (`golden/`); the weekly rebuild must equal the update byte for byte; CI on Linux and Windows | ✅ (see §6 for the Windows resolver gap) |
| 13 | Error handling | A part that can't go on stops with the reason and a non-zero exit; Airflow retries (2 × 5 min), then alerts; parts are idempotent; publish only after validate passes, so a failed run leaves the last good block live | ✅ |

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
- 285 unit tests pass on Windows (14 skipped by design: team data, Airflow, GX).
- With the team data, the Django and packages answer keys pass: 6 of 6.
- The v1.2 golden check matches 20 of 24 files. 3 (`resolution`, `call_edge`, `blast_radius`) are likely the resolver's Windows gap (unverified), and `call_site` likely the join fix made after the freeze. A fresh Linux run in Airflow will settle it.
- 0 schema violations and 0 data errors on 1,788,242 rows.

| Appendix | What |
|---|---|
| [A](appendix-airflow.md) | Airflow run and Gantt |
| [B](appendix-quality.md) | Tests, schema, statistics, anomalies, bias with mitigation |
| [C](appendix-scenarios.md) | Every product situation on the real block, with verdicts and charts |
| [D](appendix-features.md) | Feature checklist: status, tables, evidence, reason |
| [E](appendix-redteam.md) | Red team: what we attacked, found and fixed |

## 6. Known limits

- The DVC remote (GCS) is a placeholder until GCP is set up. Use a local remote until then (README, "Data versioning").
- On Windows, 3 of 24 golden files (`resolution`, `call_edge`, `blast_radius`) differ from the Mac values. This is likely the resolver's Windows path gap ([UPDATE-PLAN.md](../notes/UPDATE-PLAN.md)), not yet verified; a fresh Linux run in Airflow will settle it.
- None of the four sources has cross-repo chains, so chains and P4 are tested on fixtures only ([docs/02-product.md](../../docs/02-product.md), "Known gaps").
- Spark tickets rarely link to each other (5.8% of the newest 500 carry a link), so P3, P5 and chains go quiet on Spark. Commits are well keyed: 41,138 of 50,371 Spark commits (82%) name a ticket.
- The owner-rule mitigation (team lookback of 730 days) is scored, not applied. It needs a new team `run_id` ([BIAS.md](../BIAS.md)).

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
