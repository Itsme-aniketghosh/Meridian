# Contracts · step 1 · v1

Final, signed off by data engineering.

| File | What it fixes |
|---|---|
| [schema.sql](schema.sql) | Every table: columns, types, keys, allowed values |
| [meridian.toml](meridian.toml) | Datasets, pinned refs, team rules, every threshold. Hashed into the `run_id` |
| [settings.toml](settings.toml) | Everything that never changes a row: where, when and how fast it runs (Airflow, schedule, retries, timeouts, workers), where alerts go, the data checks, the bias slicing. Read through `common.load_settings`. Not in the `run_id`, so it can change any time |
| This page | The rules behind both, and what each part takes and delivers |

Changes to `schema.sql`, `meridian.toml` or this page need the contract owners' OK. If you're blocked, ship the version that works and log the change you need. Don't change the contract yourself.

## Rules for every table

1. **Every row carries `run_id`.** Nothing is shared across runs except through it.
2. **One clock.** `snapshot_at` is "now". No stage after Collect reads the wall clock.
3. **Times** are UTC text: `2026-10-01T01:23:03Z`.
4. **Paths** are relative to the repo root, use `/`, and keep git's case. `repo` is `owner/name`.
5. **IDs are content hashes:** the first 16 hex characters of sha256 over the fields joined by `\x1f`. Never row numbers or random IDs.
6. **Booleans** are `0`/`1`. **Lists** are JSON arrays, sorted.
7. **Enums are closed.** Only the values in the `CHECK` constraints. A value you need that isn't there is a contract change.
8. **Order:** insert rows in primary-key order. Tables are `WITHOUT ROWID`, so the same rows give the same bytes.
9. **Nothing is silently dropped.** A failure is a row: `parse_failure`, `detection = 'dropped'`, `owner_team = 'unowned'`, `target_kind = 'unmapped'`. The coverage report counts them.
10. **No personal data beyond what ownership needs.** Emails are used only to merge identities. Raw data stays out of git. What may be committed: [CSV-POLICY.md](CSV-POLICY.md).

## Snapshot

Collect writes it, every part reads it.

```
snapshots/<run_id>/
  manifest.json          run_id, snapshot_at, repos {repo: sha}, sources {name: sha256}, config_hash
  repos/<owner>__<name>/ checkout at the run's sha (full history, core.longpaths=true)
  jira/SPARK/page-*.json.gz          raw REST pages with changelog (the base pull)
  jira/SPARK/delta-<time>/           tickets changed since the pull before: cursor.json, page-*.json.gz, keys.json.gz
  gitlab/issues.jsonl.gz             raw issues, labels, links, activity (the base pull)
  gitlab/delta-<time>/               issues changed since the pull before: cursor.json, issues.jsonl.gz, iids.json.gz
snapshots/_cache/                    kept between runs, shared by all of them
  repos/<owner>__<name>.git          one bare mirror per repo, updated with git fetch (settings.toml [collect] mirrors)
  jira/SPARK/, gitlab/               the base pull and every delta, never overwritten
```

- `run_id` = the first 16 hex characters of sha256 over `manifest.json` without `run_id`, keys sorted. The same inputs always give the same run.
- A `ref` in `meridian.toml` that names a branch follows it: collect fetches the mirror and resolves the branch to a commit. A SHA (or `sha^1`) stays pinned.
- When any repo follows a branch, `snapshot_at` is the run's pull time, read by collect (`--snapshot-at` still wins). Otherwise it's the configured one.
- Tickets: a base pull, then deltas, each holding the tickets changed since the pull before, in full (whole changelog or activity notes). A run reads the base and the deltas up to the first pull that started after `snapshot_at`, and pulls a new delta if none did. A later copy of a ticket replaces the earlier one. The manifest's source hash covers exactly those files, cursors included.
- Deleted tickets: an `updated >=` query can't return them, so each delta also lists every key (Jira `keys.json.gz`) or iid (GitLab `iids.json.gz`) that exists now, and readers leave out a ticket missing from the newest listing. The deletion time isn't known, so a run whose `snapshot_at` is before it drops the ticket too.
- Each checkout borrows the mirror's objects (`git clone --shared`), and the mirror keeps `refs/runs/<run_id>` so they're never pruned. Don't move or delete the mirrors.
- Snapshots live in a local `snapshots/` folder (or GCS), never in git. DVC tracks the raw ticket pulls; git keeps only its pointer files.

## Handoff format

Each part writes each of its tables as `out/<run_id>/<table>.jsonl`: one JSON object per row, field names exactly as in `schema.sql`, sorted by primary key. `meridian.writer` loads them unchanged into one SQLite file.

## Every part runs as one command

The pipeline runs on a schedule in Airflow (in Docker today; GCP isn't set up yet). Every part starts the same way, so each one is a DAG task:

```
python -m meridian.<part> --run-id <run_id> --config meridian.toml
```

| Command | Reads | Writes |
|---|---|---|
| `meridian.collect` | the sources | `snapshots/<run_id>/`, `run` |
| `meridian.history` | snapshot | `git_commit` … `file_bug_fixes` |
| `meridian.scan` | snapshot | `candidate`, `parse_failure`, `module_import`, `deprecation` |
| `meridian.resolve` | snapshot, `candidate` | `resolution`, `call_edge` |
| `meridian.owners` | `git_commit`, `ticket`, `commit_ticket` | `person` … `file_owner` |
| `meridian.join` | all of the above | `call_site`, `ticket_map`, `coverage` |
| `meridian.graph` | the joined block | `blocked_by`, `blast_radius`, `chain` |
| `meridian.validate` | every table, `settings.toml` `[validate]` | no table: `meridian.sqlite`, `stats.json`, `anomalies.json` |
| `meridian.bias` | the joined block, `settings.toml` `[bias]` | no table: `bias.json` |
| `meridian.report` | `run`, `git_commit`, `call_site`, `call_edge`, `blast_radius` | no table: `changes.json` |
| `meridian.publish` | the validated `meridian.sqlite`, `anomalies.json` | no table: `published/<run_id>.sqlite`, `latest.json` |

join, validate, report and publish also take `--prev-run <run_id>`: the run to match against or compare with. It's an explicit argument, not a hidden input.

Rules, so a part can become an Airflow task without changes:
- **Same run, same output.** Running a part twice for one `run_id` replaces its own tables with the same rows. Airflow retries depend on this.
- **No clock, no hidden inputs.** Time comes from `run.snapshot_at`, never from Airflow's run date. Paths come from `meridian.toml` or the `MERIDIAN_DATA` variable (and `settings.toml` from `MERIDIAN_SETTINGS`), never hard-coded.
- **git only through `common.git`, jsonl only through `common.write_jsonl`.** `common.git` ignores the user's global and system git config, so every machine gives the same output. `write_jsonl` writes `\n` on every OS, sorted, atomically.
- **Exit code 0 means done, anything else means failed.** Log to stdout. Never prompt for input.
- **Write only your own tables.**
- **One container image** holds every part, with dependencies from one lockfile, Python 3.11.

## Who writes and who reads each table

| Table | Written by | Read by |
|---|---|---|
| `run` | collect | everyone |
| `git_commit`, `commit_file` | history | owners, join, graph |
| `ticket`, `ticket_event`, `ticket_link` | history | owners (Spark components), join, graph |
| `commit_ticket` | history | owners, join |
| `bug_inducing`, `file_bug_fixes` | history | join |
| `candidate`, `parse_failure`, `module_import`, `deprecation` | scan | resolve, join |
| `resolution`, `call_edge` | resolve | join, graph |
| `person`, `person_alias`, `team_map`, `file_owner` | owners | join, graph |
| `call_site`, `ticket_map`, `coverage` | join | graph |
| `blocked_by`, `blast_radius`, `chain` | graph | next stages |

---

## Collect and history

| Due | Tables | Check before handover |
|---|---|---|
| D2 | `snapshots/<run_id>/manifest.json`, `run` | Same inputs twice gives the same `run_id` |
| D3 | The writer: loads `out/<run_id>/*.jsonl` into one SQLite file using `schema.sql` | Load twice, then `cmp`: identical bytes |
| D3 | `git_commit`, `commit_file` for Spark, Django, the packages | Spark: 45,312 first-parent commits at `f868de6914` |
| D4 | `ticket`, `ticket_event`, `ticket_link`, `commit_ticket` | 59,491 tickets · 94.0% of the last 1,000 commits keyed · 3 dead keys |
| D6 | `bug_inducing`, `file_bug_fixes` | Scored on the 130 Defects4J labels · 9.7x risky-file lift reproduced |

Built from week 1's Jira pull (`week1/pair2/jira/pull.py`), its merged loader (`week1/pair2/merged/build.py`) and the GitLab pulls (`week1/cross-team/gitlab/`). `week1/pair3/defects4j/defects4j_bic_labels.csv` is the SZZ answer key.

Rules:
- Status, resolution and `resolved_at` are as of `snapshot_at`. Other fields are as of the pull. `run.notes` says so.
- A commit key must exist in `ticket`, otherwise there's no row.
- Bulk-closed = labelled `bulk-closed`, or 20+ resolutions by one person in one hour. Flag the ticket, don't delete it.
- SZZ counts fix commits from 2015 on only.
- Keep deleted GitLab "blocked by" links, with `removed_at`.

---

## Owners

Reads `meridian.toml` (`[teams]`, `[identity]`, `[ownership]`), `git_commit`, `ticket` and `commit_ticket`.

Team rules (final):

| Dataset | A team is | Owner of a file is |
|---|---|---|
| Spark | A person's most common Jira component across their keyed commits in the last 180 days. A ticket with *n* components gives each 1/*n*. Tie → their most recent commit's component. None → `unassigned` | The team with the largest share of the file's commits (180 days, no merges, no bots). Below 0.30 → `unowned`. Tie → most recent commit |
| Django | The path: `django/contrib/<app>/` → `contrib.<app>`, `django/<pkg>/` → `<pkg>`, `tests/` → `tests`. **Synthetic**, and the screen says so | The path rule. `basis = 'path'`. The window ends at the pinned commit's `authored_at`, not `snapshot_at` |
| Packages | The repo. Each repo is one team, so cross-repo means cross-team | The repo. `basis = 'repo'`. The window ends at the pinned commit's `authored_at`, not `snapshot_at` |
| GitLab | The issue's `group::` label | No code rows (Ruby). Teams label tickets for the gold set |

Why: Spark has no team field, but 98% of tickets carry a component, the closest thing to real teams. Summing share by team, rather than taking the top person, makes the weak person signal (20% vs 18%) matter less. Django and the packages have no teams at all, so a fixed path or repo rule is the honest, deterministic choice, and it still gives the graph different owners to test against.

| Due | Tables | Check before handover |
|---|---|---|
| D3 | `person`, `person_alias` | Spark: 3,381 emails → about 2,926 people. 0 merges on `generic_names`. Top 20 groups hand-checked |
| D4 | `team_map` (Spark people) | Every person has one team, `unassigned` counted |
| D5 | `team_map` (Django, packages), `file_owner` for all three | Beats last toucher on Spark (week 1: 20% vs 18%). Report the person-level number too |
| D6 | GitLab gold set: `labels/gitlab_cross_team_blocks.csv` and `labels/gitlab_stall_reasons.csv`, columns in [CSV-POLICY.md](CSV-POLICY.md) | ≥ 38 distinct issues · the random 50 model-drafted, person-reviewed: model waits hold 4 of 7 (bar not met) · `check_csv.py` passes |

Identity merging started from week 1's `merge_people()` (name + email local part, generic names skipped), plus `.mailmap` and the bot patterns. The owner-vs-last-toucher test is in the `week1/pair2/` README.

Rules:
- Use the author, never the committer: Spark's merge script makes the merger the committer.
- `person_id` = hash of the group's smallest email. It stays the same while that email stays the smallest: an added email that sorts first, or two groups merging, gives a new `person_id`.
- Co-authors stay in the data but don't count toward ownership.

---

## Scan and resolve

- **scan:** `candidate` is keyed by (repo, path, line, col). `role` and `junk` take only the listed values. Python outside `.py` uses `file_kind = 'embedded'`.
- **resolve:** `resolution` has one row per candidate, with the same key. The 542 alias calls the scanner can't see come in as new rows with `via = 'import_alias'` or `'assign_alias'`. `call_edge` holds resolved calls only, never imports.

## Changes from [03-data](../docs/03-data.md)

| 03-data | Now | Why |
|---|---|---|
| `call_site.file` | `path` | One name for paths everywhere |
| `call_site.blocked_by` column | `blocked_by` table | A site can be blocked by several sites. Keeps `call_site` append-only |
| `ticket_map.ticket_id`, `target`, `method` | `key`, `target_kind`, `target`, `method` | Unmapped tickets become rows |
| 4 tables | 25 tables | Every part's output is a table, so every number has a row |
| — | `coverage` | "Always report what we missed" |
| — | v1.1: `ticket.description`, `candidate.in_except` / `call_site.in_except`, `module_import`, `deprecation`, `resolution.receiver_from`; `blocked_by.depth` is `>= 1` | Planned features had nothing to read ([KNOWLEDGE-BASE.md](KNOWLEDGE-BASE.md)). Depth is `[graph] blocked_by_max_depth`, a setting |
| — | v1.2: `call_site.effective_sha` (the commit `effective_date` is from), `call_edge.introduced_sha` (blame of the caller's line), both 40 hex | Per-commit attribution and the per-run change report, `meridian.report` → `changes.json` ([UPDATE-PLAN.md](notes/UPDATE-PLAN.md) #5, #17) |

03-data's schema sketch predates these changes. Where they differ, `schema.sql` wins.
