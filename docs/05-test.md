# Test

## Rules

- Pass bars are set here, before the run. Changing one takes a commit that says why
- Every metric carries its N
- Split by time, never at random
- Recall is quoted against the edit unit (279) and broken down by detector
- Report calibration, not accuracy. A model that abstains ships with its answer rate
- A model ships only if it beats its baseline. Otherwise the baseline ships

## Levels

| Level | What | When |
|---|---|---|
| Unit | Hand-built fixtures, one per rule | Every commit |
| Golden | Frozen demo snapshot → exact expected output | Every commit |
| Benchmark | Django, Spark, cross-repo corpus | Weekly and on release |
| Outcome | Real use: applied, filed, ignored | Continuous |

## By component

**Collect**
- Fixture repo and mock Jira → complete snapshot manifest, stable ordering
- Force-push fixture → halt
- Kill mid-run, then resume → same snapshot as a clean run
- Pass: 100%

**Determinism**
- Same snapshot run twice → byte-identical DB dump
- Weekly full rebuild vs incremental → identical
- Rerun with a warm cache → 0 LLM calls
- Pass: 0 differences

**Candidate extraction**
- Golden snippets: direct call, `import ... as`, attribute call, re-export, `getattr`
  (expected miss), string URL
- Every parse failure counted
- Pass: all goldens

**Resolver**
- Django `ugettext*`: 279 ground-truth lines, frozen in week 4
- A method-call deprecation (`self.x.method()`), chosen in week 1
- Recall and precision per detector. Django's own misses reported separately
- Pass: recall ≥ 0.95 on the edit unit, `ast_resolved` precision ≥ 0.98

**Call site identity**
- Edit fixtures: lines inserted above, function moved to another file, file renamed and
  edited, duplicate lines, file split
- Django replay: a `removed` and a `new` row with the same normalized line in the same
  week counts as an identity error
- Pass: fixtures 100%, replay errors ≤ 1%

**Burndown**
- Replay Django weekly, from before the deprecation to the conversion commit
- Independent check: a grep count at each step (it equals the edit set for this symbol)
- Pass: equal at every step, 0 at the conversion commit

**Ownership**
- Fixtures: mailmap merge, bot exclusion, ties, the unowned threshold
- Spark backtest: owner at time t vs the author of the file's next commit
- Baselines: last person to touch the file, and CODEOWNERS where it exists
- Pass: beats last-toucher, or last-toucher ships

**Ticket ↔ commit links**
- Spark: key regex over commits, then hand-check 50 links
- Pass: precision ≥ 0.95

**Ticket → code mapping**
- Spark tickets that have fix commits: hide the commits, map from the text alone,
  compare with the files the fix touched
- Precision and recall per method (path, stack trace, symbol)
- Pass: path and stack-trace precision ≥ 0.90

**SZZ and fragility**
- SZZ: bug-fix commits identified vs Defects4J, and 50 bug-introducing commits
  hand-labelled
- Fragility: train on data before 2025, test on 2025. Baseline is the `bug_fixes`
  count. Report calibration and answer rate
- Pass: the model beats the baseline on the test year, or the baseline ships

**Blocking and chains**
- Fixtures: receiver from another team's factory → blocked. Import only → not.
  Wrapper → blast radius. Unresolved → `unknown`. Cycle → flagged
- Spark: hide parent links and rebuild sub-task relations (sanity check)
- Spark history: ticket pairs whose fix commits touched the same function within 30
  days = collision labels
- Cross-repo corpus: 30 `blocked_by` edges hand-labelled
- Pass: fixtures 100%, hand-labelled precision ≥ 0.90

**Rules: stall, ETA, order, reasons**
- One fixture per rule → fires exactly on its spec
- Order: property test that the output is a valid topological order. Suggested vs
  actual landing order reported as Kendall tau
- ETA: replay error vs the actual end date, reported
- Reasons: each one logged when shown, with a verdict when the stall breaks. No
  accuracy claim below N = 30

**Auditor learning**
- Simulation: policies with known true skill play synthetic matches, and Elo must rank
  them correctly. Report how many matches that takes, which tells us when the ratings
  start to mean anything
- Recompute ratings from the match log twice → identical
- Seeded ε: a rerun shows the same policy. Over 1,000 synthetic tasks the challenger
  share is within ε ± 2%
- Interleaving: a fixture with a known better policy → it wins more than half its matches
- Replay leak: a policy that reads a row with `known_at` after the task → the run fails
- One fixture per verdict rule
- Promotion: a challenger with < 30 matches or a lead < 50 is never promoted
- Pass: simulation ranks correctly, determinism 100%, leaks caught 100%

**Ticket checks**
- One fixture per check
- Labels: Spark's 145 non-bulk Incomplete and 534 Cannot Reproduce, against Fixed.
  GitHub needs-info and needs-repro. Spot-check 50 from each source
- Baseline: description length
- Pass: a model ships only if it beats length

**Duplicates**
- Spark Duplicate tickets (bulk-closed share checked first): does call-site overlap
  find the original?
- Baseline: title similarity
- Pass: beats title similarity

**LLM layer**
- Schema-valid rate and fallback rate, reported
- LLM disabled → every view still renders from templates
- Pass: complete views with the LLM off

**Grounding gate**
- 200 outputs with injected wrong numbers, paths, IDs, and names; 200 clean ones
- Pass: catches 100% of injected errors, false rejects ≤ 5%

**Diffs**
- Django conversion commit: draft each call site from the parent, compare with the
  real after-line
- Exact match, applies, parses, refusals. Reported separately for `codemod` and `llm`
- Pass: ≥ 90% of drafted diffs match or are equivalent
- Outcome: applied clean, with edits, or ignored

**Ticket rewrite and maker**
- Every added fact is in the fact set (gate)
- Maker property test: each untracked call site appears in exactly one draft, with one
  draft per (team, wave)
- Outcome: filed unchanged, with edits, or ignored

**Intake**
- Fingerprint dedupe vs GitHub issues closed as duplicate
- Stack trace → file on Spark tickets that have both a trace and a fix commit
- Personal-data fixture (emails, tokens) → nothing stored
- Pass: personal data 100%

**Push check**

All on replayed history. Each first-parent commit is replayed as a push against the
map at its parent. No new data needed except the fixture repo.

| Test | Data | Pass |
|---|---|---|
| Equivalence: push output = full map at C minus full map at C^, for the scope | Django, 500 commits | 0 differences |
| P1 drift: alerts vs commits that add `ugettext*` after its deprecation (independent: `git log -S`) | Django | Recall 100%, precision ≥ 0.95 |
| P3 collision: alerts vs ticket pairs whose fix commits touched the same function within 30 days | Spark + Jira | Report precision and recall, hand-check 50 |
| P5 ticket can move: alerts vs the ticket being resolved within 14 days | Spark + Jira | Report, with N |
| P8 bad key: keys vs the Jira snapshot | Spark | 100% on fixtures |
| P9 defect risk: SZZ labels, train before 2025, test on 2025 | Spark, Defects4J sanity | Beats lines × files, or that ships |
| Fixture repo: one scripted push per check, plus a 201-file push | Generated, about 20 pushes, no LLM | Each fires exactly on its spec |
| Noise: alerts per push | Django and Spark replay | Median ≤ 1. Hand-check 50, precision ≥ 0.80 |
| Runtime on a free runner | Django | p95 ≤ 5 min |

**Views**
- Snapshot tests from a frozen DB
- Automated check that every on-screen number has a row reference
- Coverage block present on every screen
- Web image has no parser dependency (build check)

**End to end**
- Frozen demo snapshot → golden output, in CI on every change
- Pass: identical, or the golden file updated in the same commit with a reason

**Infra**
- Billing alert on day 1
- Peak memory and runtime logged per repo job, alert at 80% of the limit

## Week-1 checks

| Check | Decides |
|---|---|
| 20 packages: find each one's `url()` migration commit | ≥ 12: build the corpus. 5 to 11: blast radius and ownership only. < 5: drop it and say cross-repo is untested |
| Can pyright or Jedi bind the 480 aliased `_()` calls? | Which resolver, and whether blast radius ships |
| Build per-repo environments with internal packages | Whether cross-repo resolution works at all |
| Pick a method-call deprecation benchmark | Whether the resolver number means anything for `verify_token`-style calls |
| Time to hand-verify the 279 | Whether the week-4 freeze holds |
| Count collision and dependency pairs in Spark history | Whether chains can be tested before a customer |
| Bulk-closed share of Spark Duplicates | Whether the duplicate labels are usable |
| Replay 50 Django commits as pushes, time each | Whether the push check fits a free runner |
| Can the Jira snapshot answer "open at time t" from created and resolved dates? | Whether P3 and P5 can be tested on Spark |
| Matches per month expected from the Django replay (stalls plus nominations) vs the number the simulation needs | Whether Elo can move within 10 weeks, or learning starts with explore only |
