# Handoff · graph data block v1 → stall rules, Jev, views

What step 1 hands to stage 5 (rules), 6 (Jev and the LLM agents) and 8 (views). One SQLite file per run, built by `meridian.writer` from `out/<run_id>/*.jsonl` against [schema.sql](../schema.sql). Team run `70dd1dabfed544b8`, snapshot 2026-10-01T01:23:03Z. Every query a stage needs is already written and tested in [KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md): start there, not from the tables.

## Read it like this

- **"Now" is `run.snapshot_at`.** Never the wall clock.
- **`call_site` is append-only.** It keeps every version; a site's current state is its newest version by `known_at` (the KB's `site_now` view). Removed sites stay, with `status = 'removed'` and the date it happened in `effective_date`.
- **Every table carries `run_id`.** Only `call_site` holds earlier runs' rows, each with the run that wrote it.
- **Nothing is dropped silently.** A miss is a row (`unowned`, `unmapped`, `ast_unresolved`, `parse_failure`) and a count in `coverage`. Show the coverage on every screen.
- **Teams are datasets' own rules.** Spark: the Jira component. Django: the path, synthetic. Packages: the repo. GitLab: the `group::` label.

## Every table

| Table | Rows on 70dd… | One row is | Next stages use it for |
|---|---:|---|---|
| `run` | 1 | the run: repo SHAs, source hashes, config hash, status | `snapshot_at` as "now"; `run_id`, coverage on every screen |
| `git_commit` | 139,228 | a commit: first-parent flag, author email, time | stall #4 orphaned owner, #8 competing load, ETA history |
| `commit_file` | 849,643 | a file a commit touched, with lines added and deleted | stall #8, P9 defect risk, push scope |
| `ticket` | 74,015 | a Jira or GitLab ticket as of `snapshot_at` (status, resolution) and as of the pull (text) | ticket checks, Manager rewrites, stall #3 mismatch |
| `ticket_event` | 235,252 | a status, resolution, label or link change | "open at time t", stale ticket check, outcome verdicts |
| `ticket_link` | 61,615 | a link between tickets, `removed_at` kept | "Jira links N of the hidden chains", stall verdicts (block removed) |
| `commit_ticket` | 42,191 | a ticket key named by a commit | ticket → code, P8 bad key |
| `bug_inducing` | 26,559 | SZZ: a commit that introduced a fixed bug | fragility scoring (kept for models) |
| `file_bug_fixes` | 1,492 | fix commits per file in 365 days, with their keys | risky (≥ 3), stall #6, P7. Spark only |
| `candidate` | 3,196 | a place a tracked old name appears: role, scope, junk, `in_except` | stall #5 (inside `except`), no LLM diff there |
| `parse_failure` | 9 | a file tree-sitter couldn't parse | coverage |
| `module_import` | 35,727 | an import statement, any module | stall #7 test gap, push scope (importers) |
| `deprecation` | 52 | a function or class that announces it's going away | P2 legacy use, legacy radar |
| `resolution` | 3,739 | what a candidate or alias call really is; `receiver_from` | blast radius depth 1; blocked_by |
| `call_edge` | 2,293 | a resolved call, caller → callee, up to depth 3 | blast radius; blocked_by depth > 1 |
| `person`, `person_alias` | 8,378 / 9,845 | merged identities and their emails | SQLite only, never in git or LLM facts |
| `team_map` | 3,101 | a person, path, repo or label → team | stall #8, "teams not connected" |
| `file_owner` | 43,870 | a file's owner team and share (< 0.30 → `unowned`) | stall #4, Maker drafts per team |
| `call_site` | 1,526 | a line someone must edit: owner, `bug_fixes`, status, `effective_date` | everything: burndown, ETA, stall, order, diffs, views |
| `ticket_map` | 246,428 | a ticket → a call site, a file, or `unmapped` | never notified, mismatch, duplicates, collisions, P3, P5 |
| `coverage` | 14 | a count of what we missed, with its definition | every screen |
| `blocked_by` | 0 | site A waits on site B in another team's function | stall #1 upstream, order of work, consumer views, P4 |
| `blast_radius` | 25 | per repo and symbol: sites, invocations by depth, files | API owner view, legacy radar |
| `chain` | 43 | ticket → ticket: `depends_on`, `duplicates`, `collides`, or a team's `untracked` sites | staff view, Maker (untracked), ticket checks |

## What's empty on this run, and why

| Empty | Because | Use instead |
|---|---|---|
| `blocked_by` | no repo has a `receiver_from`: `url()` and `ugettext()` are called directly | the KB fixture (FUL-7 waits on PLAT-2291) |
| `chain` except `untracked` | no ticket maps to a call site (Django and the packages have no tracker; Spark has no tracked symbols) | the KB fixture |
| `ticket_map` for GitLab | GitLab is Ruby: no code in the snapshot | the GitLab gold set, ticket level |
| `bug_fixes` outside Spark | SZZ runs on Spark only | — |
| tracking (`suggestion`, `outcome`, `push_alert`) | stage 6 hasn't offered anything | proposed DDL in the KB; move it into schema.sql with the first suggestion |

No single dataset fills every table (KB gap G7). Screens say which dataset a number comes from, and that Django teams are synthetic.

## Where each stage starts

- **Stall rules (5).** The KB's `stall_*` queries are reasons #1–#8 as written in 04-architecture; `owner_teams` gives moving / blocked / stalled; `order_of_work` is the topological order. Thresholds are in `[rules]`. Fix first: `order_of_work` never ends on a `blocked_by` cycle (no cycle guard).
- **Jev and the agents (6).** Facts for a ticket rewrite: `manager_facts`. For a diff: the `call_site` row, its `in_except`, its file's `bug_fixes` with keys. Every number, path, ID and team in the text must come from these rows (the gate, stage 7). Never people.
- **Views (8).** Staff, API owner, consumer and push views are the KB's `staff_*`, `owner_*`, `consumer_*`, `p*_` queries. `tests/dashboard.py` runs all of them on a real block and shows which have rows.

## Known limits

- `call_edge` holds only callers above tracked symbols (G3): no fan-in signals for the radar yet.
- Graph's code-level chains need a ticket on a call site; this run has none. Collisions were checked on Spark instead: `tests/collisions.py`, [labels/spark_collision_pairs_50.csv](../labels/spark_collision_pairs_50.csv).
- `call_edge` is 2,293 here and 2,288 in RESOLVER.md: 5 Django edges, cause open ([OPEN-POINTS](../../OPEN-POINTS.md)).
- 21,810 of 27,495 Spark files are `unowned`: 2,785 of 3,048 people have no keyed commit with a component in 180 days.
