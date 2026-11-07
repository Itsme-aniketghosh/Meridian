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
