# Bias · data slicing on run 70dd1dabfed544b8

`python -m meridian.bias --run-id 70dd1dabfed544b8 --config meridian.toml` → `out/<run_id>/bias.json`. Slicing uses Fairlearn's `MetricFrame`. Thresholds in [settings.toml](settings.toml) `[bias]`: a slice is **judged** when it has **N ≥ 30** rows, and **flagged** when its whole Wilson 95% interval sits more than **0.10 below** the overall value (`ci_high < overall − 0.10`). A slice under N = 30 is unjudged: neither flagged nor cleared. Every slice in `bias.json` carries `n`, `ci_low`, `ci_high` and `flagged`; every slicing carries how many slices it judged (`tested`) and Fairlearn's `difference()` (max − min) and `ratio()` (min / max) over the judged slices.

**Where the numbers come from.** 70dd's `bias.json` was computed before this version of `bias.py`. Ticket mapping and resolver below are recomputed with the current code from 70dd's `meridian.sqlite` (the per-ticket overall reproduces 0.608 on N = 59,491). Owner-rule numbers are from 70dd's `bias.json`, with the unassigned exclusion derived exactly from its counts; the owner rule's per-slice numbers on the new definition, the prior-commits slice and the held-out check need the rerun (marked *rerun*).

## What gets sliced, and why these

There is no trained model in step 1. The pipeline's "models" are its rules, and three of them decide what each team sees on screen. Each has a right answer to check against or a rate that should not depend on who you are:

| Rule | Metric | Slices (the "groups") | Why it matters |
|---|---|---|---|
| Owner rule (Spark) | Does the owner team predict the next author's team? (week 1's test), on next authors with a team · coverage: share of files owned, share of next authors with a team | top-level module (`sql/`, `core/`, …) · the next author's team · the next author's commits before the cut (0, 1, 2–10, >10) | A team the rule gets wrong is sent the wrong work; an unowned module is nobody's; newcomers shouldn't be served worse than regulars |
| Ticket → code mapping (Spark Jira) | share of tickets with a code target | component (a ticket counts once in each of its components) · issue type · year filed · component on tickets since 2020 | An unmapped team looks idle: "never notified", no chains |
| Resolver (Django, packages) | share of edit-role candidates kept as call sites | repo | A repo the resolver drops more in would show less work left |

No slices by email domain or any other personal attribute (privacy). The people-level slice is commit count only.

## What we found

**1. Owner rule: 0.496 where it can be scored; a quarter of next authors can't be.** 70dd's `bias.json` reports 0.373 (N = 988) and `unassigned` at 0.000 on N = 244. That 0.000 is true by construction, not a finding: `team_owner` never names `unassigned`, and the same `person_teams` (same lookback) builds both the prediction and the label, so an unassigned next author is a miss under every variant. `bias.py` now scores accuracy only on next authors with a team, **0.496 (N = 744, 95% CI 0.460–0.532)**, and reports the unassigned rate as coverage on its own (24.7% of owned files' next authors; `assigned_share_by_module`). Still flagged on 70dd under the CI rule: team `Project Infra` 0.000 on **exactly N = 30** (CI 0–0.114; borderline: one file fewer and it would be unjudged) and module `core/` 0.167 (N = 78, CI 0.100–0.265). Per-module and per-team accuracy without the unassigned, and the prior-commits slice: *rerun*.

**2. Some modules are mostly unowned.** Owned share overall 0.543 (N = 1,819); `examples/` 0.028 (CI 0.011–0.070), `mllib/` 0.149 (0.091–0.235), `common/` 0.281 (0.186–0.401): all flagged under the CI rule. Their authors are the same unassigned people.

**3. Ticket mapping depends mostly on age, partly on where the code lives.** 10,647 tickets (18%) have several components. 70dd's `bias.json` sliced on the first component only. `bias.py` now counts a ticket in each of its components, so the component overall is over (ticket, component) pairs: 0.612 (N = 71,845); per ticket it stays 0.608 (N = 59,491).
- All time: 11 of 36 judged components flagged: EC2 0.083, Spark Docker 0.209, Mesos 0.287, `(none)` 0.334, Input/Output 0.348, Java API 0.369, Kubernetes 0.389, Spark Submit 0.392, GraphX 0.414, Spark Shell 0.419, Deploy 0.447. Kubernetes all time is 0.375 by first component (70dd) and 0.389 counted in every component.
- By year, 5 of 18 flagged: tickets filed before 2014 map under 25% of the time (their files are gone by the pinned SHA); 2014 on, 0.52–0.72.
- **Since 2020 (overall 0.676, N = 35,285), EC2 and Mesos do not clear: they are unjudged** (N = 8 and N = 17; first component only, N = 8 and 14). Holding age fixed still leaves **8 of 32 judged components flagged**: Spark Docker 0.209 (N = 86), Input/Output 0.315 (92), Spark Submit 0.325 (123), Java API 0.333 (81), Spark Shell 0.344 (64), Examples 0.354 (79), Kubernetes 0.372 (1,762), Deploy 0.426 (61). Under the old point-estimate rule on the first component, 12 were flagged since 2020, including Connect 0.563 (N = 3,163). Connect now reads 0.566 (N = 3,194, CI 0.549–0.583): 0.11 below overall, but its interval reaches the 0.576 line, so it isn't flagged. Kubernetes and Spark Docker work is partly committed in Apache repos the snapshot doesn't hold (`spark-docker`, `spark-kubernetes-operator`). Input/Output, Java API, Spark Submit and Examples have no such explanation yet.
- `(none)`: 1,382 tickets with no component, 0.334 (CI 0.310–0.360). All were filed 2017 or earlier (276 in the 0010–0012 import), none since 2020, so this is the age effect again. They have no team, so no team view would show them either way.
- Issue types without code (Wish 0.117, Brainstorming 0.118, Question 0.161) are low by nature: 3 of 13 judged types.
- Below overall with confidence but within 0.10, so not flagged: MLlib 0.545 (N = 1,425, first component), New Feature 0.518 (N = 4,505), years 2014, 2017, 2018.

**4. The resolver treats every repo alike.** Kept share 0.981 overall (N = 1,556), 0 of 12 judged repos flagged. The lowest judged repo is jazzband/django-silk, 0.933 on N = 30 (CI 0.787–0.982). django-tenants (0.857, N = 7) is below N = 30, so it is unjudged.

**How many slices.** Ticket mapping and resolver: 111 slices judged, 27 flagged. Owner rule on 70dd: 17 judged, 6 flagged; the rerun adds the assigned-share and prior-commits slicings (`bias.json` `slices_tested`, `slices_flagged`). The CI rule is per slice, not corrected for multiple comparisons. A Bonferroni check over 70dd's 125 judged slices keeps the large gaps (unassigned, Project Infra, `examples/`, `mllib/`, EC2, Mesos, Kubernetes, Spark Docker, Input/Output, Java API, old years, no-code types) but not `core/`, `common/`, Deploy (all time and since 2020), Spark Shell or all-time Spark Submit. Treat those as likely, not established.

**Fairlearn spread over judged slices** (`difference` / `ratio`): component 0.690 / 0.107, component since 2020 0.608 / 0.256, type 0.645 / 0.153, year 0.692 / 0.033, resolver by repo 0.067 / 0.933. Owner rule: *rerun*.

**Also found (a data anomaly, not bias):** 289 Spark tickets are dated in the years 0010–0012. Apache Jira itself has those dates (the 2013 import from Spark's old tracker). `meridian.validate` now warns on dates before 2000 or after the snapshot.

## Mitigation

Each candidate fix for the owner rule is scored on the same files (`bias.json`, `owner_rule.mitigations`). Accuracy on next authors with a team is derived from 70dd's counts. The worst module gap is 70dd's (unassigned counted as misses); on the new definition it is *rerun*.

| Variant | Files owned | Accuracy (owners.py, unassigned = miss) | **Accuracy, next authors with a team** | Next author unassigned | Worst module gap (70dd) |
|---|---:|---:|---:|---:|---:|
| as configured: `min_share` 0.30, teams from 180 days | 988 | 0.373 | **0.496** (N = 744) | 24.7% | 0.207 |
| `min_share` 0.20 / 0.10 (adjusting the decision threshold) | 992 | 0.372 | 0.493 (N = 748) | 24.6% | 0.205 |
| teams from 365 days | 989 | 0.439 | 0.538 (N = 806) | 18.5% | 0.234 |
| **teams from 730 days** | **989** | **0.458** | **0.545** (N = 831) | **16.0%** | **0.202** |
| fallback to the last toucher's team when unowned | 1,634 | 0.303 | 0.395 (N = 1,255) | 23.2% | 0.278 |

- **Lowering the threshold does nothing**: the unowned files are unowned because their authors have no team, not because shares are close to 0.30.
- **Falling back to the last toucher** owns 65% more files but is wrong more often (0.395 on next authors with a team) and widens the module gap: it trades accuracy for coverage.
- **A longer lookback for a person's team helps, but less than 0.373 → 0.458 suggests.** Each variant is scored against its own labels: the 730-day lookback also gives a team to 86 next authors who were unassigned, and every one of those was a guaranteed miss before. Without them the gain is **0.496 → 0.545 (+0.049)**, with overlapping intervals (0.460–0.532 vs 0.511–0.579). Coverage is the bigger win: unassigned next authors 24.7% → 16.0%. Trade-off: a team from two years ago can be stale for someone who moved.
- **Held-out check (*rerun*).** `bias.py` now picks the best variant (accuracy on next authors with a team) at a cut one window earlier, 2025-10-06, with next authors limited to before the current cut, so the pick never sees the answers it is checked on. It then scores the pick against the configured rule on the current cut, 2026-04-04: the delta, with a 95% interval from a seeded paired bootstrap over files (1,000 resamples, seed 0). `bias.json` `owner_rule.held_out`.

**Applied in the shipped output (no new run_id).** The views get the caveat now: every slice in `bias.json` carries `n`, `ci_low`, `ci_high` and `flagged`. The coverage slicings are `owned_share_by_module`, `assigned_share_by_module` and `mapped_share_by_component` (and `_since_2020`). A view shows "low coverage" next to a module, team or component whose slice is flagged, and "too few to judge" when `n < min_n`, so an unowned module isn't read as nobody's work and an unmapped component isn't read as idle. `meridian.publish` copies `bias.json` beside the published block as `<run_id>[.rebuild].bias.json` (bias runs in the weekly rebuild).

**Next-run change (team decision):** give a person's team its own lookback, `[teams.spark] lookback_days = 730`, separate from the 180-day ownership window. Validated on 70dd: next authors with a team 75.3% → 84.0%, accuracy on them 0.496 → 0.545, worst module gap 0.207 → 0.202. It changes `meridian.toml` and `owners.py`, so it means a new team run_id. Adopt it only if the held-out delta's interval on the rerun stays above 0.

**Ticket mapping:** no fuzzy matching is allowed (04-architecture), so the fix is coverage, not guessing:
- Add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot so their keyed commits map (a new run_id). That should close much of the Kubernetes and Docker gap; the next bias run will show how much.
- "Never notified" (stall reason #2, stage 5) should treat a component whose `mapped_share` slice is flagged as unknown rather than unnotified.

Trade-offs are on record in `bias.json`, rerun on every run. `tests/test_bias.py` pins the rules on a synthetic history: the CI flag rule, a slice at exactly N = `min_n`, multi-component tickets, the prior-commits slice and the held-out scoring.
