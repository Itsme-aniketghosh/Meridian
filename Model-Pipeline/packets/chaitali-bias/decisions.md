# Chaitali: decisions

Each decision has its test and the rule that picks, written before the run. Results go under each one after the run, with N. Runs are tagged `decision=chaitali-<n>`.

Looked at before writing these (Nov 7), on `val` only, untuned LightGBM, to size the tests: `val` N = 4,113, 486 positives. Rows and positives per slice:

| Slice | Groups | Groups with ≥ 30 rows | Groups with ≥ 30 positives |
|---|---:|---:|---:|
| `module` | 33 | 14 | 4 |
| `component` | 31 | 14 | 3 |
| `exp_bucket` | 4 | 4 | 2 |
| `size_bucket` | 4 | 4 | 3 |

## 1. Which slices

Options: the four slices as they are; or groups under 30 rows merged into one `other` per slice.

Test: judged slices per metric (recall needs 30 positives, the false-positive rate 30 negatives, PR-AUC 30 positives) under each option, on `val`.

Rule: merge into `other` when it adds a judged slice and hides none (a group judged on its own stays on its own). `other`'s members are listed in `bias_report.json`, so a flag on it can be traced. If merging adds nothing, keep the groups as they are and list the unjudged ones.

## 2. Metric per slice

All three: recall, false-positive rate, PR-AUC. The report shows where they disagree.

Test: for each judged slice, which of the three flag it.

Rule: none to pick. A disagreement is a finding, written up as such. PR-AUC also moves with a slice's positive rate (a slice with fewer positives scores lower with nothing wrong), so a PR-AUC flag alone, with recall and the false-positive rate unflagged, is reported as a base-rate effect, not a harm.

## 3. Flag rule

Options: phase 1's (judged at N ≥ 30, flagged when the whole Wilson 95% interval is more than 0.10 below overall, or above for the false-positive rate); the same with a Bonferroni-corrected interval (95% split over every judged test).

Test: (a) the planted slices of PASS-BARS §6.1, exact counts: 500 positives at recall 0.35 against 0.50 overall must be flagged, the same at 0.50 must not. (b) Each judged slice's power: the chance the rule flags a true 0.15 gap at its N, from the binomial, exactly. (c) Flags on `val` under both options.

Rule: keep phase 1's rule if it passes (a), so the report compares with phase 1. Report the Bonferroni count beside it. If phase 1's flags a slice Bonferroni doesn't, the report says the flag might be one of many tests.

Already known from the simulation in PASS-BARS §6: at this table's sizes the rule rarely flags a real 0.15 gap (54% at 400 positives). (b) puts each slice's number in the report.

## 4. Newcomers

Test: recall for `exp_bucket = 0`, from the candidate and from lines × files, at the same alert rate, with N and positives.

Rule: none to pick. Both go in the report. Known before the run: `exp_bucket = 0` has 176 rows and 16 positives in `val`, under the 30 positives recall needs, so it is reported and not judged.

## 5. Mitigation

Options: none; re-weighting or re-sampling the flagged slice (with Rachna); per-group thresholds (Fairlearn `ThresholdOptimizer`).

Test: each on the same `val` rows: overall PR-AUC, the flagged slice's recall, the worst gap.

Rule (PASS-BARS §6.2): adopt the one that closes at least half of the worst flagged gap for at most 0.01 `val` PR-AUC. A per-group threshold only on a group known at push time (`module`, `size_bucket`, `exp_bucket`; not `component`). If none qualifies, the model ships unmitigated and the report gives the numbers. If nothing is flagged, nothing is tried, and the report says so with each slice's power.

## 10. The threshold recall and the false-positive rate are measured at

Found on Nov 7: `[eval] threshold = "precision"` (precision 0.80 on at least 30 alerts) flags 0 of 4,113 `val` commits for every model and baseline: no score reaches that precision. At 0 alerts recall is 0 in every slice, so nothing can be compared.

Options: `[eval]`'s threshold; the alert rate today's P9 rule flags (`[eval] threshold_alert_rate`, 15.4%).

Rule: use `[eval]`'s threshold when it flags at least `threshold_min_alerts` `val` commits; otherwise the alert-rate rule, the same share of commits as today's rule, so the model and lines × files are compared at the same reviewer workload. `bias_report.json` names which one was used. Rochan to be told: `metrics.json` has the same 0 alerts.

---

# Results, first candidate (Nov 7)

Run on the untuned LightGBM (`reports/untuned_val_scores.csv`, column `lgbm`), the candidate until Rachna's tuned one lands (Nov 8), when every number below is redone. `val` N = 4,113, 486 positives. Threshold by decision 10: `[eval]`'s precision rule flags 0, so the P9 alert rate: 634 alerts (15.4%), for the model and lines × files alike. MLflow `meridian-defect-risk`, runs tagged `chaitali-1` and `chaitali-3`; the report run `4e53702581af4b14ae2458ad4e3ba624`.

## 1. Which slices: pool

Judged slices per metric (recall / false-positive rate / PR-AUC):

| Slice | As they are | Under 30 rows into `(other)` |
|---|---|---|
| `module` | 4 / 14 / 4 | 4 / **15** / 4 |
| `component` | 3 / 14 / 3 | 3 / **15** / 3 |
| `exp_bucket` | 2 / 4 / 2 | 2 / 4 / 2 |
| `size_bucket` | 3 / 4 / 3 | 3 / 4 / 3 |

Pooling adds one judged slice each for `module` and `component` and hides none: **pooled** (`pool_below = 30`). `(other)` holds 19 modules and 17 components, listed in `bias_report.json`. 62 judged tests in all.

## 2. Metric per slice: they disagree, by size

| Slice | Recall | False-positive rate | PR-AUC |
|---|---|---|---|
| `size_bucket=Q2` (986 rows, 51 positives) | **0.039**, flagged | 0.007 | **0.135**, flagged |
| `size_bucket=Q3` (917, 114) | **0.140**, flagged | 0.070 | **0.210**, flagged (not under Bonferroni) |
| `size_bucket=Q4` (1,031, 299) | 0.746 | **0.449**, flagged | 0.488 |

Overall: recall 0.496, false-positive rate 0.108, PR-AUC 0.394. The two harms land on different commits: smaller commits get their bugs missed, large ones get false alarms. No `module`, `component` or `exp_bucket` slice is flagged by any metric.

## 3. Flag rule: phase 1's

The planted slices on exact counts: recall 175 of 500 (0.35, interval 0.310–0.393) against 0.50 flagged; 250 of 500 not; the false-positive version flagged, its no-gap version not. Phase 1's rule passes, so it stays. Bonferroni (95% split over 62 tests) keeps 4 of the 5 flags; `size_bucket=Q3` PR-AUC is the one that might be one of many tests.

Power at a true 0.15 gap, judged recall slices: 0.10 to 0.53 (`exp_bucket=>10`, 410 positives, is the highest). False-positive rate: 0.12 to 1.00. So no recall flag on a module or component is weak evidence of no gap: the rule would have caught a 0.15 gap there at most 1 time in 3.

## 4. Newcomers: no worse off, not judged

`exp_bucket = 0`: 176 rows, 16 positives, under 30, so not judged. Recall at the same alert rate: model **0.562** (9 of 16), lines × files **0.438** (7 of 16). The model doesn't treat newcomers worse than the rule it replaces; 16 is too few to say more.

## 10. Threshold: the alert rate

Used `alert_rate` (0.154): `[eval]`'s precision rule flags 0 val commits.

## Context for decision 5: the old rule is more skewed than the model

The same check on lines × files at the same alert rate flags 6 slices: `size_bucket` Q2 recall **0.000**, Q3 recall **0.018**, Q4 false-positive rate **0.540**, both PR-AUCs, and `component=Pandas API on Spark` false-positive rate 0.294 (not under Bonferroni). The model is less skewed in every size slice. Part of the skew is the base rate: positives are 1.9% of Q1, 5.2% of Q2, 12.4% of Q3 and 29.0% of Q4, so at a fixed alert budget most alerts go to large commits. Decision 5 tests whether a mitigation can narrow it further within its rule.

---

# Decision 5: how it runs (Nov 8, before the run)

Written after decisions 1–4 found the flags (all on `size_bucket`) and before any mitigation was tried.

- **The gap it judges:** the worst flagged gap on the candidate with no mitigation (on the first candidate: `size_bucket=Q2` recall, 0.039 against 0.496). Closing = how much of that slice-and-metric gap goes, at the same alert budget.
- **Per-group thresholds, by `size_bucket`** (known at push time): (a) one threshold per bucket so every bucket reaches the same recall, with total alerts held at the budget; (b) Fairlearn's `ThresholdOptimizer` with true-positive-rate parity, reported for its own alert count. Both are fitted on one half of `val` (split by `sha`) and scored on the other, then swapped, so no threshold is judged on the rows that set it.
- **Re-sampling, with Rachna's code unchanged:** the train rows of positives in `size_bucket` Q1–Q3 added 1 and 3 more times (×2, ×4), refitted by `train.fit_and_score` with the candidate's kind, params and seed, scored on all of `val`. Whole-number re-weighting is the same fit, so it's covered by this.
- **Cost:** `val` PR-AUC against no mitigation. A threshold doesn't change the score, so its PR-AUC cost is 0 by construction; overall recall and precision at the budget are reported beside it, and a threshold that costs more than 0.02 overall precision isn't adopted (the 0.01 PR-AUC bar can't see that cost).
- **If more than one qualifies:** the one with the smallest cost; on a tie, the simpler (a threshold before a refit).
