# Test

- Pass bars fixed before the run. Changing one needs a commit saying why
- Every metric has its N. Split by time, never random
- A model ships only if it beats its baseline
- Phase 2's bars, each owner's rows and their results: [PASS-BARS.md](../Model-Pipeline/PASS-BARS.md)

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
| Determinism | Same snapshot twice; rebuild vs incremental; warm cache | 0 diffs, 0 LLM calls. Warm cache met: reruns make 0 calls, byte for byte |
| Candidates | Goldens: direct, alias, attribute, re-export, `getattr` (expected miss) | All |
| Resolver | Django 265; one method-call deprecation | Recall ≥ 0.95, precision ≥ 0.98 |
| Call site ID | Fixtures: insert, move, rename, duplicate, split. Django replay | 100%, replay errors ≤ 1% |
| Burndown | Django weekly replay vs the conversion commit's changed lines (grep over-counts by 19) | Equal, 0 at conversion |
| Ownership | Spark: owner vs next author. Baselines: last toucher, CODEOWNERS | Beats last toucher |
| Ticket links | Spark, hand-check 50 | Precision ≥ 0.95 |
| Ticket → code | Spark: hide fix commits, map from text | Path, trace precision ≥ 0.90 |
| SZZ, fragility | Defects4J + Fonte (130 bug-inducing commits): SZZ right 44%. 20 positives checked: 9 real, bar 16, missed. Train 2015–22, val 2023, test 2024 once. Baselines: `bug_fixes`, churn | Beats both. Met on val: PR-AUC 0.4067 vs churn 0.316, prior bug fixes 0.222. It also beats lines × files on 2024: 0.268 vs 0.214, paired +0.054 [+0.028, +0.081] |
| Blocking, chains | Fixtures; Spark same-function pairs in 30 days; GitLab gold set (262 pairs), spot-checked by a person | 100%, precision ≥ 0.90 |
| Rules | Fixture per rule; order is topological; ETA error reported | Exact |
| Stall reasons | GitLab: 57 blocked issues with written reasons; then logged verdicts | No claim below N = 30 |
| Auditor | Elo simulation with known skill; recompute twice; seeded ε ± 2% over 10,000; `known_at` leak; promotion guard | Ranks right, 100%. Guard: luck promotes a no-better policy 4.0% of the time (was 87%) |
| Ticket checks | Spark 145 Incomplete, 534 Cannot Reproduce vs Fixed; GitHub needs-info | Beats description length |
| Duplicates | Spark Duplicates | Beats title similarity |
| LLM | LLM off | Every view renders. Met (fault 15) |
| Router (Jev) | Same problem twice; Jev down; injected failure per agent. Baseline: rules pick | Same pick, 0 calls on rerun. Beats rules on cost at equal pass rate. No model did (rules 29 of 30, $0), so the rules route. Jev down → rules pick (fault 17) |
| Gate | 200 injected errors, 200 clean | 100% caught, ≤ 5% false rejects. Met: 2,000 of 2,000 over 10 seeds, 0 false rejects |
| Diffs | Django: draft from parent vs real after-line. Packages: the 12 finished migrations | ≥ 90% match. Django 95.1%, met. Packages 73.2%, missed |
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
| P9 vs SZZ, time split | Spark | Beats lines × files. The model ranks better (2024 PR-AUC 0.268 vs 0.214) but can't alert at precision 0.80 (best 0.62 on 42 val alerts), so P9 keeps lines × files |
| One scripted push per check + a 201-file push | ~20 generated, no LLM | Exact |
| Alerts per push | Replay | Median ≤ 1, precision ≥ 0.80. Median met: 0 on the 21 migration commits (7 alerts, 19 pushes checked). Precision missed: 0.333 on 45 alerts (60 real 2023 Spark pushes) |
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
