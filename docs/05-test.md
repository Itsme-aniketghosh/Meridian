# Test

- Pass bars fixed before the run. Changing one needs a commit saying why
- Every metric has its N. Split by time, never random
- A model ships only if it beats its baseline

| Level | When |
|---|---|
| Unit fixtures, one per rule | Every commit |
| Golden: frozen snapshot → exact output | Every commit |
| Benchmark: Django, Spark, cross-repo | Weekly |
| Outcome: used, edited, ignored | Continuous |

## Components

| Component | Test | Pass |
|---|---|---|
| Collect | Fixture repo + mock Jira; force-push; kill and resume | 100% |
| Determinism | Same snapshot twice; rebuild vs incremental; warm cache | 0 diffs, 0 LLM calls |
| Candidates | Goldens: direct, alias, attribute, re-export, `getattr` (expected miss) | All |
| Resolver | Django 265; one method-call deprecation | Recall ≥ 0.95, precision ≥ 0.98 |
| Call site ID | Fixtures: insert, move, rename, duplicate, split. Django replay | 100%, replay errors ≤ 1% |
| Burndown | Django weekly replay vs the conversion commit's changed lines (grep over-counts by 19) | Equal, 0 at conversion |
| Ownership | Spark: owner vs next author. Baselines: last toucher, CODEOWNERS | Beats last toucher |
| Ticket links | Spark, hand-check 50 | Precision ≥ 0.95 |
| Ticket → code | Spark: hide fix commits, map from text | Path, trace precision ≥ 0.90 |
| SZZ, fragility | Defects4J + Fonte (130 bug-inducing commits); 50 hand labels; train < 2025, test 2025. Baselines: `bug_fixes`, churn | Beats both |
| Blocking, chains | Fixtures; Spark same-function pairs in 30 days; GitLab: 30+ cross-team blocks, spot-checked by a person | 100%, precision ≥ 0.90 |
| Rules | Fixture per rule; order is topological; ETA error reported | Exact |
| Stall reasons | GitLab: 57 blocked issues with written reasons; then logged verdicts | No claim below N = 30 |
| Auditor | Elo simulation with known skill; recompute twice; seeded ε ± 2% over 1,000; `known_at` leak; promotion guard | Ranks right, 100% |
| Ticket checks | Spark 145 Incomplete, 534 Cannot Reproduce vs Fixed; GitHub needs-info | Beats description length |
| Duplicates | Spark Duplicates | Beats title similarity |
| LLM | LLM off | Every view renders |
| Router (Jev) | Same problem twice; Jev down; injected failure per agent. Baseline: rules pick | Same pick, 0 calls on rerun. Beats rules on cost at equal pass rate |
| Gate | 200 injected errors, 200 clean | 100% caught, ≤ 5% false rejects |
| Diffs | Django: draft from parent vs real after-line | ≥ 90% match |
| Maker | Each untracked site in exactly one draft | Property holds |
| Intake | Dedupe vs GitHub duplicates; personal-data fixture | 0 personal data stored |
| Views | Frozen DB snapshots; every number has a row | 100% |
| End to end | Demo snapshot → golden in CI | Identical |

## Push check

Replay each first-parent commit as a push against its parent's map.

| Test | Data | Pass |
|---|---|---|
| Push output = map(C) − map(C^) | Django, 500 commits | 0 diffs |
| P1 vs `git log -S ugettext` after deprecation | Django | Recall 100%, precision ≥ 0.95 |
| P3 vs same-function fix pairs in 30 days | Spark | Report, hand-check 50 |
| P5 vs ticket resolved in 14 days | Spark | Report |
| P9 vs SZZ, time split | Spark | Beats lines × files |
| One scripted push per check + a 201-file push | ~20 generated, no LLM | Exact |
| Alerts per push | Replay | Median ≤ 1, precision ≥ 0.80 |
| Runtime | Free runner | p95 ≤ 5 min |

## Week-1 checks

| Check | Decides |
|---|---|
| `url()` migration commit in 20 packages | ≥ 12 corpus · 5–11 partial · < 5 cross-repo untested |
| pyright or Jedi on 542 aliased `_()` | Resolver, blast radius |
| Per-repo envs with internal packages | Cross-repo resolution |
| Method-call benchmark | Resolver number means anything |
| Time to hand-verify the 265 | Week-4 freeze |
| Collision pairs in Spark | Chains testable |
| Bulk-closed share of Duplicates | Duplicate labels usable |
| 50 Django pushes timed | Free runner fits |
| Jira "open at time t" | P3, P5 testable |
| Matches per month vs simulation need | Elo moves in 10 weeks |
