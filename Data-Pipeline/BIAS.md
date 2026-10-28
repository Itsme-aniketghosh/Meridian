# Bias: data slicing on run c5118b019d642a9b

`python -m meridian.bias --run-id c5118b019d642a9b --config meridian.toml` writes `out/<run_id>/bias.json`, slicing with Fairlearn's `MetricFrame`. The thresholds are in [settings.toml](settings.toml) `[bias]`. A slice is judged when it has N ≥ 30 rows. It's flagged when its whole Wilson 95% interval (an interval for a share that holds up on small samples) sits more than 0.10 below the overall value (`ci_high < overall − 0.10`). A slice under N = 30 is unjudged: neither flagged nor cleared. Each slicing also records how many slices it judged (`tested`) and Fairlearn's `difference()` (max − min) and `ratio()` (min / max) over the judged slices.

Every number below comes from `bias.py` as it is now, rerun on 2026-10-27 on the outputs of the Airflow runs in the report's Appendix A. Against v1 (70dd1dabfed544b8), the owner rule and resolver slices are unchanged. Only the ticket-mapping shares moved, by at most 0.017, and the same slices are flagged, less the years 0010–0012, which are no longer slices (below).

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

Counting every next author gives 0.373 (N = 988), with `unassigned` at 0.000 on N = 244. That 0.000 is built in: `team_owner` never names `unassigned`, and the same `person_teams` (same lookback) builds both the prediction and the label, so an unassigned next author is a miss under every variant. So `bias.py` now scores accuracy only on next authors with a team: 0.496 (N = 744, 95% CI 0.460–0.532). The unassigned rate is reported on its own as coverage (24.7% of owned files' next authors, `assigned_share_by_module`). Without the unassigned:
- By module, 3 of 11 judged, 1 flagged: `core/` 0.228 (N = 57, CI 0.138–0.352). Counting the unassigned as misses it read 0.167 (N = 78), so part of `core/`'s gap was the circular class, but not all of it. `sql/` 0.519 (N = 393) and `python/` 0.509 (N = 232) sit on the overall line.
- By the next author's team, 6 of 12 judged, 1 flagged: `Project Infra` 0.000 on exactly N = 30 (CI 0–0.114). That's borderline: one file fewer and it would be unjudged. It's also one person: every one of those files has the same next author, whose tickets carry the Project Infra component, so that's their team. But they edit SQL code (50 of their 59 files are in `sql/`), so the rule names SQL or no owner, and never Project Infra (it names Project Infra for 5 files in all). The 0.000 is one person's team label against the code they touch, not a group the rule treats worse. No fix: the label comes from Jira components, and the code area is right. The highest is Structured Streaming 0.804 (N = 51). Build (0.328, N = 116) and Spark Core (0.312, N = 64) are low, but their intervals reach the flag line.
- By the next author's commits before the cut, 2 of 3 judged, 0 flagged: 2–10 commits 0.560 (N = 50), more than 10 0.493 (N = 686). One commit (0.375, N = 8) is too few to judge, so "newcomers aren't served worse" only holds down to people with 2 prior commits. There is no 0-commit slice at all: a first-time author can't have a team, so newcomers sit entirely in the no-team group (section 5 below).

### 2. Some modules are mostly unowned

Owned share overall is 0.543 (N = 1,819). `examples/` 0.028 (CI 0.011–0.070), `mllib/` 0.149 (0.091–0.235) and `common/` 0.281 (0.186–0.401) are all flagged. Their authors are the same unassigned people.

### 3. Ticket mapping depends mostly on age, partly on where the code lives

10,647 tickets (18%) have several components. 70dd's `bias.json` sliced on the first component only. `bias.py` now counts a ticket in each of its components, so the component overall is over (ticket, component) pairs: 0.613 (N = 71,845). Per ticket it stays 0.608 (N = 59,491).
- All time, 11 of 36 judged components are flagged: EC2 0.087, Spark Docker 0.209, Mesos 0.287, `(none)` 0.334, Input/Output 0.348, Java API 0.369, Kubernetes 0.390, Spark Submit 0.402, GraphX 0.414, Spark Shell 0.422, Deploy 0.451. On v1 (70dd), Kubernetes was 0.375 by first component and 0.389 counted in every component.
- By year, 2 of 15 are flagged, 2012 and 2013. Tickets filed before 2014 map under 25% of the time, because their files are gone by the pinned SHA. From 2014 on it's 0.52–0.72.
- Since 2020 (overall 0.677, N = 35,285), EC2 and Mesos don't clear. They're unjudged (N = 8 and N = 17, or 8 and 14 by first component). Holding age fixed still leaves 8 of 32 judged components flagged: Spark Docker 0.209 (N = 86), Input/Output 0.315 (92), Spark Submit 0.341 (123), Java API 0.333 (81), Spark Shell 0.359 (64), Examples 0.354 (79), Kubernetes 0.372 (1,762), Deploy 0.426 (61). Under the old point-estimate rule on the first component, 12 were flagged since 2020, including Connect 0.563 (N = 3,163). Connect now reads 0.566 (N = 3,194, CI 0.549–0.583), 0.11 below overall, but its interval reaches the 0.576 line, so it isn't flagged. Kubernetes and Spark Docker work is partly committed in Apache repos the snapshot doesn't hold (`spark-docker`, `spark-kubernetes-operator`). We have no such explanation yet for Input/Output, Java API, Spark Submit and Examples.
- `(none)`: 1,382 tickets with no component, 0.334 (CI 0.310–0.360). All were filed in 2017 or earlier (276 in the 0010–0012 import) and none since 2020, so this is the age effect again. They have no team, so no team view would show them anyway.
- Issue types without code (Wish 0.117, Brainstorming 0.118, Question 0.163) are low by nature: 3 of 13 judged types.
- Below overall with confidence but within 0.10, so not flagged: MLlib 0.545 (N = 1,425, first component), New Feature 0.518 (N = 4,505), and the years 2014, 2017 and 2018.

### 4. The resolver treats every repo alike

Kept share is 0.981 of candidates overall (N = 1,556), and 0 of 12 judged repos are flagged. What's dropped are look-alikes (a name that lands on a parameter, a local or another function), so this slice asks whether repos lose look-alikes at a similar rate. That real calls are kept comes from the answer keys: in Django 265 of 265 edit lines and 542 of 542 aliased calls (all 294 candidates resolve right), in the packages 1,236 of 1,236 ([RESOLVER.md](RESOLVER.md)). The lowest judged repo is jazzband/django-silk, 0.933 on N = 30 (CI 0.787–0.982). django-tenants (0.857, N = 7) is under N = 30, so it's unjudged.

### 5. Next authors with no team, as their own group

Accuracy can't score them, so `bias.json` `owner_rule.no_team_authors` describes them instead (counts only, never a person):
- 438 of 1,819 files (24.1%) have a next author with no team. That's 96 of the 169 next authors.
- By commits before the cut: 0 commits 70 people (257 files), 1 commit 1 (2), 2–10 commits 11 (32), more than 10 14 (147). Most are newcomers. A person's team comes from their own ticket-keyed commits in the lookback, so a first-time author can never have one. The 26 with earlier commits have none in the last 180 days that name a ticket with a component.
- By module: `sql/` 325 files (33.9% of `sql/`'s 959), `core/` 33 (19.0%), `python/` 32 (10.7%), `connector/` 14 (66.7% of 21), `common/` 12 (18.8%), `resource-managers/` 6, `mllib/` 5, `examples/` 4, `dev/` 3, and one file each in `docs/`, `graphx/`, `launcher/`, `project/`.
- The rule names an owner for 55.7% of their files, against 53.9% for next authors with a team. So their files are routed like anyone's; the gap is that nobody can check the route.
- The 730-day lookback gives a team to the next author of 150 of the 438 files. On the 86 of those the rule owns, the named owner matches that team 0.651 of the time, above the overall 0.496. All of them made commits before the cut: a newcomer has no team under any lookback, so stays uncheckable.

### How many slices

133 judged and 29 flagged (`bias.json` `slices_tested`, `slices_flagged`). Ticket mapping and resolver judge 108 and flag 24. The owner rule judges 25 and flags 5 (module 3/1, team 6/1, prior commits 2/0, assigned share 7/0, owned share 7/3).

The interval rule is per slice and isn't corrected for multiple comparisons. So we also ran a Bonferroni check, which tightens each test so the whole set of 133 keeps a 5% error rate: each slice's interval at 1 − 0.05/133, z = 3.56. It keeps 23 of the 29 flags. The large gaps hold (unassigned, Project Infra, `examples/`, `mllib/`, EC2, Mesos, Kubernetes, Spark Docker, Spark Submit, Input/Output, Java API, old years, no-code types). `core/`, `common/`, Deploy and Spark Shell (all time and since 2020) don't, so treat those as likely, not established.

Fairlearn's spread over judged slices (`difference` / `ratio`): component 0.686 / 0.112, component since 2020 0.608 / 0.256, type 0.645 / 0.153, resolver by repo 0.067 / 0.933. Owner rule: accuracy by module 0.291 / 0.439, by team 0.804 / 0.000, by prior commits 0.067 / 0.880; assigned share by module 0.311 / 0.680; owned share by module 0.855 / 0.032.

We also found a data anomaly, not bias: 289 Spark tickets are dated in the years 0010–0012. Apache Jira itself has those dates, from the 2013 import from Spark's old tracker. `meridian.validate` treats a date before 2000 or after the snapshot as invalid and warns on every run, and `bias.py` puts those tickets in no year slice.

## Mitigation

We scored each candidate fix for the owner rule on the same files in the rerun (`bias.json`, `owner_rule.mitigations`). The worst module gap is on next authors with a team.

| Variant | Files owned | Accuracy (owners.py, unassigned = miss) | Accuracy, next authors with a team | Next author unassigned | Worst module gap |
|---|---:|---:|---:|---:|---:|
| as configured: `min_share` 0.30, teams from 180 days | 988 | 0.373 | 0.496 (N = 744) | 24.7% | 0.268 |
| `min_share` 0.20 / 0.10 (a looser decision threshold) | 992 | 0.372 | 0.493 (N = 748) | 24.6% | 0.265 |
| `min_share` 0.40 (stricter: abstains more) | 974 | 0.378 | 0.500 (N = 736) | 24.4% | 0.272 |
| `min_share` 0.50 | 956 | 0.378 | 0.501 (N = 721) | 24.6% | 0.282 |
| `min_share` 0.60 | 817 | 0.400 | 0.534 (N = 612) | 25.1% | 0.284 |
| teams from 365 days | 989 | 0.439 | 0.538 (N = 806) | 18.5% | 0.263 |
| teams from 730 days | 989 | 0.458 | 0.545 (N = 831) | 16.0% | 0.242 |
| fallback to the last toucher's team when unowned | 1,634 | 0.303 | 0.395 (N = 1,255) | 23.2% | 0.369 |

- Lowering the threshold does nothing. Those files are unowned because their authors have no team, not because shares sit near 0.30.
- Raising it traces the abstain curve. 0.40 and 0.50 stay flat (0.500, 0.501, intervals 0.464–0.536 and 0.464–0.537 against 0.460–0.532). 0.60 is the first clear step: 0.534 (0.495–0.573) on 817 files, and 0.505 → 0.560 at the earlier cut (769 → 595 files). It gives up 171 files, which are right 42 of 132 times (0.318), and the worst module gap widens to 0.284.
- Falling back to the last toucher owns 65% more files, but it's right less often (0.395 against 0.496) and widens the module gap.
- A longer lookback for a person's team helps, but less than 0.373 → 0.458 suggests. Each variant is scored against its own labels, and the 730-day lookback also gives a team to 86 next authors who were unassigned, every one a guaranteed miss before. Without them the gain is 0.496 → 0.545 (+0.049), with overlapping intervals (0.460–0.532 vs 0.511–0.579). Coverage is the bigger win: unassigned next authors go from 24.7% to 16.0%. The catch is that a team from two years ago can be stale for someone who moved.
- On held-out data the longer lookback doesn't hold up. `bias.py` picks the best variant at a cut one window earlier, 2025-10-06, with next authors limited to before the current cut, so the pick never sees the answers it's checked on. Then it scores the pick against the configured rule on the current cut, 2026-04-04, with a 95% interval from a seeded paired bootstrap over files (1,000 resamples, seed 0). Only a variant that owns at least as many files as the configured rule there can be picked: a stricter `min_share` is right more often on what it still names, so it would win by abstaining, not by being right more (`owned_at_earlier_cut` records the counts). On 1,279 files at the earlier cut the configured rule wins: 180 days 0.505, 730 days 0.502, `min_share` 0.20/0.10 0.502, 365 days 0.486. The stricter thresholds score 0.508 (0.40, 755 files), 0.504 (0.50, 737) and 0.560 (0.60, 595) but own fewer files, so they're read as the curve above, not picked. So the pick is the configured rule, and the held-out delta is 0.0, with a 95% interval of 0.0–0.0 (`bias.json` `owner_rule.held_out`). The +0.049 on the current cut is in-sample only.

### Kept, after measuring: abstain below `min_share` 0.30

`min_share` is the owner rule's decision threshold. Below it the rule names no owner and the file shows as "unowned, needs review". It's the rule we already had; measuring it against the alternatives is what's new, and we kept it: the rule abstains rather than guesses. The curve above is its measurement, against the rule that never abstains (the last-toucher fallback, 0.395 on 1,634 files).

Why abstaining reduces harm: a wrong owner is silent. The work goes to a team that didn't write the code, and nothing shows it went wrong. "Unowned, needs review" is visible, so a person routes it. On the 511 scored files where the rule abstains and the fallback guesses, the guess is right 127 times (0.249): three in four would go to the wrong team.

Why 0.30: from 0.10 to 0.50 accuracy is flat (0.493–0.501), so 0.30 keeps the most files at the same accuracy. 0.60 is better on accuracy at both cuts (+0.038 now, +0.055 earlier) but sends 171 more files to review and widens the worst module gap. We recommend it if the team has the review capacity. It changes `meridian.toml` `[ownership] min_share`, so a new run_id; it isn't adopted on c5118b019d642a9b.

### What we ship now (no new run_id)

The views get the caveat now. Every slice in `bias.json` carries `n`, `ci_low`, `ci_high` and `flagged`, and the coverage slicings are `owned_share_by_module`, `assigned_share_by_module` and `mapped_share_by_component` (and `_since_2020`). A view shows "low coverage" next to a module, team or component whose slice is flagged, and "too few to judge" when `n < min_n`. So an unowned module isn't read as nobody's work, nor an unmapped component as idle. This stays in `out/<run_id>/bias.json`, because bias runs in the weekly rebuild and publish ships only the block.

### Not adopted: the 730-day lookback

It would give a person's team its own lookback, `[teams.spark] lookback_days = 730`, separate from the 180-day ownership window. On c5118b019d642a9b it raises next authors with a team from 75.3% to 84.0% and narrows the worst module gap from 0.268 to 0.242. We had set the rule to adopt it only if the held-out delta's interval stayed above 0, and it's 0.0–0.0. So the coverage gain is real but the accuracy gain isn't shown. The team can still choose it for coverage alone. That changes `meridian.toml` and `owners.py`, so it would mean a new team run_id.

### Ticket mapping

04-architecture rules out fuzzy matching, so the fix is coverage, not guessing:
- Add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot so their keyed commits map (a new run_id). That should close much of the Kubernetes and Docker gap, and the next bias run will show how much.
- "Never notified" (stall reason #2, stage 5) should treat a component whose `mapped_share` slice is flagged as unknown, not unnotified.

`tests/test_bias.py` pins the rules on a synthetic history: the interval flag rule, a slice at exactly N = `min_n`, multi-component tickets, the prior-commits slice, a threshold above the configured one, the no-team group and the held-out scoring (a stricter threshold isn't picked for abstaining).
