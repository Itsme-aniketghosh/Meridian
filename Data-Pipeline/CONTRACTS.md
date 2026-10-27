# Contracts · step 1 · v1

**Final.** Signed off by Utkarsh (data engineering). This closes Stage 0 of the pipeline plan, so everyone can start on Day 2.

| File | What it fixes |
|---|---|
| [schema.sql](schema.sql) | Every table: columns, types, keys, allowed values |
| [meridian.toml](meridian.toml) | Datasets, pinned refs, team rules, every threshold. Hashed into the `run_id` |
| [settings.toml](settings.toml) | Everything that never changes a row: where, when and how fast it runs (Airflow, GCP, schedule, retries, timeouts, workers), where alerts go, the data checks and the bias slicing. Was infra.toml. Parts read their sections through `common.load_settings`. It never changes a row, so it's not in the `run_id` and can change any time |
| This page | The rules behind both, and what Vishwa and Chaitali get and deliver |

To change `schema.sql`, `meridian.toml` or this page, ask Utkarsh and Aniket. If you're blocked, ship the version that works and log the change you need. Don't change the contract yourself.

## Rules for every table

1. **Every row carries `run_id`.** Nothing is shared across runs except through it.
2. **One clock.** `snapshot_at` is "now". No stage after Collect reads the wall clock.
3. **Times** are UTC text: `2026-10-01T01:23:03Z`.
4. **Paths** are relative to the repo root, use `/`, and are case as in git. `repo` is `owner/name`.
5. **IDs are content hashes:** the first 16 hex characters of sha256 over the fields joined by `\x1f`. Never use row numbers or random IDs.
6. **Booleans** are `0`/`1`. **Lists** are JSON arrays, sorted.
7. **Enums are closed.** Only the values in the `CHECK` constraints. A value you need that isn't there is a contract change.
8. **Order:** insert rows in primary-key order. Tables are `WITHOUT ROWID`, so the same rows give the same bytes.
9. **Nothing is silently dropped.** A failure is a row: `parse_failure`, `detection = 'dropped'`, `owner_team = 'unowned'`, `target_kind = 'unmapped'`. The coverage report counts them.
10. **No personal data beyond what ownership needs.** Emails are used only to merge identities. Raw data stays out of git. What may be committed: [CSV-POLICY.md](CSV-POLICY.md).

## Snapshot · Vishwa writes it, everyone reads it

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

- `run_id` = the first 16 hex characters of sha256 over `manifest.json` without `run_id`, with keys sorted. The same inputs always give the same run.
- A `ref` in `meridian.toml` that names a branch follows it: collect fetches the mirror and resolves the branch to a commit. A SHA (or `sha^1`) stays pinned.
- When any repo follows a branch, `snapshot_at` is the run's pull time, read by collect (`--snapshot-at` still wins). Otherwise it's the configured one.
- Tickets: a base pull, then deltas, each the tickets changed since the pull before, each in full (whole changelog or activity notes). A run reads the base and the deltas up to the first pull that started after `snapshot_at`, and pulls a new delta if none did. Readers take the base, then each delta in order: a later copy of a ticket replaces the earlier one. The manifest's source hash covers exactly those files, cursors included.
- Deleted tickets: an `updated >=` query can't return them, so each delta also lists every key (Jira `keys.json.gz`) or iid (GitLab `iids.json.gz`) that exists now. Readers leave out a ticket missing from the newest listing. When it was deleted isn't known, so a run whose snapshot_at is before the deletion drops it too.
- Each checkout borrows the mirror's objects (`git clone --shared`), and the mirror keeps `refs/runs/<run_id>` so they're never pruned. Don't move or delete the mirrors.
- Snapshots live in GCS or a local `snapshots/` folder, never in git.

## Before the writer lands (Days 2–3)

Write each table as `out/<run_id>/<table>.jsonl`:
- one JSON object per row
- field names exactly as in `schema.sql`
- sorted by primary key

The writer loads these unchanged, so nothing you write early is wasted. Use `run_id = "dev"` until Vishwa's manifest exists.

## Every part runs as one command (for Airflow)

The pipeline runs on a schedule in **Airflow on GCP**. Utkarsh builds the DAG from your parts once they work, so every part must be startable the same way:

```
python -m meridian.<part> --run-id <run_id> --config meridian.toml
```

| Part | Command | Reads | Writes |
|---|---|---|---|
| Vishwa | `meridian.collect` | the sources | `snapshots/<run_id>/`, `run` |
| Vishwa | `meridian.history` | snapshot | `git_commit` … `file_bug_fixes` |
| Rachna | `meridian.scan` | snapshot | `candidate`, `parse_failure`, `module_import`, `deprecation` |
| Rochan | `meridian.resolve` | snapshot, `candidate` | `resolution`, `call_edge` |
| Chaitali | `meridian.owners` | `git_commit`, `ticket`, `commit_ticket` | `person` … `file_owner` |
| Utkarsh | `meridian.join` | all of the above | `call_site`, `ticket_map`, `coverage` |
| Aniket | `meridian.graph` | the joined block | `blocked_by`, `blast_radius`, `chain` |
| Aniket | `meridian.validate` | every table, `settings.toml` `[validate]` | no table: `meridian.sqlite`, `stats.json`, `anomalies.json` |
| Aniket | `meridian.bias` | the joined block, `settings.toml` `[bias]` | no table: `bias.json` |

**Rules, so a part can become an Airflow task without changes:**
- **Same run, same output.** Running a part twice for one `run_id` replaces its own tables with the same rows. Airflow retries depend on this.
- **No clock, no hidden inputs.** Time comes from `run.snapshot_at`, never from Airflow's run date. Paths come from `meridian.toml` or the `MERIDIAN_DATA` variable (and `settings.toml` from `MERIDIAN_SETTINGS`), never hard-coded.
- **git only through `common.git`, jsonl only through `common.write_jsonl`.** `common.git` ignores the user's global and system git config, so every machine gives the same output. `write_jsonl` writes `\n` on every OS, sorted, atomically.
- **Exit code 0 means done, anything else means failed.** Log to stdout. Never prompt for input.
- **Write only your own tables.**
- **One container image** holds every part, with dependencies from one lockfile, Python 3.11.

## Who writes and who reads each table

| Table | Written by | Read by |
|---|---|---|
| `run` | Vishwa | everyone |
| `git_commit`, `commit_file` | Vishwa | Chaitali, Utkarsh, Aniket |
| `ticket`, `ticket_event`, `ticket_link` | Vishwa | Chaitali (Spark components), Utkarsh, Aniket |
| `commit_ticket` | Vishwa | Chaitali, Utkarsh |
| `bug_inducing`, `file_bug_fixes` | Vishwa | Utkarsh |
| `candidate`, `parse_failure`, `module_import`, `deprecation` | Rachna | Rochan, Utkarsh |
| `resolution`, `call_edge` | Rochan | Utkarsh, Aniket |
| `person`, `person_alias`, `team_map`, `file_owner` | Chaitali | Utkarsh, Aniket |
| `call_site`, `ticket_map`, `coverage` | Utkarsh | Aniket |
| `blocked_by`, `blast_radius`, `chain` | Aniket | next stages |

---

## Vishwa · start kit

**You get:** `schema.sql`, `meridian.toml`, and the Jira pull already done in week 1.

**You deliver, in this order:**

| Due | Tables | Check before you hand over |
|---|---|---|
| D2 | `snapshots/<run_id>/manifest.json`, `run` | Same inputs twice gives the same `run_id` |
| D3 | The writer: loads `out/<run_id>/*.jsonl` into one SQLite file using `schema.sql` | Load twice, then `cmp`: identical bytes |
| D3 | `git_commit`, `commit_file` for Spark, Django, the packages | Spark: 45,312 first-parent commits at `f868de6914` |
| D4 | `ticket`, `ticket_event`, `ticket_link`, `commit_ticket` | 59,491 tickets · 94.0% of the last 1,000 commits keyed · 3 dead keys |
| D6 | `bug_inducing`, `file_bug_fixes` | Scored on the 130 Defects4J labels · 9.7x risky-file lift reproduced |

**Start from week 1. Most of this already exists:**
- `week1/pair2/jira/pull.py` is the Jira pull. It's resumable, makes raw pages, and has no rate limits.
- `week1/pair2/merged/build.py` already does most of the Collect work:
  - `read_commits()` → `git_commit` + `commit_file`
  - the ticket loader → `ticket`, with the parent's type and the bulk-closed flag
  - `ticket_history` → `ticket_event`
  - its `commit_ticket` → yours
  - its rollback to `SNAPSHOT_AT` already matches the contract

  Rename the columns to the schema, add `run_id`, and drop `people`/`people_emails`, which are now Chaitali's.
- `week1/cross-team/gitlab/xt_pull_issues.py` and `xt_pull_notes.py` → the raw GitLab files. Keep deleted "blocked by" links (`removed_at`).
- `week1/pair3/defects4j/defects4j_bic_labels.csv` is the SZZ answer key.

**Rules that matter most:**
- Status, resolution and `resolved_at` are as of `snapshot_at`. Other fields are as of the pull. Say so in `run.notes`.
- A commit key must exist in `ticket`, otherwise there's no row.
- Bulk-closed = labelled `bulk-closed`, or 20+ resolutions by one person in one hour. Flag the ticket, don't delete it.
- SZZ counts fix commits from 2015 on only.

---

## Chaitali · start kit

**You get:** `schema.sql`, `meridian.toml` (`[teams]`, `[identity]`, `[ownership]`), and from Vishwa `git_commit` (D3) plus `ticket` and `commit_ticket` (D4). Until then, use `week1/pair2/merged/data/spark_jira.sqlite`.

**Team rules (final):**

| Dataset | A team is | Owner of a file is |
|---|---|---|
| Spark | A person's most common Jira component across their keyed commits in the last 180 days. A ticket with *n* components gives each 1/*n*. Tie → their most recent commit's component. None → `unassigned` | The team with the largest share of the file's commits (180 days, no merges, no bots). Below 0.30 → `unowned`. Tie → most recent commit |
| Django | The path: `django/contrib/<app>/` → `contrib.<app>`, `django/<pkg>/` → `<pkg>`, `tests/` → `tests`. **Synthetic**, and the screen says so | The path rule. `basis = 'path'`. The window ends at the pinned commit's `authored_at`, not `snapshot_at`. |
| Packages | The repo. Each repo is one team, so cross-repo means cross-team | The repo. `basis = 'repo'`. The window ends at the pinned commit's `authored_at`, not `snapshot_at`. |
| GitLab | The issue's `group::` label | No code rows (Ruby). Teams label tickets for the gold set |

**Why these:**
- Spark has no team field, but 98% of tickets carry a component, so components are the closest real teams.
- Summing share by team, not just counting the top person, makes the weak person signal (20% vs 18%) matter less.
- Django and the packages have no teams at all. A fixed path or repo rule is honest and deterministic, and it gives the graph different owners to test against.

**You deliver:**

| Due | Tables | Check before you hand over |
|---|---|---|
| D3 | `person`, `person_alias` | Spark: 3,381 emails → about 2,926 people. 0 merges on `generic_names`. Top 20 groups hand-checked |
| D4 | `team_map` (Spark people) | Every person has one team, `unassigned` counted |
| D5 | `team_map` (Django, packages), `file_owner` for all three | Beats last toucher on Spark (week 1: 20% vs 18%). Report the person-level number too |
| D6 | GitLab gold set: `labels/gitlab_cross_team_blocks.csv` and `labels/gitlab_stall_reasons.csv`, columns in [CSV-POLICY.md](CSV-POLICY.md) | ≥ 38 distinct issues · ≥ 9 of 10 waits hold · `check_csv.py` passes |

**Start from week 1:**
- `merge_people()` in `week1/pair2/merged/build.py` already merges on name + email local part and skips generic names. Move it over, and add `.mailmap` and the bot patterns.
- `week1/pair2/` README has the owner-vs-last-toucher test.
- `week1/cross-team/gitlab/xt_cross_team_blocks.csv` and Vishwa's 30 are the two lists to merge for the gold set. They're local only today; scrub them into `labels/` per [CSV-POLICY.md](CSV-POLICY.md).

**Rules that matter most:**
- Use the author, never the committer: Spark's merge script makes the merger the committer.
- `person_id` = hash of the group's smallest email. It stays the same while that email stays the smallest: an added email that sorts first, or two groups merging, gives a new `person_id`.
- Co-authors stay in the data but don't count toward ownership.

---

## Rachna and Rochan · what's fixed for you

- **Rachna:** `candidate` is keyed by (repo, path, line, col). `role` and `junk` take only the listed values. Python outside `.py` uses `file_kind = 'embedded'`.
- **Rochan:** `resolution` has one row per candidate, with the same key. The 542 alias calls the scanner can't see come in as new rows with `via = 'import_alias'` or `'assign_alias'`. `call_edge` holds resolved calls only, never imports.

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

03-data gets updated to match once v1 is frozen (Day 10).
