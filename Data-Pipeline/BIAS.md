# Bias: data slicing on run 70dd1dabfed544b8

`python -m meridian.bias --run-id 70dd1dabfed544b8 --config meridian.toml` writes `out/<run_id>/bias.json`, slicing with Fairlearn's `MetricFrame`. The thresholds are in [settings.toml](settings.toml) `[bias]`. A slice is judged when it has N ≥ 30 rows. It's flagged when its whole Wilson 95% interval (a confidence interval for a share that still behaves on small samples) sits more than 0.10 below the overall value (`ci_high < overall − 0.10`). A slice under N = 30 is unjudged: neither flagged nor cleared. Each slicing also records how many slices it judged (`tested`) and Fairlearn's `difference()` (max − min) and `ratio()` (min / max) over the judged slices.

Every number below comes from `bias.py` as it is now, rerun on 70dd on 2026-10-26 on the outputs of the Airflow run in the report's Appendix A.

## What we slice, and why

There's no trained model at this stage. Three of the pipeline's rules decide what each team sees, and each has a right answer to check against or a rate that shouldn't depend on who you are:

| Rule | Metric | Slices (the "groups") | Why it matters |
|---|---|---|---|
| Owner rule (Spark) | Does the owner team predict the next author's team? (week 1's test), on next authors with a team · coverage: share of files owned, share of next authors with a team | top-level module (`sql/`, `core/`, …) · the next author's team · the next author's commits before the cut (0, 1, 2–10, >10) | A team the rule gets wrong is sent the wrong work. An unowned module is nobody's. Newcomers shouldn't be served worse than regulars |
| Ticket → code mapping (Spark Jira) | share of tickets with a code target | component (a ticket counts once in each of its components) · issue type · year filed · component on tickets since 2020 | An unmapped team looks idle: "never notified", no chains |
| Resolver (Django, packages) | share of edit-role candidates kept as call sites | repo | A repo the resolver drops more in would show less work left |

We don't slice by email domain or any other personal attribute, for privacy. The only people-level slice is commit count.

## What we found

### 1. Owner rule: 0.496 where it can be scored, but a quarter of next authors can't be

Counting every next author gives 0.373 (N = 988), with `unassigned` at 0.000 on N = 244. That 0.000 is true by construction: `team_owner` never names `unassigned`, and the same `person_teams` (same lookback) builds both the prediction and the label, so an unassigned next author is a miss under every variant. So `bias.py` now scores accuracy only on next authors with a team: 0.496 (N = 744, 95% CI 0.460–0.532). The unassigned rate is reported on its own as coverage (24.7% of owned files' next authors, `assigned_share_by_module`). Without the unassigned:
- By module, 3 of 11 judged, 1 flagged: `core/` 0.228 (N = 57, CI 0.138–0.352). Counting the unassigned as misses it read 0.167 (N = 78), so part of `core/`'s gap was the circular class, but not all of it. `sql/` 0.519 (N = 393) and `python/` 0.509 (N = 232) sit on the overall line.
- By the next author's team, 6 of 12 judged, 1 flagged: `Project Infra` 0.000 on exactly N = 30 (CI 0–0.114). That's borderline, since one file fewer and it would be unjudged. The highest is Structured Streaming 0.804 (N = 51). Build (0.328, N = 116) and Spark Core (0.312, N = 64) are low, but their intervals reach the flag line.
- By the next author's commits before the cut, 2 of 3 judged, 0 flagged: 2–10 commits 0.560 (N = 50), more than 10 0.493 (N = 686). One commit (0.375, N = 8) is too few to judge, so "newcomers aren't served worse" only holds down to people with 2 prior commits.

### 2. Some modules are mostly unowned

Owned share overall is 0.543 (N = 1,819). `examples/` 0.028 (CI 0.011–0.070), `mllib/` 0.149 (0.091–0.235) and `common/` 0.281 (0.186–0.401) are all flagged. Their authors are the same unassigned people.

### 3. Ticket mapping depends mostly on age, partly on where the code lives

10,647 tickets (18%) have several components. 70dd's `bias.json` sliced on the first component only. `bias.py` now counts a ticket in each of its components, so the component overall is over (ticket, component) pairs: 0.612 (N = 71,845). Per ticket it stays 0.608 (N = 59,491).
- All time, 11 of 36 judged components are flagged: EC2 0.083, Spark Docker 0.209, Mesos 0.287, `(none)` 0.334, Input/Output 0.348, Java API 0.369, Kubernetes 0.389, Spark Submit 0.392, GraphX 0.414, Spark Shell 0.419, Deploy 0.447. Kubernetes is 0.375 by first component (70dd) and 0.389 counted in every component.
- By year, 5 of 18 are flagged. Tickets filed before 2014 map under 25% of the time, because their files are gone by the pinned SHA. From 2014 on it's 0.52–0.72.
- Since 2020 (overall 0.676, N = 35,285), EC2 and Mesos don't clear. They're unjudged (N = 8 and N = 17, or 8 and 14 by first component). Holding age fixed still leaves 8 of 32 judged components flagged: Spark Docker 0.209 (N = 86), Input/Output 0.315 (92), Spark Submit 0.325 (123), Java API 0.333 (81), Spark Shell 0.344 (64), Examples 0.354 (79), Kubernetes 0.372 (1,762), Deploy 0.426 (61). Under the old point-estimate rule on the first component, 12 were flagged since 2020, including Connect 0.563 (N = 3,163). Connect now reads 0.566 (N = 3,194, CI 0.549–0.583), 0.11 below overall, but its interval reaches the 0.576 line, so it isn't flagged. Kubernetes and Spark Docker work is partly committed in Apache repos the snapshot doesn't hold (`spark-docker`, `spark-kubernetes-operator`). We have no such explanation yet for Input/Output, Java API, Spark Submit and Examples.
- `(none)`: 1,382 tickets with no component, 0.334 (CI 0.310–0.360). All were filed in 2017 or earlier (276 in the 0010–0012 import) and none since 2020, so this is the age effect again. They have no team, so no team view would show them anyway.
- Issue types without code (Wish 0.117, Brainstorming 0.118, Question 0.161) are low by nature: 3 of 13 judged types.
- Below overall with confidence but within 0.10, so not flagged: MLlib 0.545 (N = 1,425, first component), New Feature 0.518 (N = 4,505), and the years 2014, 2017 and 2018.

### 4. The resolver treats every repo alike

Kept share is 0.981 overall (N = 1,556), and 0 of 12 judged repos are flagged. The lowest judged repo is jazzband/django-silk, 0.933 on N = 30 (CI 0.787–0.982). django-tenants (0.857, N = 7) is under N = 30, so it's unjudged.

### How many slices

136 judged and 32 flagged (`bias.json` `slices_tested`, `slices_flagged`). Ticket mapping and resolver judge 111 and flag 27. The owner rule judges 25 and flags 5 (module 3/1, team 6/1, prior commits 2/0, assigned share 7/0, owned share 7/3).

The interval rule is per slice and isn't corrected for multiple comparisons. So we also ran a Bonferroni check, which tightens each test so the whole set of 136 keeps a 5% error rate: each slice's interval at 1 − 0.05/136, z = 3.56. It keeps 26 of the 32 flags. The large gaps hold (unassigned, Project Infra, `examples/`, `mllib/`, EC2, Mesos, Kubernetes, Spark Docker, Spark Submit, Input/Output, Java API, old years, no-code types). `core/`, `common/`, Deploy (all time and since 2020), GraphX and Spark Shell (all time) don't, so treat those as likely, not established.

Fairlearn's spread over judged slices (`difference` / `ratio`): component 0.690 / 0.107, component since 2020 0.608 / 0.256, type 0.645 / 0.153, year 0.692 / 0.033, resolver by repo 0.067 / 0.933. Owner rule: accuracy by module 0.291 / 0.439, by team 0.804 / 0.000, by prior commits 0.067 / 0.880; assigned share by module 0.311 / 0.680; owned share by module 0.855 / 0.032.

We also found a data anomaly, not bias: 289 Spark tickets are dated in the years 0010–0012. Apache Jira itself has those dates, from the 2013 import from Spark's old tracker. `meridian.validate` now warns on dates before 2000 or after the snapshot.

## Mitigation

We scored each candidate fix for the owner rule on the same files in the rerun (`bias.json`, `owner_rule.mitigations`). The worst module gap is on next authors with a team.

| Variant | Files owned | Accuracy (owners.py, unassigned = miss) | Accuracy, next authors with a team | Next author unassigned | Worst module gap |
|---|---:|---:|---:|---:|---:|
| as configured: `min_share` 0.30, teams from 180 days | 988 | 0.373 | 0.496 (N = 744) | 24.7% | 0.268 |
| `min_share` 0.20 / 0.10 (adjusting the decision threshold) | 992 | 0.372 | 0.493 (N = 748) | 24.6% | 0.265 |
| teams from 365 days | 989 | 0.439 | 0.538 (N = 806) | 18.5% | 0.263 |
| teams from 730 days | 989 | 0.458 | 0.545 (N = 831) | 16.0% | 0.242 |
| fallback to the last toucher's team when unowned | 1,634 | 0.303 | 0.395 (N = 1,255) | 23.2% | 0.369 |

- Lowering the threshold does nothing. The unowned files are unowned because their authors have no team, not because shares are close to 0.30.
- Falling back to the last toucher owns 65% more files, but it's wrong more often (0.395) and widens the module gap.
- A longer lookback for a person's team helps, but less than 0.373 → 0.458 suggests. Each variant is scored against its own labels, and the 730-day lookback also gives a team to 86 next authors who were unassigned, every one a guaranteed miss before. Without them the gain is 0.496 → 0.545 (+0.049), with overlapping intervals (0.460–0.532 vs 0.511–0.579). Coverage is the bigger win: unassigned next authors go from 24.7% to 16.0%. The catch is that a team from two years ago can be stale for someone who moved.
- On held-out data the longer lookback doesn't hold up. `bias.py` picks the best variant at a cut one window earlier, 2025-10-06, with next authors limited to before the current cut, so the pick never sees the answers it's checked on. Then it scores the pick against the configured rule on the current cut, 2026-04-04, with a 95% interval from a seeded paired bootstrap over files (1,000 resamples, seed 0). On 1,279 files at the earlier cut the configured rule wins: 180 days 0.505, 730 days 0.502, `min_share` 0.20/0.10 0.502, 365 days 0.486. So the pick is the configured rule, and the held-out delta is 0.0, with a 95% interval of 0.0–0.0 (`bias.json` `owner_rule.held_out`). The +0.049 on the current cut is in-sample only.

### What we ship now (no new run_id)

The views get the caveat now. Every slice in `bias.json` carries `n`, `ci_low`, `ci_high` and `flagged`, and the coverage slicings are `owned_share_by_module`, `assigned_share_by_module` and `mapped_share_by_component` (and `_since_2020`). A view shows "low coverage" next to a module, team or component whose slice is flagged, and "too few to judge" when `n < min_n`. That way an unowned module isn't read as nobody's work, and an unmapped component isn't read as idle. This stays in `out/<run_id>/bias.json`, because bias runs in the weekly rebuild and publish ships only the block.

### Not adopted: the 730-day lookback

It would give a person's team its own lookback, `[teams.spark] lookback_days = 730`, separate from the 180-day ownership window. On 70dd it raises next authors with a team from 75.3% to 84.0% and narrows the worst module gap from 0.268 to 0.242. We had set the rule to adopt it only if the held-out delta's interval stayed above 0, and it's 0.0–0.0. So the coverage gain is real but the accuracy gain isn't shown. The team can still choose it for coverage alone. That changes `meridian.toml` and `owners.py`, so it would mean a new team run_id.

### Ticket mapping

04-architecture rules out fuzzy matching, so the fix is coverage, not guessing:
- Add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot so their keyed commits map (a new run_id). That should close much of the Kubernetes and Docker gap, and the next bias run will show how much.
- "Never notified" (stall reason #2, stage 5) should treat a component whose `mapped_share` slice is flagged as unknown, not unnotified.

`tests/test_bias.py` pins the rules on a synthetic history: the interval flag rule, a slice at exactly N = `min_n`, multi-component tickets, the prior-commits slice and the held-out scoring.
