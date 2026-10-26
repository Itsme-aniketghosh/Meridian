# Join · step 1

Utkarsh's part of the [pipeline plan](notes/PIPELINE-PLAN.md): one block of call sites with owner, bug fixes and status, tickets mapped to code, and a count of everything we missed. Team run 70dd1dabfed544b8, 2026-10-23.

```
python -m meridian.join --run-id <run_id> --config meridian.toml                       # first run
python -m meridian.join --run-id <run_id> --config meridian.toml --prev-run <run_id>   # match to the last run
```

| Reads | Writes |
|---|---|
| `out/<run_id>/`: `candidate`, `resolution`, `parse_failure`, `file_owner`, `file_bug_fixes`, `ticket`, `commit_ticket`, `commit_file`, `git_commit`, `run`. The snapshot checkouts. With `--prev-run`: that run's `call_site`, `candidate`, `run` | `out/<run_id>/call_site.jsonl`, `ticket_map.jsonl`, `coverage.jsonl` |

## Verdict

| Task | Bar | Got | |
|---|---|---|---|
| Call-site IDs + matching to the last run | fixtures 100% | insert, move, rename, duplicate, split, removal, new, a file that stops parsing, a removed ID coming back, a repo added to or dropped from the config (a dropped repo with present sites stops the run): **22 of 22** tests in `tests/test_join.py` | ✅ |
| Django call sites | the 265 | **265 of 265** lines of `labels/django_edit_sites_265.csv`, plus the 2 in `code.sample`, nothing else | ✅ |
| One joined block | every row has an owner or `unowned`, and a status | **1,526** call sites, all `present`, all owned (Django by path, packages by repo) | ✅ |
| Ticket → code, no fuzzy matching | path and trace precision ≥ 0.90 | spot check of 6 tickets with frames and 5 path hits: all right. **Hand check still to do** | ⏳ |
| Django replay error ≤ 1% | | not done: "dropped first if we're behind" | ⬜ |
| Same run, same bytes | 0 diffs | `shasum` identical on a second run | ✅ |
| Loads with the writer | | `meridian.writer` loads the whole block against `schema.sql`; a second run's block with the first run's versions loads too (fixture) | ✅ |

Run time about 20 s, peak 2.4 GB (reading the ticket and commit tables).

## On 70dd1dabfed544b8

| | |
|---|---|
| `call_site` | 1,526: 1,163 calls, 362 imports, 1 alias. Django 289 (267 lines), packages 1,237. 1 `ast_unresolved` (django-debug-toolbar `panels/staticfiles.py:34`), kept |
| `ticket_map` | 246,428 rows: 184,713 `commit_key`, 22,812 `stack_trace`, 1,026 `path`, 37,877 `unmapped` |
| Tickets mapped | Spark 36,138 of 59,491 (61%). GitLab 0 of 14,524: Ruby, no code in the snapshot |
| Not call sites | 30 look-alikes the resolver dropped, 1,640 definitions, attributes and references (still in `candidate`) |
| Gaps counted | 9 parse failures of 6,557 files · 58,191 of 256,185 keyed-commit file links point at files gone by the pinned SHA · 134 of 82,833 stack frames name more than one file · 21,810 Spark files unowned |

## How it decides

- **A call site** is a candidate with a role in `[scanner] edit_roles` (import, call, alias) that the resolver didn't drop. `ast_unresolved` stays. Alias calls like `_("...")` are blast radius, not call sites. `reference` rows (the 315 keyword-argument names) stay in `candidate` only.
- **call_site_id** = h(repo, symbol, path, scope, normalized line, occurrence). Normalized = all whitespace removed. occurrence = 0, 1, … among sites with the same other five, in line order.
- **Matching to the last run**, in `[join] match_order`: git's line map from the last SHA to this one, renames followed; else the same symbol, scope and normalized line anywhere in the repo (a moved line, a split file). Unmatched last-run sites get a `removed` version, unless their file is a parse failure this run (they stay present); unmatched new ones a new ID. A removed site's ID can come back, to one site only.
- **Append-only.** A version is written only when a field changes. `call_site.jsonl` holds every version so far, so "now" is each site's newest.
- **effective_date:** present = blame of the line at this SHA (`--first-parent -w`). Removed = the first-parent commit after the last one that still had the line (reverse blame). The date is that commit's `authored_at`; **effective_sha** is the commit itself (`meridian.report` groups changes by it).
- **owner_team** from `file_owner`, `unowned` if the file has no row. **bug_fixes** from `file_bug_fixes`, 0 if none (Django and the packages have none: SZZ is Spark only).
- **ticket_map**, all hits kept: `commit_key` (every file a keyed commit touched that still exists), `path` (a token that is exactly a file path), `stack_trace` (a Java/Scala or Python frame that names exactly one file; on a call site's line it also maps to the site), `symbol` (`module.name` of a tracked symbol → its call sites). No hit → `unmapped`.
- **coverage:** 14 counts, each with a note. The note is the definition.

## Flags

- **Writer change (made, Aniket to OK, Vishwa to know).** From the second run on, `call_site.jsonl` carries the earlier runs' versions (append-only, as `KNOWLEDGE-BASE.md` reads it). `meridian.writer` now allows earlier run_ids in `call_site` only, and only versions known by this run's `snapshot_at`. Every other table still takes one run_id. Tests: `test_history` `test_call_site_carries_earlier_runs_versions`, `test_join` `test_second_runs_block_loads_with_the_first_runs_versions`.
- **`--prev-run` is a new argument** to the one command. It's explicit, so no hidden input. CONTRACTS.md's command table needs a line for it.
- **SQLite version.** This machine has 3.53.1; `meridian.toml` pins 3.50.4. The block loads, but the byte-identical check holds only on the pinned version: the image must have it.
- **Removed effective_date** uses `git_commit.seq`, so a site removed by a commit that came in through a merge is dated by the merge, the first-parent commit where it became true.

## Next

1. Hand-check a sample of `path` and `stack_trace` rows for the 0.90 bar.
2. A second real run with `--prev-run`, once there's a later snapshot (the nightly DAG).
3. Hand to Aniket: the graph reads `call_site`, `call_edge`, `ticket_map`.

## Rerun

```
python -m meridian.join --run-id <run_id> --config meridian.toml
python -m unittest tests.test_join -v
```
