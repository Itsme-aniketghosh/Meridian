# Bias · data slicing on run 70dd1dabfed544b8

`python -m meridian.bias --run-id 70dd1dabfed544b8 --config meridian.toml` → `out/<run_id>/bias.json`. Thresholds in [settings.toml](settings.toml) `[bias]`: a slice needs **N ≥ 30** and is flagged when its metric is **more than 0.10 below** the overall value. Slicing uses Fairlearn's `MetricFrame`.

## What gets sliced, and why these

There is no trained model in step 1. The pipeline's "models" are its rules, and three of them decide what each team sees on screen. Each has a right answer to check against or a rate that should not depend on who you are:

| Rule | Metric | Slices (the "groups") | Why it matters |
|---|---|---|---|
| Owner rule (Spark) | Does the owner team predict the next author's team? (week 1's test) · share of files it owns at all | top-level module (`sql/`, `core/`, …) · the next author's team | A team the rule gets wrong is sent the wrong work; an unowned module is nobody's |
| Ticket → code mapping (Spark Jira) | share of tickets with a code target | component · issue type · year filed · component on tickets since 2020 | An unmapped team looks idle: "never notified", no chains |
| Resolver (Django, packages) | share of edit-role candidates kept as call sites | repo | A repo the resolver drops more in would show less work left |

## What we found

**1. The owner rule can never be right for people without a team.** Overall 0.373 (N = 988), as `owners.py` reports. By the next author's team, `unassigned` scores **0.000 on N = 244**: a person gets a team only from a component on one of their keyed commits in the last 180 days, and a quarter of next authors have none. No team-level rule can predict a team that doesn't exist. Module `core/` is also low (0.167, N = 78).

**2. Some modules are mostly unowned.** Owned share overall 0.543 (N = 1,819); `examples/` 0.028, `mllib/` 0.149, `common/` 0.281. Their authors are the same unassigned people.

**3. Ticket mapping depends mostly on age, partly on where the code lives.** Overall 0.608 (N = 59,491), 16 of 39 components flagged (EC2 0.082, Mesos 0.297, Spark Docker 0.189). By year, tickets filed before 2014 are mapped under 25% of the time, 2014 on 52–72%: their files are gone by the pinned SHA. Holding age fixed (tickets since 2020, overall 0.668) clears EC2 and Mesos, but **Kubernetes (0.354, N = 1,570) and Spark Docker (0.189) stay low**: much of their work is committed in other Apache repos (`spark-docker`, `spark-kubernetes-operator`) that the snapshot doesn't hold. Issue types without code (Wish, Brainstorming, Question) are low by nature.

**4. The resolver treats every repo alike.** Kept share 0.981 overall (N = 1,556), no repo flagged (lowest django-tenants 0.857 on N = 7, below N = 30).

**Also found (a data anomaly, not bias):** 289 Spark tickets are dated in the years 0010–0012. Apache Jira itself has those dates (the 2013 import from Spark's old tracker). `meridian.validate` now warns on dates before 2000 or after the snapshot.

## Mitigation

Each candidate fix for the owner rule is scored on the same slices (`bias.json`, `owner_rule.mitigations`):

| Variant | Files owned | Accuracy | Next author unassigned | Worst module gap |
|---|---:|---:|---:|---:|
| as configured: `min_share` 0.30, teams from 180 days | 988 | 0.373 | 24.7% | 0.207 |
| `min_share` 0.20 (adjusting the decision threshold) | 992 | 0.372 | 24.6% | 0.205 |
| `min_share` 0.10 | 992 | 0.372 | 24.6% | 0.205 |
| teams from 365 days | 989 | 0.439 | 18.5% | 0.234 |
| **teams from 730 days** | **989** | **0.458** | **16.0%** | **0.202** |
| fallback to the last toucher's team when unowned | 1,634 | 0.303 | 23.2% | 0.278 |

- **Lowering the threshold does nothing**: the unowned files are unowned because their authors have no team, not because shares are close to 0.30.
- **Falling back to the last toucher** owns 65% more files but is wrong more often (0.303) and widens the module gap: it trades accuracy for coverage.
- **A longer lookback for a person's team is the fix**: it resamples the underrepresented group (people with no recent keyed commit get the team of their older work). Accuracy 0.373 → 0.458, unassigned next authors 24.7% → 16.0%, and the worst module gap doesn't grow. Trade-off: a team from two years ago can be stale for someone who moved.

**Recommendation (team decision, not applied):** give a person's team its own lookback, `[teams.spark] lookback_days = 730`, separate from the 180-day ownership window. It changes `meridian.toml` and `owners.py`, so it means a new team run_id. Until then, the views (stage 8) should show the owner rule's coverage per module next to it, so an unowned module isn't read as nobody's work.

**Ticket mapping:** no fuzzy matching is allowed (04-architecture), so the fix is coverage, not guessing:
- Add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot so their keyed commits map (a new run_id). That should close much of the Kubernetes and Docker gap; the next bias run will show how much.
- Views (stage 8) should show the mapped share per component next to each team, and "never notified" (stall reason #2, stage 5) should treat a component under the overall rate as unknown rather than unnotified.

Trade-offs are on record in `bias.json`, rerun on every run: `tests/test_bias.py` pins the rules on a synthetic history.
