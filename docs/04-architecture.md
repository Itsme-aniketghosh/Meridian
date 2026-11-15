# Architecture

## Determinism

- Every run reads a frozen snapshot (repo SHAs, raw Jira, raw intake) in GCS by `run_id`
- Same snapshot + config + image = byte-identical rows
- No wall clock after Collect. Time = `snapshot_at`
- Thresholds in `meridian.toml`, hash on the run
- Output sorted by (repo, path, line, column). IDs are content hashes
- Randomness seeded: hash(run_id, task_id)
- Rules decide facts, models score, Jev routes, LLMs write text and code (cached)

## Pipeline

```
 1 COLLECT   frozen snapshot
 2 EXTRACT   candidates · resolver · ownership · ticket links · SZZ
 3 JOIN      call_site rows, stable IDs
 4 GRAPH     blocked_by · blast radius · chains
 5 RULES     stall · ETA · order · reasons · ticket checks · fragility
 6 LLM       orchestrator plans · Jev routes · agents write · walkers when a tool fails · cached
 7 GATE      text matches facts, else template
 8 VIEWS     read-only
 9 LEARN     Elo over auditor and walk policies → ε-greedy → back to 5 · model refresh (§11)
 per push    2–4 on the diff only (§10)
```

## 1. Collect

- Record each repo's HEAD SHA. Pull Jira by key, store raw JSON, never re-query
- Intake stored by source ID, personal data stripped
- Force-push → halt repo. Rate limit → checkpoint, resume. Publish only when complete

## 2. Extract

- Candidates: tree-sitter, pinned. Match rules per symbol from config. Parse failure → excluded, counted
- Resolver: pinned. Resolves against the snapshot (the repo, its dependencies' checkouts,
  the standard library), nothing installed. Binds to symbol →
  `ast_resolved`. Binds elsewhere → dropped. Can't bind → `ast_unresolved`. Pattern
  hits → `string_url`, `config`
- Ownership: 180 days, no merges, bots, or ignore-revs. `.mailmap` + alias table.
  Owner = top author's team by commit share. Tie → most recent. Share < 0.30 → `unowned`
- Ticket links: key regex `[A-Z][A-Z0-9]+-\d+`, must exist in snapshot. Ticket →
  code by linked-commit files, exact path, stack frame, exact symbol. All hits kept.
  None → `unmapped`. No fuzzy matching
- SZZ: fix = Bug + Fixed, not a revert. Blame changed lines at the parent, skip whitespace and
  comments. `bug_fixes` = fix commits on the file in 365 days

## 3. Join

- ID = hash(repo, symbol, path, scope, normalized line, occurrence)
- Match last run's rows: git line map with renames → same (repo, symbol, scope,
  normalized line) → same (path, scope, symbol) → else `removed` / new
- `effective_date` = first first-parent commit where true

## 4. Graph

- blocked_by A → B: different owners, A's receiver assigned from a call to F, B
  inside F within 3 resolved calls. Unresolved → `unknown`. Imports alone don't count
- Blast radius: resolved invocations reaching the symbol, depth ≤ 3
- Chains: `depends_on` (block between mapped sites), `duplicates` (shared call
  site), `collides` (shared function or file), untracked (no ticket). Cycles flagged

## 5. Rules

- Risky: `bug_fixes` ≥ 3
- Stall: present call sites, no change in 14 days
- ETA: remaining ÷ removals/day over 28 days. < 3 removals → none
- Order: topological sort on `blocked_by`, then `bug_fixes` desc, then ID
- Fragility: the `bug_fixes` count unless a model beats it

Stall reasons (auditor policy v0), ranked by #, then evidence. No probabilities.

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

Ticket checks: unmapped · no done condition · bug without repro · spans teams ·
duplicate · wrong owner · stale (30 days, code changed). Sub-tasks: duplicate and
wrong owner only.

- Maker: one draft per (team, wave)
- Intake: fingerprint = exception type + top 3 in-repo frames, or normalized error.
  Exact match clusters

## 6. LLM

- Agents write prose from a fact set, and code the codemod can't. Decide no facts
- Pinned models and prompts, JSON schema, cache key hash(model, prompt, facts)
- Diffs: codemod from the owner's prior migrations first. LLM only if ≥ 2 same-shape
  examples and not in `except`. Must apply, parse, touch only that site, resolve

Orchestrator: per stalled team, one planning call on the strongest model the billing
can buy (Gemini 3.1 Pro). It works one closed list: identify the stall's own reasons,
push steps from a menu of 4 writer tasks, finish. Each step once, so every plan ends.
Bad JSON or a reason not in the facts → the rules plan (every available step).

Context builder (a tool): keeps the files holding 90% of a team's bug fixes, counts the
rest ("3 call sites in 2 other files"). Switched on by its rule: ≥ 90% of fixes kept and
≥ 10 points over random (96.4% with 35% of files; random 72.7%).

Graph walkers: when the tool fails (error, hang, nothing usable, or two gate misses;
decided by code), they query the graph directly. Named KNOWLEDGE-BASE queries only,
block read-only, ≤ 3 teams, 2 `blocked_by` hops, 24 queries per stall, every result
cached by run_id. What they find is one more writer task, `walk`. Walk policies:
`walk-v0` (blocked_by order) is the champion; `walk-v1` follows the auditor's ranking,
served ε of the time, the other's route kept in shadow (§9).

Jev, the router, picks which model starts each task. On a failed check the next model
up, or stop. The rules route today: on 30 held-out tasks they were right 29 times at $0,
and no model beat them. Phi-4-mini when a router runs.

| Agent | Models | Retry on |
|---|---|---|
| Orchestrator | Gemini 3.1 Pro, one call per stall | Bad JSON, gate miss → rules plan |
| Diff drafter | codemod; LLM coding small → mid → large | Diff doesn't apply, parse, or resolve |
| Writer | Gemini 3.8 Flash (picked: cheapest within 2 points of the best, 204 tasks) | Bad JSON, gate miss |
| Policy writer | coding mid → large | Cut in phase 2 (waits for live matches) |

- Picks only from this table. Models and a top-model budget pinned in `meridian.toml`. Cached
- Jev down → rules pick. Top model fails → template, or nothing for code
- Every model pick has its rule committed before its run; the answers are cached, so the comparison reruns for $0

## 7. Gate

- Every number, path, SHA, ID, person, team in LLM text must be in its facts
- Any miss → whole text becomes the template. Fallback rate published, and sliced by task kind, repo and team
- A GitHub fix key (`#6921`) is a ticket, like `SPARK-6921`, not a number
- A gate that rejects right text is fixed in the facts, never loosened: a looser gate lets a swapped team through

## 8. Views

Read-only. `run_id`, `snapshot_at`, and coverage on every screen.

## 9. Auditor learning

- Policy: versioned code choosing where to look. Jobs: stall (rank reasons, v0
  above) and explore (nominate unfiled legacy code, 200 expansions per run)
- Explore signals: deprecation marker with callers · same-signature twin gaining
  callers · no commits in 2 years and fan-in ≥ 10 · high `bug_fixes` and fan-in
- ε-greedy: champion with 1 − ε, else random challenger. Seed hash(run_id,
  task_id). ε = 0.2, then 0.1 after 30 matches
- Matches:
  - stall: all policies in shadow; ranking the true reason higher wins
  - explore: team-draft interleaving; more accepts wins; no reply in 14 days = no match
  - replay: past stalls, rows with `known_at` ≤ stall time only, half K
    ([Dream-RSI](https://arxiv.org/html/2609.14858v1))
- Verdicts by rule: block removed ≤ 7 days before resumption · ticket created ≤ 14
  days before · new author resumed · ticket reopened. Else a human picks. None → no match
- Elo: start 1500, K 32 then 16 after 30 matches. Recomputed from the log each run.
  Per repo after 30 matches
- Promotion: ≥ 30 live matches, +50 Elo, beats v0, and a luck test on the live wins
  and losses against the champion (draws left out): luck gets a no-better policy past it
  ≤ 5% of the time per champion, however often it's checked. Built to find +200 Elo; the
  5% is split over the challengers (auditor decision 12)
- LLMs may propose policy code. A human reviews it. Never in the decision path
- Walk policies play the same matches: the stall's verdict scores the route each walk took

## 10. Push check

- Trigger: every PR, GitHub Actions. A fork's PR can't comment, so its check goes to the job summary
- Base: last nightly map as one SQLite file. Jira from the base snapshot
- Scope: changed files + importers. Over 200 files → skip
- Run: Extract, Resolve, Join, Graph on scope. Output = base rows vs push rows

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
| P9 | Defect risk | lines × files above threshold. The model ranks better (2024 PR-AUC 0.268 vs 0.214) but can't alert at precision 0.80, so P9 keeps lines × files |

- One comment, templates only, $0. Max 10 lines. Never blocks merge
- Outcome stored: fixed, dismissed, ignored
- Limits: one repo per push; base up to 24 h old
- Nightly must contain every push row for the merged SHA

## 11. Self-improving loops

Each loop changes production only through a rule written before it runs.

| Loop | Turns by | Status |
|---|---|---|
| Model | `model.refresh`: new block → roll the split → retrain all → re-score the champion on the same val → promote on a gain over 2 seed-noise SDs | Built; waits for a new published block |
| Auditor policies | §9: shadow rankings, verdicts, Elo, the luck guard | Built; 0 live matches |
| Walk policies | walk-v0 vs walk-v1, scored by the same verdicts | Built; 0 live matches |
| Agents | gate miss → next model up → template; fallback rate sliced → fix the facts → re-gate the cache at $0 | Built; a person makes the fix |
| Diff drafter | waits for the owner's first migrated file, copies their style, learns from their edits, never re-offers a rejected diff | Built; fixture only |
| Policy writer | an LLM writes a policy from the champion's losses → human review → challenger | Cut |

## Schedule and infra

- 02:00 UTC incremental run. Sunday full rebuild

| What | Where |
|---|---|
| Pipeline | Airflow: daily update DAG, Sunday rebuild DAG (Docker today, GCP not set up) |
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
| Incremental ≠ rebuild | Alert, publish the rebuild |
| No base map | Push check silent |
| Push ≠ nightly | Nightly wins, logged |
| Planner answer bad | Rules plan |
| Context tool fails | Graph walkers |
| Cache miss in CI (`--strict-cache`) | Job fails |
| Champion file broken | P9 uses lines × files and says so |
