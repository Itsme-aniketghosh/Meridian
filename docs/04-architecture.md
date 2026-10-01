# Architecture

## Determinism contract

- Every run starts from a frozen snapshot: repo SHAs, raw Jira JSON, and raw intake
  JSON, stored in GCS under `run_id`
- Same snapshot + same config + same container digest = byte-identical rows
- Nothing reads the wall clock after Collect. All time math uses `snapshot_at`
- Every threshold lives in `meridian.toml`. Its hash is recorded on the run
- Output is sorted by (repo, path, line, column) before it's written
- IDs are content hashes, never random or serial
- The only randomness is seeded: seed = hash(run_id, task_id)
- Rules decide. Models score. LLMs only write text and draft diffs, and their output
  is cached
- A weekly full rebuild from raw must equal the incremental map. Any difference halts
  the pipeline

## Pipeline

```
 repos     git log     Jira     Slack / support / GitHub issues
   └─────────┴──────────┴──────────┘
                  ▼
 1 COLLECT   frozen snapshot per run_id
 2 EXTRACT   candidates · resolver · ownership · ticket links · SZZ
 3 JOIN      call_site rows, stable IDs, append-only
 4 GRAPH     blocked_by · blast radius · ticket chains
 5 RULES     stall · ETA · order · reasons · ticket checks · fragility
 6 LLM       prose and diff drafts only, cached
 7 GATE      text must match its facts, else template
 8 VIEWS     staff engineer · owner · consumer
 9 LEARN     outcomes → Elo over auditor policies → ε-greedy pick → back to 5

 per push:   2–5 on the diff only, against the last nightly map (section 10)
```

## 1. Collect

- Clone each repo and record its default-branch HEAD SHA
- Pull every Jira ticket in the configured projects, ordered by key, and store the raw
  JSON. Reruns read the stored JSON and never re-query
- Intake: same approach per source, ordered by source ID. Personal data is stripped
  before storage
- `snapshot_at` = when the run started
- Previous SHA isn't an ancestor of the new one (force-push) → halt that repo
- Rate limited → checkpoint, back off, resume. A run publishes only when complete

## 2. Extract

**Candidates** (tree-sitter, pinned grammar)
- Per-symbol match rules come from config: importing the symbol, calling it as an
  attribute, binding an alias to it
- File fails to parse → excluded and counted

**Resolver** (pinned version, one environment per repo built from its lockfile,
internal packages installed at the pinned versions)
- Binds to the tracked symbol → `ast_resolved`
- Binds to something else → dropped and counted
- Can't bind → `ast_unresolved`
- String-URL and config pattern matches → `string_url`, `config`
- `confidence` = that detector's precision from the last benchmark, taken from config

**Ownership**
- Window: 180 days of history ending at `snapshot_at`
- Excluded: merge commits, bots (list in config), revs in `.git-blame-ignore-revs`
- Identities merged by `.mailmap` plus an alias table in config
- Share = the author's commits touching the file ÷ all commits touching the file
- Owner = the top author's team, from the roster in config
- Tie → the author with the most recent commit. Top share < 0.30 → `unowned`
- No commits in the window → use full history. Still none → `unowned`

**Ticket links**
- Commit → ticket: regex `[A-Z][A-Z0-9]+-\d+` in the message, and the key must exist
  in the snapshot
- Ticket → code: every rule that hits is recorded, not just the first:
  1. files touched by the ticket's linked commits
  2. an exact file path in the ticket text
  3. stack-trace frames that resolve to a file in the map
  4. an exact symbol name in the text that exists in the map
- No hit → `unmapped`. No embeddings, no fuzzy matching

**SZZ**
- Bug-fix commit = linked to a ticket of type Bug with resolution Fixed
- Blame the lines the fix changed, at the fix's parent, ignoring whitespace-only and
  comment-only lines
- Bug-introducing commits = the ones that last touched those lines, before the ticket
  was created
- `bug_fixes` on a file = bug-fix commits touching it in 365 days

## 3. Join: call site identity

- New ID = hash(repo, symbol, path, enclosing scope, normalized line, occurrence index
  within scope)
- Normalized line = comments stripped, whitespace collapsed
- Each run matches the previous run's present rows to today's candidates, in order:
  1. git's line mapping from the previous SHA to the new one, with rename detection
     at a fixed threshold. Same symbol at the mapped line → same ID
  2. same (repo, symbol, scope, normalized line) anywhere in the repo. Ties broken by
     path, then line
  3. previous row unmatched → append `removed`. Candidate unmatched → new ID
- `effective_date` = date of the first first-parent commit where the change is true

## 4. Graph

**blocked_by** (A → B) only if all hold:
- A and B are present call sites with different owners
- A's receiver is assigned, as a local or a `self` attribute in scope, from a call
  that resolves to function F
- B sits inside F, directly or through at most 3 resolved calls
- Several assignments with different sources → blocked by all of them, sorted
- Receiver can't be resolved → `unknown`, counted in coverage

These are not blocks:
- A only imports a file that contains B
- A calls F and F contains B, but A never names the symbol. That's blast radius,
  not a call site

**Blast radius:** resolved invocations that reach the symbol, depth ≤ 3.

**Chains** between two open tickets:
- `depends_on`: a call site mapped to T1 is blocked by one mapped to T2
- `duplicates`: T1 and T2 map to at least one of the same call sites
- `collides`: they share a function (call-site mapping) or a file (file mapping), and
  they aren't duplicates
- untracked: a present call site that no ticket maps to
- A cycle in `depends_on` → flagged, never broken automatically

## 5. Rules and models

All thresholds come from config.

- **Risky:** `bug_fixes` ≥ 3
- **Stall:** a team has present call sites and no status change in 14 days
- **ETA:** remaining ÷ mean removals per day over the last 28 days. Fewer than 3
  removals → no ETA
- **Order:** topological sort over `blocked_by`. Waves = levels. Within a wave, sort by
  `bug_fixes` descending, then `call_site_id`
- **Fragility:** ships as the `bug_fixes` count. A model replaces it only if it beats
  the count on a time-split test (see 05). The artifact is pinned, its seed fixed, and
  it abstains on files with fewer than 5 commits

**Stall reasons**, ranked by the priority below, then by evidence count. This is
auditor policy v0. Later policies compete with it (section 9).

| # | Reason | Fires when |
|---|---|---|
| 1 | Upstream block | Any of the team's call sites has `blocked_by` present |
| 2 | Never notified | No ticket maps to any of the team's call sites |
| 3 | Ticket mismatch | Ticket is Done and a mapped call site is present |
| 4 | Orphaned owner | Top author has no commit in any ingested repo in 60 days |
| 5 | Semantics unclear | Call site sits inside an `except` block and has no eligible diff |
| 6 | Fragility freeze | Every remaining call site is risky and the team is committing elsewhere |
| 7 | Test gap | No test file (test globs in config) imports the call site's module |
| 8 | Competing load | Zero commits to call-site files, while total team commits are ≥ 75% of their 56-day average |

- Output: a rank and evidence rows, no probability
- Nothing fires → "reason unknown"

**Ticket checks**

| Check | Fires when |
|---|---|
| Unmapped | No ticket-to-code rule hit |
| No done condition | No checklist and no heading from the config list |
| Bug without repro | Type Bug, no stack trace, no code block, no steps heading |
| Spans teams | Maps to call sites owned by 2 or more teams |
| Duplicate | A `duplicates` chain exists |
| Wrong owner | Assignee's team ≠ the owner of most mapped call sites |
| Stale | No update in 30 days, and mapped code changed since then |

- Sub-tasks only get Duplicate and Wrong owner
- A model replaces a rule only if it beats the rule (see 05)

**Ticket maker:** one draft per (team, wave) covering that team's untracked call
sites, with `depends_on` taken from chains.

**Intake:** fingerprint = hash of the exception type plus the top 3 in-repo frames,
or of the normalized error string. Clusters are exact fingerprint matches. Reports
with no fingerprint go to an unclustered list.

## 6. LLM layer

- **Allowed:** writing prose from a fact set (briefs, reasons, rewrites, ticket
  drafts, solutions), and drafting diffs the codemod can't do
- **Not allowed:** deciding owners, order, reasons, chains, flags, or any number
- Model ID and prompt version are pinned. Output must match a JSON schema
- Sampling uses the lowest-variance settings the model supports
- Cache key = hash(model, prompt, facts). A cache hit means no API call
- Invalid output → 2 retries → template text
- LLM unavailable → template text. Every view still renders

**Diffs**
1. **Codemod first:** rename and signature patterns taken from the owner's own prior
   migration commits, applied deterministically
2. **LLM draft** only if the call site has the same AST shape as at least 2
   migrated examples in the repo, and isn't inside an `except` block
3. **Every diff must:** apply cleanly, parse, change only that call site's lines and
   imports, and have the new symbol resolve. Otherwise: no diff, flag instead
- Each diff is labelled `codemod` or `llm`

## 7. Grounding gate

- Every number, path, SHA, ticket ID, person, and team in LLM text must appear in its
  fact set
- Any miss → the whole text is replaced by the template. Nothing is stripped
  mid-sentence
- The fallback rate is published

## 8. Views

- Read-only against the DB. Every screen shows `run_id` and `snapshot_at`
- The web image has no parser dependency
- Coverage appears on every screen

## 9. Auditor learning

Auditors improve by competing. Each policy is a player with an Elo rating, and
ε-greedy decides which one users see.

**Policies**
- A policy is versioned, deterministic code that decides where the auditor looks, in
  what order, and within what budget
- Two jobs:
  - **Stall:** rank the stall reasons. v0 is the fixed catalog in section 5
  - **Explore:** walk the graph and nominate legacy code worth migrating that nobody
    has filed
- Explore signals, which each policy weights and orders differently:
  - a symbol with a deprecation marker (`DeprecationWarning`, `@deprecated`, or
    "deprecated" in its docstring) that still has call sites
  - two functions with the same signature, one gaining callers and one losing them
  - no commits in 2 years and fan-in ≥ 10
  - high `bug_fixes` together with high fan-in
- Explore budget: 200 graph expansions per run
- New policies are hand-written first. Later, an LLM reads lost matches and proposes
  policy code, and a human reviews it before it joins the pool. The LLM never runs in
  the decision path

**Choosing (ε-greedy)**
- For each task, the champion is shown with probability 1 − ε. Otherwise a uniformly
  random challenger is shown
- The draw is seeded from hash(run_id, task_id), so a rerun shows the same policy
- ε = 0.2 until the champion has 30 matches, then 0.1
- Explore: the champion and the chosen challenger both run. Their nominations are
  merged into one list by team-draft interleaving (seeded). The staff engineer accepts
  or rejects each nomination

**Matches**
- **Stall:** every policy runs in shadow on every stall, since rules are cheap. When
  the stall breaks and a verdict is set, every pair of policies plays a match. Ranking
  the true reason higher wins; the same rank is a draw
- **Explore:** the policy whose nominations get more accepts wins. Equal is a draw. No
  response in 14 days means no match
- **Replay:** a new policy plays every frozen past stall before it goes live, and sees
  only rows with `known_at` ≤ the stall's time. Replay matches count at half K. The idea
  comes from Dream-RSI (Zheng et al., 2026,
  [arXiv:2609.14858](https://arxiv.org/html/2609.14858v1)); applying it to auditors is
  ours

**Verdicts**, set by rule where possible:

| Reason | Confirmed when |
|---|---|
| Upstream block | The blocking call site was removed ≤ 7 days before the team's first removal after the stall |
| Never notified | A ticket mapping to the team's call sites was created ≤ 14 days before the team resumed |
| Orphaned owner | The first removals after the stall came from someone other than the previous top author |
| Ticket mismatch | The closed ticket was reopened, or the resuming commits reference it |
| Any other | A human picks what unblocked it, and who decided is logged |

- No verdict → no match

**Elo**
- Start at 1500. Expected score = 1 / (1 + 10^((R_b − R_a) / 400))
- K = 32 for a policy's first 30 matches, then 16
- Ratings are recomputed from the full match log on every run, in (decided_at,
  task_id) order. Same log → same ratings
- Ratings are per repo once that repo has 30 matches, global before that. This is how
  it adapts to each codebase
- Promotion: a challenger replaces the champion only if it has ≥ 30 live matches,
  leads by ≥ 50 Elo, and beats v0 head to head. Every promotion is logged with its
  match record

## 10. Push check (early detection)

Runs on every push and PR. Catches problems before the nightly run sees them.

**Input**
- Base = the last published nightly map, exported as one SQLite file
- Snapshot = (base `run_id`, push SHA). Jira comes from the base snapshot, never
  queried live
- Scope = changed files plus files that import them, from the base map. Over 200
  files → skip, post "too large, nightly covers it"

**Run:** Extract, Join, and Graph on the scope only. Output = the diff between the
base rows and the push rows for that scope.

**Checks** (rules, thresholds in config)

| # | Check | Fires when |
|---|---|---|
| P1 | Drift | A new call site to a tracked symbol |
| P2 | New legacy use | A new call to a symbol with a deprecation marker (section 9 signals) |
| P3 | Collision | The push edits a function mapped to an open ticket owned by another team |
| P4 | New block | The push creates a `blocked_by` edge, or moves a block onto another team |
| P5 | Ticket can move | The push removes the last present call site mapped to a ticket that isn't Done |
| P6 | Ticket mismatch | The push re-adds a call site mapped to a Done ticket |
| P7 | Risky edit | The push changes a call site in a file with `bug_fixes` ≥ 3 |
| P8 | Bad key | The commit message names a ticket key not in the snapshot |
| P9 | Defect risk | Just-in-time score above threshold. Ships as lines changed × files touched unless a model beats it (05) |

**Output**
- One check run plus one PR comment. Templates only, LLM off, so each push costs $0
- Sorted by check number, then path, then line. At most 10 lines, then "and N more"
- Never blocks a merge. Status is always neutral
- Each alert is stored with its outcome: fixed in a later commit, dismissed, or ignored

**Limits**
- One repo per push. A push that breaks another repo's resolution shows up only
  nightly, and the comment says so
- The base map can be up to 24 h old. Two pushes on the same day don't see each other

**Consistency:** the nightly run must contain every row the push check produced for
the merged SHA. A mismatch is a bug, logged with both rows.

## One call site: `cart/pricing.py:214`

1. **Collect:** checkout repo at a known SHA, plus a Jira snapshot
2. **Extract:** candidate `self.auth.verify_token(tok)`. The resolver binds it
   (`ast_resolved`). r.mehta has a 62% share, so the owner is checkout. 4 bug fixes in
   365 days (BUG-4471, BUG-4102, BUG-3988, BUG-3771)
3. **Join:** `cs_8812`, `present`
4. **Graph:** `self.auth` is assigned from `shared.client.get_auth()`, Platform-owned,
   which still calls the old path → `blocked_by` is set
5. **Rules:** risky. Sits inside `except AuthError`, so no diff is eligible. Stall
   reasons would be upstream block (#1) and semantics unclear (#5)
6. **LLM:** writes the brief line from those facts
7. **Gate:** "4 bug fixes" matches 4 ticket IDs → passes
8. **Views:** one of Checkout's 9, flagged as risky and blocked
- **Later:** two lines added above it → it moves to 216, still `cs_8812`. Once
  migrated → a `removed` row is appended, and the burndown goes from 31 to 32

## Daily loop

- 02:00 UTC: collect → parse changed files plus files that depend on them → join →
  graph → rules → LLM (cached) → gate → publish the run
- Sunday: full rebuild from raw, which must equal the incremental result

## Infra

| What | Where |
|---|---|
| Pipeline | Cloud Run Jobs, one per repo |
| Push check | GitHub Actions (free on public repos and the student plan) |
| Schedule | Cloud Scheduler |
| Site and API | Cloud Run |
| Database | SQLite on GCS until there's a user, then Cloud SQL (the only always-on bill) |
| Snapshots, LLM cache | GCS |
| Alerts | Cloud Monitoring |

- A Cloud Run Job's local disk is held in memory, so size each job's memory from its
  last peak
- Billing alert on day 1

## Failure behavior

| Failure | Behavior |
|---|---|
| Jira unreachable | Map from code only, run flagged partial |
| Force-push | Halt that repo |
| Parse failure | File excluded, counted, reported |
| No ticket-to-code links | Map and ownership work. Risk and chains are off, and we say so |
| Rate limited | Checkpoint, resume, no partial publish |
| Incremental ≠ full rebuild | Halt, keep the last good run live |
| No base map yet | Push check posts nothing |
| Push check ≠ nightly | Nightly wins, mismatch logged |
