# Architecture

## Determinism

- Every run reads a frozen snapshot (repo SHAs, raw Jira, raw intake) in GCS by `run_id`
- Same snapshot + config + image = byte-identical rows
- No wall clock after Collect. Time = `snapshot_at`
- Thresholds in `meridian.toml`, hash on the run
- Output sorted by (repo, path, line, column). IDs are content hashes
- Randomness seeded: hash(run_id, task_id)
- Rules decide facts, models score, Jev routes, LLMs write text and code (cached)
- Weekly full rebuild must equal incremental, else halt

## Pipeline

```
 1 COLLECT   frozen snapshot
 2 EXTRACT   candidates · resolver · ownership · ticket links · SZZ
 3 JOIN      call_site rows, stable IDs
 4 GRAPH     blocked_by · blast radius · chains
 5 RULES     stall · ETA · order · reasons · ticket checks · fragility
 6 LLM       Jev routes, agents write, cached
 7 GATE      text matches facts, else template
 8 VIEWS     read-only
 9 LEARN     Elo over auditor policies → ε-greedy → back to 5
 per push    2–4 on the diff only (§10)
```

## 1. Collect

- Record each repo's HEAD SHA. Pull Jira by key, store raw JSON, never re-query
- Intake stored by source ID, personal data stripped
- Force-push → halt repo. Rate limit → checkpoint, resume. Publish only when complete

## 2. Extract

- **Candidates:** tree-sitter, pinned. Match rules per symbol from config. Parse failure → excluded, counted
- **Resolver:** pinned, one env per repo from its lockfile. Binds to symbol →
  `ast_resolved`. Binds elsewhere → dropped. Can't bind → `ast_unresolved`. Pattern
  hits → `string_url`, `config`
- **Ownership:** 180 days, no merges, bots, or ignore-revs. `.mailmap` + alias table.
  Owner = top author's team by commit share. Tie → most recent. Share < 0.30 → `unowned`
- **Ticket links:** key regex `[A-Z][A-Z0-9]+-\d+`, must exist in snapshot. Ticket →
  code by linked-commit files, exact path, stack frame, exact symbol. All hits kept.
  None → `unmapped`. No fuzzy matching
- **SZZ:** fix = Bug + Fixed. Blame changed lines at the parent, skip whitespace and
  comments. `bug_fixes` = fix commits on the file in 365 days

## 3. Join

- ID = hash(repo, symbol, path, scope, normalized line, occurrence)
- Match last run's rows: git line map with renames → same (repo, symbol, scope,
  normalized line) → else `removed` / new
- `effective_date` = first first-parent commit where true

## 4. Graph

- **blocked_by A → B:** different owners, A's receiver assigned from a call to F, B
  inside F within 3 resolved calls. Unresolved → `unknown`. Imports alone don't count
- **Blast radius:** resolved invocations reaching the symbol, depth ≤ 3
- **Chains:** `depends_on` (block between mapped sites), `duplicates` (shared call
  site), `collides` (shared function or file), untracked (no ticket). Cycles flagged

## 5. Rules

- **Risky:** `bug_fixes` ≥ 3
- **Stall:** present call sites, no change in 14 days
- **ETA:** remaining ÷ removals/day over 28 days. < 3 removals → none
- **Order:** topological sort on `blocked_by`, then `bug_fixes` desc, then ID
- **Fragility:** the `bug_fixes` count unless a model beats it

**Stall reasons** (auditor policy v0), ranked by #, then evidence. No probabilities.

| # | Reason | Fires when |
|---|---|---|
| 1 | Upstream block | A call site has `blocked_by` |
| 2 | Never notified | No ticket maps to the team's call sites |
| 3 | Ticket mismatch | Ticket Done, call site present |
| 4 | Orphaned owner | Top author silent 60 days |
| 5 | Semantics unclear | Inside `except`, no eligible diff |
| 6 | Fragility freeze | All remaining risky, team busy elsewhere |
| 7 | Test gap | No test imports the module |
| 8 | Competing load | No commits to call-site files, team at ≥ 75% of usual |

**Ticket checks:** unmapped · no done condition · bug without repro · spans teams ·
duplicate · wrong owner · stale (30 days, code changed). Sub-tasks: duplicate and
wrong owner only.

- **Maker:** one draft per (team, wave)
- **Intake:** fingerprint = exception type + top 3 in-repo frames, or normalized error.
  Exact match clusters

## 6. LLM

- Agents write prose from a fact set, and code the codemod can't. Decide no facts
- Pinned models and prompts, JSON schema, cache key hash(model, prompt, facts)
- **Diffs:** codemod from the owner's prior migrations first. LLM only if ≥ 2 same-shape
  examples and not in `except`. Must apply, parse, touch only that site, resolve

**Jev, the router.** A general reasoning model. It reads the input problem and picks the
agent and model. On a failed check it picks the next model up, or stops.

| Agent | Models | Retry on |
|---|---|---|
| Diff drafter | coding small → mid → large | Diff doesn't apply, parse, or resolve |
| Policy writer | coding mid → large | Policy tests fail |
| Writer | general small → mid → large | Bad JSON, gate miss |

- Picks only from this table. Models and a top-model budget pinned in `meridian.toml`. Cached
- Jev down → rules pick. Top model fails → template, or nothing for code

## 7. Gate

- Every number, path, SHA, ID, person, team in LLM text must be in its facts
- Any miss → whole text becomes the template. Fallback rate published

## 8. Views

Read-only. `run_id`, `snapshot_at`, and coverage on every screen.

## 9. Auditor learning

- **Policy:** versioned code choosing where to look. Jobs: **stall** (rank reasons, v0
  above) and **explore** (nominate unfiled legacy code, 200 expansions per run)
- **Explore signals:** deprecation marker with callers · same-signature twin gaining
  callers · no commits in 2 years and fan-in ≥ 10 · high `bug_fixes` and fan-in
- **ε-greedy:** champion with 1 − ε, else random challenger. Seed hash(run_id,
  task_id). ε = 0.2, then 0.1 after 30 matches
- **Matches:**
  - stall: all policies in shadow; ranking the true reason higher wins
  - explore: team-draft interleaving; more accepts wins; no reply in 14 days = no match
  - replay: past stalls, rows with `known_at` ≤ stall time only, half K
    ([Dream-RSI](https://arxiv.org/html/2609.14858v1))
- **Verdicts** by rule: block removed ≤ 7 days before resumption · ticket created ≤ 14
  days before · new author resumed · ticket reopened. Else a human picks. None → no match
- **Elo:** start 1500, K 32 then 16 after 30 matches. Recomputed from the log each run.
  Per repo after 30 matches
- **Promotion:** ≥ 30 live matches, +50 Elo, beats v0
- LLMs may propose policy code. A human reviews it. Never in the decision path

## 10. Push check

- **Trigger:** every push and PR, GitHub Actions
- **Base:** last nightly map as one SQLite file. Jira from the base snapshot
- **Scope:** changed files + importers. Over 200 files → skip
- **Run:** Extract, Join, Graph on scope. Output = base rows vs push rows

| # | Check | Fires when |
|---|---|---|
| P1 | Drift | New call site to a tracked symbol |
| P2 | Legacy use | New call to a deprecation-marked symbol |
| P3 | Collision | Edits a function mapped to another team's open ticket |
| P4 | New block | Creates or moves a `blocked_by` edge |
| P5 | Ticket can move | Removes the last call site of a non-Done ticket |
| P6 | Mismatch | Re-adds a call site of a Done ticket |
| P7 | Risky edit | Changes a call site in a file with `bug_fixes` ≥ 3 |
| P8 | Bad key | Commit names a key not in the snapshot |
| P9 | Defect risk | lines × files above threshold, unless a model beats it |

- One comment, templates only, $0. Max 10 lines. Never blocks merge
- Outcome stored: fixed, dismissed, ignored
- Limits: one repo per push; base up to 24 h old
- Nightly must contain every push row for the merged SHA

## Schedule and infra

- 02:00 UTC incremental run. Sunday full rebuild

| What | Where |
|---|---|
| Pipeline | Cloud Run Jobs, one per repo, Cloud Scheduler |
| Push check | GitHub Actions (free) |
| Site, API | Cloud Run |
| DB | SQLite on GCS, Cloud SQL once there's a user |
| Snapshots, LLM cache | GCS |

- Job memory sized from last peak (disk is in memory). Billing alert day 1

## Failures

| Failure | Behavior |
|---|---|
| Jira down | Code-only map, run flagged partial |
| Force-push | Halt repo |
| Parse failure | Excluded, counted |
| No ticket links | Risk and chains off, said on screen |
| Incremental ≠ rebuild | Halt, keep last good run |
| No base map | Push check silent |
| Push ≠ nightly | Nightly wins, logged |
