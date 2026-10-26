# Appendix · Feature coverage of the data pipeline

Can the block as it stands serve each feature the team proposed ([02-product](../../docs/02-product.md), [04-architecture](../../docs/04-architecture.md), [project-scoping](../../scoping/project-scoping.md))? Dropped ideas ([06-dead-ideas](../../docs/06-dead-ideas.md): stall probabilities, trained stall/ETA model, incident fragility, blocking merges, LLM on every push, filing tickets or PRs) are left out.

| Status | Meaning |
|---|---|
| ✅ supported now | Columns in [schema.sql](../schema.sql), query proven in CI on the fixture, and the real block gives a meaningful answer (or the code path is built and tested) |
| 🟡 partial | Tables and query exist and pass on the fixture, but the real block gives them an empty input, or only half the feature is covered |
| ❌ not yet | The data the feature needs isn't in the block |
| ⛔ out of scope | A later stage (6 LLM, 7 gate, 9 auditor) does the work; the pipeline only supplies facts |

Query names are the named SQL in [KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md), proven on its fixture by `tests/test_kb.py`. "70dd" counts come from running each query read-only on the real team block `70dd1dabfed544b8` (snapshot 2026-10-01, status `complete`, 25 tables), with the parameters `tests/dashboard.py` picks. Tests rerun for this appendix (`test_kb`, `test_pushcheck`, `test_graph`, `test_join`, `test_report`): 56 of 56 OK.

## Checklist

### Staff engineer

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| S1 · Call sites remaining | ✅ | `call_site` (newest version by `known_at`, `status`) | `staff_headline`; 70dd: 1,526 present | |
| S2 · Open tickets on the API | 🟡 | `ticket_map` (`target_kind = 'call_site'`), `ticket.status` | `staff_headline`; 70dd: 0 | No real ticket maps to a call site: Django and packages have no tracker, Spark has no tracked symbol, GitLab is Ruby ([HANDOFF.md](../notes/HANDOFF.md) "What's empty", KB G7) |
| S3 · Hidden chains, "Jira links N of them" | 🟡 | `chain` (`depends_on`), `blocked_by`, `ticket_map`, `ticket_link` | `staff_chains`, `derive_depends_on`; `test_graph` 13 of 13; 70dd: 0 `depends_on` (43 chain rows, all `untracked`) | Needs `blocked_by` (0 on 70dd) and S2's mapping. Cross-repo chains on fixtures only ([02-product](../../docs/02-product.md) Known gaps) |
| S4 · Collisions | 🟡 | `chain` (`collides`), `ticket_map`, `call_site.scope`, `ticket.status` | `derive_collides`; 70dd: 0. Spark proxy: inside rule 32 of 32 pairs, model-read ([GRAPH.md](../GRAPH.md)) | As S2. Function scope only; 04 says "function or file" (GRAPH.md Flags). Human check of the 50 pairs open |
| S5 · Duplicates | 🟡 | `chain` (`duplicates`), `ticket_map` | `derive_duplicates`; 70dd: 0 | As S2. 05-test "beats title similarity" on Spark Duplicates not run |
| S6 · Untracked work | ✅ | `call_site`, `ticket_map`, `chain` (`untracked`) | `staff_untracked`; 70dd: 1,526 sites, 43 `untracked` chains (one per team) | Right but total: no code dataset has a tracker, so every site is untracked |
| S7 · Vague tickets flagged | 🟡 | `ticket.description`, `desc_empty`, `ticket_map` | `staff_vague`; 70dd: 0 (reads only tickets mapped to call sites). The data exists: 13,676 Jira tickets `desc_empty`, 6,045 open Jira tickets without "done when" | As S2. "Done when" is a `LIKE '%done when%'` heuristic; 05-test "beats description length" not run |
| S8 · Suggested rewrites | ⛔ | facts: `manager_facts` rows | Stage 6 Writer + stage 7 gate | Rewrite outcome also needs description changes in `ticket_event` (logged today: status, resolution, label, link) |
| S9 · Link coverage, tickets unmapped | ✅ | `coverage`, `ticket_map` | `staff_link_coverage`; 70dd: 36,138 of 74,015 (Jira 61%, GitLab 0 of 14,524) | [BIAS.md](../BIAS.md): mapping falls with ticket age and for Kubernetes/Docker |
| S10 · Teams not connected | ✅ | `call_site`, `ticket_map` | `stall_never_notified`; 70dd: 43 of 43 teams | All teams, by the data. BIAS.md: show "unknown" for under-mapped components |
| S11 · Legacy radar: deprecated symbols with callers | 🟡 | `deprecation`, `call_edge`, `resolution.resolved_to` | `radar_deprecated_with_callers`; 70dd: 52 `deprecation` rows, 0 with callers (0 resolutions or edges into them) | `call_edge` only covers callers above tracked symbols (KB G3); scan finds tracked names only |
| S12 · Legacy radar: old high fan-in files | ❌ | `git_commit` ✅; fan-in ✗ | none | KB G3: no whole-repo call graph |
| S13 · Radar accept/reject trains the auditor | ⛔ | `suggestion` (`kind = 'legacy'`), `match` | none | Stage 9; neither table is in schema.sql (KB G1) |
| S14 · Blast radius | ✅ | `blast_radius`, `call_edge`, `resolution` | `test_graph`; 70dd: 25 rows, 1,526 sites → 2,331 invocations (Django `ugettext` 93 → 585) | 2,293 vs 2,288 edges, 5 Django edges unexplained ([OPEN-POINTS](../../OPEN-POINTS.md)) |
| S15 · Per cross-team ticket: link, order, owners, risk, done condition | 🟡 | `blocked_by`, `call_site`, `file_bug_fixes`, `ticket` | Composite of S3, R3, O5 | Inherits S2, S3, O5; the diff is ⛔ (C4) |

### API owner

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| O1 · N of M done | 🟡 | `call_site` versions (`status`, `effective_date`) | `owner_done`; 70dd: 0 of 1,526 | One run, so no `removed` version yet. Needs a second run with `--prev-run` ([JOIN.md](../JOIN.md) Next) or 05-test's Django burndown replay (not done) |
| O2 · ETA | 🟡 | `call_site.effective_date` of removals | `owner_eta`; 70dd: NULL (0 removals in 28 days, under 3) | As O1 |
| O3 · Per team: left, moving / stalled, days | ✅ | `call_site`, `blocked_by` | `owner_teams`; 70dd: 43 teams (e.g. `contrib.admin` 43 left, stalled) | "Blocked" can't fire: O4 |
| O4 · Per team: blocked, "waiting on X" | 🟡 | `blocked_by` | `owner_teams`, `consumer_blocked`; 70dd: 0 `blocked_by` | 0 `receiver_from` on real data: `url()`, `ugettext()` are called directly (GRAPH.md) |
| O5 · Per team: risky count | 🟡 | `call_site.bug_fixes` | `owner_teams`; 70dd: 0 sites with `bug_fixes` > 0 | SZZ runs on Spark only; Spark has no call sites (JOIN.md) |

"N possible reasons" is the stall reasons below.

### API consumer

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| C1 · Sites per file | ✅ | `call_site` | `consumer_files`; 70dd: 28 files for `django-oscar` | |
| C2 · Bug fixes this year per file | 🟡 | `file_bug_fixes` | `consumer_files`; 70dd: 1,492 Spark files, 0 call-site files | As O5 |
| C3 · Blocked by a shared client; "you are blocking X" | 🟡 | `blocked_by`, `call_site.owner_team` | `consumer_blocked`, `consumer_blocking`; 70dd: 0 | As O4 |
| C4 · One-line swaps, diffs | ⛔ | facts: `call_site`, `in_except`, `file_bug_fixes` | Stage 6 codemod / LLM | No `suggestion` table (KB G1) |

### Developer push check ([pushcheck.py](../meridian/pushcheck.py), `.github/workflows/push-check.yml`)

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| P1 · Drift | ✅ | `call_site` base vs push (TEMP tables) | `p1_drift`; `test_pushcheck` P1; replay of 21 real commits ([scenarios §8](appendix-scenarios.md#8-push-check-p1p9)): 4 of 11 hits true | Precision 0.36 on the replay: look-alikes count, since there is no resolver at push time. 05-test's other bars not run (500 Django commits, 0 diffs; recall vs `git log -S`) |
| P2 · Legacy use | 🟡 | `deprecation`, `resolution.resolved_to` | `p2_legacy_use`; `test_pushcheck` P2; 70dd: 0 tracked sites resolve to a deprecated qualname | Scan sees tracked names only, so it misses new calls to other deprecated functions (G3) |
| P3 · Collision | 🟡 | `ticket_map`, `call_site.scope`, `ticket.status`, `ticket.components` | `p3_collision`; `test_pushcheck` P3; 70dd: 0 | As S2. The path-without-repo shortcut is fixed: pushcheck sets `asof.repo` and `site_now` is scoped to it |
| P4 · New block | 🟡 | `blocked_by` (`derive_blocked_by`), `resolution.receiver_from` | `test_pushcheck` P4 | Shortcut: sees only a new site inside F; a new receiver needs the resolver (pushcheck.py). 0 `receiver_from` on 70dd |
| P5 · Ticket can move | 🟡 | `ticket_map`, `call_site`, `ticket` | `p5_ticket_can_move`; `test_pushcheck` P5; 70dd: 0 | As S2 |
| P6 · Mismatch | 🟡 | `ticket`, `ticket_map`, `call_site` | `stall_ticket_mismatch`; `test_pushcheck` P6; 70dd: 0 | As S2 |
| P7 · Risky edit | 🟡 | `file_bug_fixes` (`bug_fixes`, `fix_keys`) | `p7_risky_edit`; `test_pushcheck` P7; 70dd: 0 call-site files with bug fixes | As O5 |
| P8 · Bad key | ✅ | commit messages, `ticket.key` | Python in pushcheck.py; `test_pushcheck` P8; history: 3 dead keys on Spark | Jira only; GitLab `#123` refs not checked (shortcut) |
| P9 · Defect risk | ✅ | `commit_file` (push numstat) | `p9_defect_risk`; `test_pushcheck` P9; 70dd: newest commit 633 lines × 6 files = 3,798. On 41k Spark commits it flags 32% of SZZ's bug-inducing commits vs 9% of the rest ([scenarios §8](appendix-scenarios.md#8-push-check-p1p9)) | No model; 05-test's time-split bar ("beats lines × files") still open |
| Push scope: changed files + importers, skip > 200 | ✅ | `module_import` | `push_scope_importers`; `test_pushcheck` scope + limits; 70dd: 1,282 pairs | `module_import` 20-row hand check open |
| Alert bars: precision ≥ 0.80, median ≤ 1, p95 ≤ 5 min | 🟡 | push replay | Replay of 21 real commits ([scenarios §8](appendix-scenarios.md#8-push-check-p1p9)): median ≤ 1 alert per push met; P1 precision 4/11 = 0.36, below 0.80 | p95 ≤ 5 min on a free runner not measured |
| Alert outcome stored (fixed, dismissed, ignored) | ❌ | `push_alert` (proposed DDL) | `push_alert_outcomes` on the fixture only | KB G1: not in schema.sql; pushcheck.py prints a comment, writes no row |

### Ticket tools

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| T1 · Manager: facts for a rewrite (sites, owner, bug fixes, `in_except`) | 🟡 | `ticket_map`, `call_site`, `in_except` | `manager_facts`; 70dd: 0 for `SPARK-48763` | As S2 |
| T2 · Check: unmapped | ✅ | `ticket_map` (`unmapped`) | 70dd: 37,877 unmapped rows | |
| T3 · Check: no done condition, bug without repro | 🟡 | `ticket.description`, `ticket.type` | `staff_vague` (done condition only) | No repro query; text heuristic; as S7 |
| T4 · Check: spans teams, duplicate, wrong owner | 🟡 | `ticket_map`, `call_site.owner_team`, `ticket.components` | `derive_duplicates`, `check_wrong_owner`; 70dd: 0 | As S2; "spans teams" has no named query |
| T5 · Check: stale (30 days, code changed) | 🟡 | `ticket_event`, `commit_file`, `ticket_map` (path) | No query ("not in the fixture", KB) | Data is there: 235,252 events, 208,551 path-level maps |
| T6 · Maker: untracked sites, one group per team | ✅ | `call_site`, `ticket_map` | `maker_drafts`; 70dd: 43 team groups | "Wave" isn't defined in the KB |
| T7 · Maker: draft text | ⛔ | facts: `maker_drafts` rows | Stage 6 (template or Writer) | |
| T8 · Intake: Slack, support, GitHub issues → fingerprint → drafts | ❌ | none | none | KB G5: nothing collects intake |

### Rules and order of work

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| R1 · Risky: `bug_fixes` ≥ 3 | 🟡 | `call_site.bug_fixes` | `owner_teams`; 70dd: 0 | As O5 |
| R2 · Stall: present, no change in 14 days | ✅ | `call_site.effective_date` | `owner_teams`; 70dd: stalled teams, days from blame | |
| R3 · Order: topological on `blocked_by`, then `bug_fixes`, then ID | 🟡 | `blocked_by`, `call_site` | `order_of_work`; 70dd: 1,526 rows, ordered by ID only (no blocks, no bug fixes) | No cycle guard ([OPEN-POINTS](../../OPEN-POINTS.md), "Not assigned"); 0 `blocked_by` |
| R4 · Fragility ships with ticket IDs | ✅ | `file_bug_fixes.fix_keys`, `bug_inducing` | `p7_risky_edit`; SZZ 44% vs churn 18% vs latest 16% on Defects4J (KB testing plan); 70dd: 1,492 Spark files, 26,559 `bug_inducing` | Spark only |
| R5 · Thresholds and "done" statuses in config | ✅ | `meridian.toml` `[rules]` | KB G8, done | |

### Stall reasons (04 §5)

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| #1 Upstream block | 🟡 | `blocked_by` | `stall_upstream`; 70dd: 0 | As O4 |
| #2 Never notified | ✅ | `ticket_map` | `stall_never_notified`; 70dd: 43 teams | As S10 |
| #3 Ticket mismatch | 🟡 | `ticket`, `ticket_map`, `call_site` | `stall_ticket_mismatch`; 70dd: 0 | As S2 |
| #4 Orphaned owner | ✅ | `file_owner.top_person_id`, `person_alias`, `git_commit` | `stall_orphaned_owner`; 70dd: 141 files | Identity-group hand check open |
| #5 Semantics unclear | ✅ | `call_site.in_except` | `stall_semantics_unclear`; 70dd: 2 sites | "No eligible diff" half is stage 6; `in_except` hand check open |
| #6 Fragility freeze | 🟡 | `call_site.bug_fixes` | `stall_fragility_freeze`; 70dd: 0 | As O5; "team busy elsewhere" half isn't in the query |
| #7 Test gap | ✅ | `module_import` (`is_test`) | `stall_test_gap`; 70dd: 250 (team, module) rows | `module_import` hand check open |
| #8 Competing load | 🟡 | `git_commit`, `commit_file`, `person_alias`, `team_map` | `stall_competing_load`; 70dd: 8 rows, all Spark teams (0 since the fix that counts only teams owning call sites) | Spark teams have no call sites, so it can't yet speak about a migrating team (G7) |
| Ranking and verdict data | 🟡 | `ticket_link.removed_at`, `ticket_event`, `git_commit` | 70dd: 3,031 `blocked_by` links (2,646 live), 111,003 status events | Ranking is stage 5, not built. Stall reasons written in comments aren't kept (KB G2). Gold set: 0 of 262 human verdicts |

### LLM and gate

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| L1 · Diff drafter (codemod from the owner's prior migrations, LLM if ≥ 2 same-shape) | ⛔ | `call_site` removed versions, `in_except` | Stage 6 | "Prior migrations" need removed sites; 70dd has one run |
| L2 · Jev router, pinned models, cache | ⛔ | `run.model_versions` (empty in step 1) | Stage 6 | |
| L3 · Gate: every fact in the text is a row | ⛔ | facts from the KB queries | Stage 7 | HANDOFF: facts never include people |

### Auditor learning (04 §9)

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| A1 · Policies, matches, ε-greedy, Elo, promotion | ⛔ | `policy`, `match` (not drafted) | Stage 9 | KB G1 |
| A2 · Replay with `known_at` ≤ stall time | 🟡 | `call_site.known_at` | `replay_as_of`; 70dd: 1,526 present, 0 removed | One run: nothing to replay yet |
| A3 · Explore: same-signature twin gaining callers | ❌ | none | none | KB G4: no function signatures |

Explore's other signals are S11 (deprecation with callers) and S12 (fan-in).

### Outcome tracking

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| U1 · Suggestion used, edited, ignored | ❌ | `suggestion`, `outcome` (proposed DDL) | `outcome_precision` on the fixture only | KB G1: not in schema.sql. Outcomes can't be backfilled |
| U2 · Burndown before vs after, with control | 🟡 | `call_site` history, `suggestion` | `outcome_before_after`, `outcome_effect`; 70dd: runs, 43 control teams, 0 removals | Needs U1's table and more than one run |
| U3 · Auto-apply gate, week-8 review | ❌ | `suggestion`, `outcome` | `auto_apply_gate`, `outcome_week8` on the fixture only | KB G1 |

Push alert outcomes are in the push check table.

### Update graph and staleness ([UPDATE-PLAN.md](../notes/UPDATE-PLAN.md))

| Feature | Status | Reads | Evidence | Gap |
|---|---|---|---|---|
| G1 · Staleness line ("data 2 days old · 14 new commits") | ✅ | `run`, `published/latest.json`, repo mirrors | `meridian/status.py`; `test_publish` | |
| G2 · "Update graph" click → `meridian_update` (anything new?, one run at a time) | 🟡 | `run` | `dags/meridian_dag.py`, `status.py --update`; `test_dag`, `test_publish` | Never run end to end in Airflow (OPEN-POINTS) |
| G3 · Incremental collect: mirrors, cursors, branches followed | 🟡 | snapshot manifest | `meridian/collect.py`; `test_collect` | No second real run yet (JOIN.md Next) |
| G4 · Weekly rebuild equals the updates (0 diffs) | 🟡 | every table | `meridian/compare.py`; `test_publish` | Never compared on two real runs |
| G5 · Change report: what changed, which commit | ✅ | `call_site.effective_sha`, `call_edge.introduced_sha`, `blast_radius` | `meridian/report.py`; `test_report`; 70dd: first run, all added | Removed edges aren't attributed to a commit |
| G6 · Publish only if validate passes | ✅ | every table | `meridian/publish.py`; `test_publish`; 70dd: 1,788,242 rows, 0 errors (README) | |
| G7 · `run_id`, `snapshot_at`, coverage on every screen | ✅ | `run`, `coverage` (14 metrics) | 70dd: `call_sites_resolved` 1,525 of 1,526, `files_owned` 22,060 of 43,870 | |

## Summary

| Status | Count |
|---|---:|
| ✅ supported now | 24 |
| 🟡 partial | 36 |
| ❌ not yet | 6 |
| ⛔ out of scope for the data pipeline | 8 |
| **Total** | **74** |

Most 🟡 rows don't lack columns. The schema and query exist and pass on the fixture; the real block just gives the query nothing to work on.

## Top gaps

| # | Gap | Blocks | What would close it (team docs) |
|---|---|---|---|
| 1 | No real ticket maps to a call site, and no call site has bug fixes: no dataset has both a tracker and tracked code (see S2), and SZZ runs on Spark only | 17 rows: S2, S4, S5, S7, S15, O5, C2, P3, P5, P6, P7, T1, T3, T4, R1, #3, #6 (and #8) | Known since week 1: the KB fixture carries the end-to-end story, and each table is scored on the dataset that has it ([KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md) G7, [02-product](../../docs/02-product.md) Known gaps). Collisions are checked on Spark hunks instead ([GRAPH.md](../GRAPH.md)). Adding `apache/spark-docker` and `spark-kubernetes-operator` raises Spark mapping, not call-site mapping ([BIAS.md](../BIAS.md)) |
| 2 | 0 `blocked_by`, because 0 `receiver_from` on real data | 6 rows: S3, O4, C3, P4, R3, #1 (plus S15, and O3's "blocked" state) | Code-level blocks stay on fixtures (GRAPH.md). Hand-check 20 `receiver_from` rows on Django to confirm 0 is right; `blocked_by` may need depth 5, which means raising `[resolver] call_edge_depth` too ([OPEN-POINTS](../../OPEN-POINTS.md), Rochan) |
| 3 | Only one run: no `removed` versions, no history | 7 rows: O1, O2, A2, U2, L1, G3, G4 (and P1's replay bar) | A second real run with `--prev-run` once there's a later snapshot from the nightly DAG ([JOIN.md](../JOIN.md) Next, OPEN-POINTS Utkarsh), or 05-test's Django weekly burndown replay (marked "dropped first if behind") |
| 4 | Tracking tables not in schema.sql (`suggestion`, `outcome`, `push_alert`; `policy`, `match` not drafted) | 6 rows: U1, U3, push alert outcomes, S13, A1, C4 (and U2) | Move the three tested DDLs into schema.sql the day stage 6 offers its first suggestion, since outcomes can't be backfilled. `policy` and `match` come with stage 9 (KB G1, OPEN-POINTS "Not assigned") |
| 5 | `call_edge` covers only callers above tracked symbols; no function signatures | 4 rows: S11, S12, A3, P2 | G3: a whole-repo call graph is a second resolver pass; measure its cost on Django first. G4: a `function(repo, path, scope, params)` table from scan's tree-sitter pass, "cheap" (KB Gaps) |

Also ❌ on its own: intake (T8, KB G5: its own collect source, later).
