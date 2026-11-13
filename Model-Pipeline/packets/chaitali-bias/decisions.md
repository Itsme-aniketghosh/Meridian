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

---

# Decision 5: results (Nov 8)

`python -m model.bias --decide 5`. Alert budget 15.4% (634 of 4,113 `val` commits). The worst flagged gap is `size_bucket=Q2` recall on both candidates. "Closes" is the share of that gap gone, at the option's own overall recall.

## First candidate: untuned LightGBM

| Option | Alerts | PR-AUC (cost) | Recall | Precision (cost) | Q2 recall | Closes | Flags | Meets the rule |
|---|---:|---|---:|---|---:|---:|---:|---|
| none | 634 | 0.394 | 0.496 | 0.380 | 0.039 | — | 5 | — |
| thresholds, equal recall by size | 614 | 0.394 (0) | 0.333 | 0.264 (−0.116) | 0.353 | 104% | 2 | no: precision cost |
| thresholds, Fairlearn TPR parity | 1,448 | 0.394 (0) | 0.603 | 0.202 (−0.178) | 0.549 | 88% | 3 | no: precision cost, 2.3× the alerts |
| re-sampling ×2 | 634 | 0.388 (−0.006) | 0.481 | 0.369 (−0.011) | 0.078 | 12% | 6 | no: closes too little |
| re-sampling ×4 | 634 | 0.356 (−0.038) | 0.426 | 0.326 (−0.054) | 0.196 | 50% | 4 | no: PR-AUC cost |

## Provisional tuned candidate: tuned LightGBM

Rachna's `candidate.json` isn't out yet, so this is `train.py --tune logreg forest lgbm` run locally into `reports/provisional/` (5 minutes; best `val` PR-AUC: LightGBM 0.413, forest 0.406, logistic regression 0.382, lines × files 0.319). Redone on her candidate when it lands.

Bias: the same 5 flags, all on size, and stronger than untuned: `size_bucket=Q2` recall **0.000** (0 of 51), Q3 recall 0.070, Q4 false-positive rate 0.488. Newcomers unchanged: 0.562 against lines × files 0.438 (16 positives, not judged).

| Option | Alerts | PR-AUC (cost) | Recall | Precision (cost) | Q2 recall | Closes | Flags | Meets the rule |
|---|---:|---|---:|---|---:|---:|---:|---|
| none | 634 | 0.413 | 0.504 | 0.386 | 0.000 | — | 5 | — |
| thresholds, equal recall by size | 618 | 0.413 (0) | 0.368 | 0.290 (−0.097) | 0.353 | 97% | 2 | no: precision cost |
| thresholds, Fairlearn TPR parity | 1,722 | 0.413 (0) | 0.708 | 0.200 (−0.187) | 0.706 | 100% | 3 | no: precision cost, 2.7× the alerts |
| re-sampling ×2 | 634 | 0.401 (−0.012) | 0.494 | 0.379 (−0.008) | 0.098 | 21% | 4 | no: closes too little, PR-AUC cost |
| re-sampling ×4 | 634 | 0.344 (−0.069) | 0.420 | 0.322 (−0.065) | 0.235 | 63% | 1 | no: PR-AUC cost |

## What it means

On both candidates no mitigation meets the rule, so the model ships unmitigated and the report says why. The reason is the same each time: at a fixed alert budget, catching small commits' bugs means taking alerts from large commits, where bugs are 15 times as common (1.9% of Q1 against 29.0% of Q4 in `val`). Equal recall by size costs a quarter to a third of all bugs caught (recall 0.50 → 0.33–0.37). Re-sampling that closes half the gap costs 4 to 7 times the 0.01 PR-AUC the rule allows. Lines × files, the rule the model replaces, is more skewed still (Q2 recall 0.000, Q3 0.018, Q4 false-positive rate 0.540).

For the final pick (Nov 9): the flagged size slices go in `[bias] accepted`, each pointing here, since a mitigation was tried and written down (the team bias bar). That is done on Rachna's candidate, not this provisional one.

---

# Nov 9: the rules for the final pick and decision 6, before the runs

Written before any bias run on Rachna's candidate and before any gate run.

## The final pick

Rachna's `reports/candidate.json` is the tuned forest (`rachna-1`: tied with LightGBM by the paired bootstrap, and the simpler model wins a tie). PASS-BARS §6.2: the pick is the best `val` PR-AUC model whose bias report passes.

Test: `bias.py` on each tuned model in `reports/tuned_val_scores.csv` (`logreg`, `forest`, `lgbm`), on the same `val` rows and threshold rule as before. Decision 5 again on the forest, since accepting a flag needs a mitigation tried on that model.

Rule:
1. A flagged slice goes in `[bias] accepted` only when decision 5 was tried on it, for this model, and no option met the rule. Its report then passes.
2. If an option meets the rule on the forest, it is adopted, and the pick is the mitigated forest.
3. Otherwise the pick is Rachna's candidate, with its flags accepted by 1. Bias reopens her tie only if the forest has more flagged judged slices than LightGBM. Then LightGBM is the pick, and the reason is written here.

## 6. Gate tolerance

Options: fail a candidate below the champion's `val` PR-AUC at all (0); below by more than 2 standard deviations of seed noise (2 sd); below by more than 2 standard deviations of the difference of two seeds (2√2 sd ≈ 2.83 sd), since the champion and the candidate are each one draw.

The other checks aren't options: `val` PR-AUC above lines × files (the bar), the bias report passes, every input is there and they agree with each other (same split, same N, same candidate). The 2024 test of the team's model bar is Rachna's and Rochan's, once, on Nov 12. CI never reads 2024.

Test: (a) Rachna's 5 seeds (`reports/seed_noise.json`, sd 0.0021): every ordered pair, one as the champion and the other as the candidate. That makes 20 comparisons, and all must pass. (b) 3 planted bad models, each fit by `train.fit_and_score` on the same train rows with the candidate's kind, params and seed 1, scored on `val`. They are: shuffled train labels (permutation seed 1); size only (`train.SIZE`, nothing from history or tickets); half the features (every other column of `train.NUMERIC` dropped, starting from the second). Each is checked against the candidate's seed 1 as the champion, and all must fail.

Rule: the smallest tolerance that gets 20 of 20 and 3 of 3. If no option gets both, the one that gets 3 of 3 with the most seed pairs passing, and the report says noise can block an honest retrain. The pick goes in `model.toml [gate] noise_sds`.

---

# Nov 9: results

## Bias on the three tuned models

`python -m model.bias --scores reports/tuned_val_scores.csv --column <model>`. `val` N = 4,113, 486 positives. Threshold by decision 10: the P9 alert rate, 634 alerts (15.4%). 62 judged tests per model.

| Model | `val` PR-AUC | Overall recall | Flags | Flagged slices |
|---|---:|---:|---:|---|
| `logreg` | 0.382 | 0.498 | 3 | Q2 recall 0.020, Q3 recall 0.140, Q4 false-positive rate 0.480 |
| `forest` (Rachna's candidate) | 0.406 | 0.516 | 5 | Q2 recall **0.000** (0 of 51), Q3 recall 0.096 (of 114), Q4 false-positive rate 0.488 (of 732 negatives); Q2 and Q3 PR-AUC, not under Bonferroni |
| `lgbm` | 0.413 | 0.504 | 5 | the same five: Q2 recall 0.000, Q3 0.070, Q4 false-positive rate 0.488 |

All the flags are on `size_bucket`. No `module`, `component` or `exp_bucket` slice is flagged on any model. Newcomers (`exp_bucket = 0`, 176 rows, 16 positives, not judged): forest recall 0.562, lines × files 0.438.

## Decision 5 on the forest

`python -m model.bias --decide 5` (the candidate's kind and params). The worst gap is `size_bucket=Q2` recall, 0.516.

| Option | Alerts | PR-AUC (cost) | Recall | Precision (cost) | Q2 recall | Closes | Meets the rule |
|---|---:|---|---:|---|---:|---:|---|
| none | 634 | 0.406 | 0.516 | 0.396 | 0.000 | — | — |
| thresholds, equal recall by size | 593 | 0.406 (0) | 0.335 | 0.275 (−0.121) | 0.294 | 92% | no: precision cost |
| thresholds, Fairlearn TPR parity | 1,645 | 0.406 (0) | 0.652 | 0.193 (−0.203) | 0.667 | 103% | no: precision cost, 2.6× the alerts |
| re-sampling ×2 | 634 | 0.398 (−0.008) | 0.504 | 0.386 (−0.009) | 0.059 | 14% | no: closes too little |
| re-sampling ×4 | 634 | 0.345 (−0.061) | 0.422 | 0.323 (−0.073) | 0.255 | 68% | no: PR-AUC cost |

None meets the rule, the same as on both LightGBM runs (Nov 8).

## The final pick: the tuned forest

By the rule above: (1) every flag on the forest had decision 5 tried, and no option met the rule. So the five go in `[bias] accepted`, and the report passes (`reports/bias_report.json`: `pass: true`, 0 open flags of 5). (2) Nothing to adopt. (3) The forest has 5 flagged judged slices, the same as LightGBM, so bias doesn't reopen Rachna's tie, and her candidate stands.

**Pick: the tuned forest** (`reports/candidate.json`; `val` PR-AUC 0.406, interval 0.364–0.450; lines × files 0.319, paired difference +0.087, interval +0.059 to +0.112). It ships unmitigated on size. It misses nearly every bug in Q2 and Q3 commits, and raises half its false alarms on Q4. That's the trade-off the report states with these numbers. Lines × files, the rule it replaces, is more skewed still. Handed to Rochan for the registry. MLflow: the bias run `c8e988aa1f9e4336ab56e0b0b47734e2`.

## 6. Gate tolerance: 2√2 sd

`python -m model.gate --decide 6`. Rachna's 5 seeds: PR-AUC 0.4058, 0.4067, 0.4053, 0.4018, 0.4026 (sd 0.0021, `val` N = 4,113). Champion for the planted models: seed 1, 0.4058. Lines × files: 0.319.

| Planted model | `val` PR-AUC |
|---|---:|
| shuffled labels | 0.154 |
| size only | 0.388 |
| half the features | 0.384 |

| Option | Tolerance | Seed pairs passing | Planted failing |
|---|---:|---:|---:|
| 0 | 0 | 10 of 20 | 3 of 3 |
| 2 sd | 0.0043 | 19 of 20 | 3 of 3 |
| 2√2 sd | 0.0060 | **20 of 20** | **3 of 3** |

Picked: **2√2 sd** (`[gate] noise_sds = 2.83`), the only option that gets both. "2 sd" blocks seed 4 against seed 2 (0.0049 apart). "Below at all" blocks half of all honest retrains. The bar alone wouldn't catch two of the planted models: size only and half the features both beat lines × files (0.388 and 0.384 against 0.319). The champion check is what stops them. MLflow: `db2e35e7da6d4a0099bbb88663a251d6`.

## The gate on the final pick

`python -m model.gate`: 5 of 5 checks pass (inputs, agree, bar +0.087, bias 0 open flags, champion: none yet, so the first model). `run_all --fake` now runs the gate too: 6 steps in 22 s.

---

# Nov 10: the rules for decisions 7 and 8, before the runs

## 7. What CI trains

Options: (a) the full search, `run_all` as it is: `train.py --tune logreg forest lgbm` with Rachna's trial counts, then seed noise, evaluate, bias, gate, registry. (b) The committed candidate: `train.py --save-model` refits `reports/candidate.json`'s kind and params on the train rows, and fails unless the refit's `val` scores equal the committed ones (Rachna's decision 9, to 1e-9). Then seed noise, evaluate, bias, gate and registry, as in (a).

Test: wall time of each, on this laptop (8 cores) and on the GitHub runner (4 cores), from the real runs.

Rule: PASS-BARS §6.5 gives a PR run at most 15 minutes. (a) on every run if it fits. If (a) takes more than 15 minutes on the laptop, it can't fit on a runner with half the cores, so (b) runs on PRs and pushes, and (a) runs only when started by hand (`workflow_dispatch`, `retune: true`), since tuning belongs to Rachna's runs (packet). (b) still has to come in under 15 minutes on the runner, measured as the median of 3 runs.

## 8. When CI runs

Options: every push to any branch; PRs only; PRs plus pushes to main.

Test: commits touching `Model-Pipeline/**` per day from `git log`, over Nov 5–9, times the run length from decision 7.

Rule: main must run, because it registers and pushes, and only main can sign in to Google Cloud. PRs must run, because the gate has to judge a change before it merges. So it's PRs plus main unless the count shows main and PR runs together can't be run daily. Every option is filtered to `Model-Pipeline/**` and the workflow file, and `workflow_dispatch` is added for a run by hand (Rochan).

## 9. LLM slices

Options: repo, owner team, task kind.

What exists, looked at before writing this (Nov 10). Gate fallback: the committed LLM cache is empty and no run has had an LLM, so every writer text so far is its template, with no LLM attempt behind it. That's not a fallback. Fallbacks are counted from `log_llm_call` rows (agent, repo, team, task, `gate_pass`, `source`), one text per `facts_hash`, so "template because the LLM is off" is never counted as "template because the gate failed". Diff match: Utkarsh's results (`packets/utkarsh-data/diffs.md`) are counts per repo, at each repo's drafting style: Django's ugettext 254 of 267, and the packages' `url()` 612 of 1,236. Five packages have their own counts, and the other 15 only a total.

Test: texts (fallback) and drafted lines (match) per slice, under each option, on the fixture and on the real block.

Rule: every slice with at least 30 (`[bias] min_n`) is judged by phase 1's rule (`[bias] max_gap`), with the fallback rate's harm upward and the match rate's downward. Every slice under 30 is listed with its N as not judged. A repo whose count isn't published goes in `(other)` with the rest, and the report names them. A flagged slice is accepted only when its owner's `decisions.md` has a fix scored on it. Repo and task kind are judged where the counts exist. Owner team only where a source gives counts per team. Otherwise the report says it can't be judged, and why.

---

# Nov 10: results

## 7. What CI trains: the runner decides

On this laptop (10 cores, not the 8 the rule says; the rule's reasoning holds either way): **(a) the full search, 310 s** (logreg 50 trials, forest 100, LightGBM 100, each stopped flat; then seed noise, evaluate, bias, gate). **(b) the refit, 15 s.** (a) picks the same candidate as the committed one: forest with the same params, and its `val` scores equal the committed ones to 1.1e-16.

(a) is under 15 minutes on the laptop, so by the rule the runner's time decides. The first PR run (#63, `ubuntu-latest`, 4 cores) ran (a): **the pipeline step took 27 min 40 s** (01:31:16 to 01:58:56 UTC, Nov 11; building the image took 2 min 38 s more), green end to end. That's over 15 minutes, so **(b) on PRs and pushes** (`TRAIN: refit`). (a) stays for a run started by hand (`workflow_dispatch`, `train: search`). (b)'s runner times, median of 3: below.

## 8. When CI runs: PRs plus main

Commits touching `Model-Pipeline/**`, all branches: Nov 5: 30, Nov 6: 22, Nov 7: 17, Nov 8: 8, Nov 9: 28. Merges to main: 2 to 9 a day. A PR run per pushed commit plus one per merge is at most about 40 runs a day. At (a)'s laptop time, that's under 4 runner-hours, and the repo is public, so GitHub's runners cost nothing. Nothing argues against running both, so it's **PRs plus main**, filtered to `Model-Pipeline/**` and the workflow, plus `workflow_dispatch`. A newer push to a PR cancels that PR's older run. Main's runs are never cancelled, since they register.

## 9. LLM slices

`python -m model.llm_bias` → `reports/llm_bias.json`, `reports/llm_bias_match.png`.

**Gate fallback: 0 LLM texts, nothing judged.** No `meridian-agents` run has logged a call, in this store or in CI's, since the committed cache is empty and nothing runs with a key. So repo, owner team and task are each listed with N = 0. On the fixture the writer has 14 tasks, on 1 repo and 6 teams (`tests/test_agents.py`). Even with the LLM on, no slice there could reach 30. `llm_bias.py` counts fallbacks from the calls, one text per `facts_hash`, so the first run with a key gives real numbers with no change. A planted team whose 40 texts all fall back is flagged, and one at 29 is listed, not judged (`tests/test_llm_bias.py`).

**Diff match, per repo** (Utkarsh's counts, `diff_match_by_repo.csv`; overall 866 of 1,503 = 0.576):

| Repo | Matched / lines | Rate | 95% interval | Judged | Flagged | Power at a 0.15 gap |
|---|---:|---:|---|---|---|---:|
| django (ugettext) | 254 / 267 | 0.951 | 0.918–0.971 | yes | no | 0.39 |
| wagtail | 194 / 240 | 0.808 | 0.754–0.853 | yes | no | 0.36 |
| DRF | 129 / 182 | 0.709 | 0.639–0.770 | yes | no | 0.27 |
| allauth | 42 / 50 | 0.840 | 0.715–0.917 | yes | no | 0.08 |
| debug-toolbar | 26 / 29 | 0.897 | 0.736–0.964 | **no: 29** | — | — |
| oscar | 81 / 247 | 0.328 | 0.272–0.389 | yes | **yes** | 0.36 |
| (other): the 15 packages without published counts | 140 / 488 | 0.287 | 0.249–0.329 | yes | **yes** | 0.59 |

By task: ugettext 0.951 (267 lines), `url()` 0.495 (1,236), neither flagged. Owner team: not judged, since the counts are per repo, not per team.

**The two flags are accepted** (`[llm_bias] accepted`). Utkarsh's decision 11 scored every `url()` style on all 20 packages, so on both flagged slices: the best single style gives 49.5%, and each repo's own best style, known only afterwards, about 68%. The LLM path can add at most 20 lines (1.6 points). The fixes were tried and written down, and none closes the gap. The cause is that each real migration chose its own style, so this is codemod output, not an LLM's. Oscar's numbers under the other styles aren't published per repo. The report says so.

**(b) changed after its first runner run, and why.** As first written, (b) refit the committed candidate and failed unless the refit equalled the committed `val` scores to 1e-9. On the runner it failed at train (#63, run 42943059501). Those scores were made on a laptop, and the image (Linux) doesn't reproduce them that exactly. In (a)'s run the same check passed, because there it compared against scores made in the same run. So (b) is now `train.py --retrain logreg forest lgbm`: `--tune` without the search, fitting each kind with its committed `tuning_<kind>.json` best params, then scoring, picking and saving on the same machine, as S8 says the numbers should be made. On the laptop: 25 s, the same forest, the gate passes. It's Rachna's file, so she's told.

(b) as `--retrain`, on the runner: **pipeline step 63 s** (#63, run 42944525502; the job about 4 min with the image build), green.

**(b)'s median of 3 (Nov 12).** Which runs count was fixed before computing: the first three green PR runs of (b), in order, so none is picked. Every later green PR run is listed for the record. Times are from GitHub's API (`actions/runs/<id>/jobs`): the run from start to finish, which is what a PR waits for, with the pipeline step and the image build inside it.

| Run | PR branch | Whole run | Pipeline step | Image build |
|---|---|---:|---:|---:|
| 42944525502 | chaitali-ci | 242 s | 63 s | 153 s |
| 42945332188 | chaitali-ci | 207 s | 46 s | 136 s |
| 42946298156 | chaitali-ci | 246 s | 67 s | 159 s |
| **Median of 3** | | **242 s (4.0 min)** | **63 s** | **153 s** |

**The bar passes: 4.0 minutes against 15.** All 24 green PR runs of (b) from Nov 11 to 12, across 15 branches: median 250 s, range 206–317 s (5.3 min), pipeline step 44–74 s. The image build is about two thirds of every run. Runs that were cancelled (a newer push) or stopped at the gate on purpose (#64) aren't timed.

## PASS-BARS §6.5 runs

- Green: #63, run 42944525502 (retrain), and run 42939195787 (full search). Both stop at the gate, as PRs must: the registry steps were skipped.
- Red at the gate on a planted bad model: #64 (`chaitali-planted-bad`, shuffled train labels, never merged), run 42944590671. "FAILED at gate": bar 0.1744 vs lines × files 0.3191, and bias with 2 open flags (`component=Pandas API on Spark` and `Structured Streaming`, false-positive rate). Nothing was registered.
- Slack: no `MERIDIAN_SLACK_WEBHOOK` in the repo yet, so these runs posted to their summaries only. A fail, a done and a push message from real runs are still to come.

---

# Nov 11: GAPS.md gap 6, the Q2 label audit: the rule, before any run

The model's worst flag is `size_bucket=Q2` recall: 0 of 51 `val` positives found. GAPS.md asks whether those 51 labels are right. SZZ blames the lines a fix changed, so a small commit may be blamed for a bug it didn't cause. On Defects4J, SZZ picks the right commit only 44% of the time. The audit estimates label noise for the bias section. **It never changes a label, and it never becomes training data** (GAPS.md: an LLM may audit labels, never write them).

## 11. How noisy are Q2's positive labels?

What's audited: each of the 51 Q2 `val` positives, with the fix that blamed it (`bug_inducing`, v1 block `70dd1dabfed544b8`; all 51 have one; c5118's SZZ table differs by 1%, per Utkarsh). For comparison, 50 Q4 `val` positives (`random.seed(1)`), since a noise share means little without one. 2023 only. 2024 is never read.

The question, per (commit, fix) pair: did this commit introduce the bug this fix repairs? The model sees only public Apache code: the fix's ticket key, its message and its diff on the blamed file, plus the blamed commit's message and its diff on that file. Lines that name a person (`Signed-off-by`, `Authored-by`, `Closes #… from <user>`) and every email address are removed first. It answers JSON `{"verdict": "yes" | "no" | "unclear", "reason": "…"}`. A commit's label holds if any of its pairs is `yes`.

Model: one strong model, fixed before the run: `claude-opus-5-5` through `meridian.llm` if our Google Cloud project can call it, else `gemini-pro`. The output names which. Each answer is cached and logged with `log_llm_call` (agent `label_audit`).

Hand check: 20 of the 51 Q2 commits (`random.seed(1)`), judged by Chaitali **blind**, on a sheet that doesn't show the model's answer, from the two GitHub commit pages and the Jira ticket.

Rule:
- Q2's label-noise estimate is the share of `no` among the commits the model judged (`unclear` left out, and counted), with its Wilson 95% interval. Q4's is computed the same way.
- The model's answer counts only if it agrees with the hand check on at least 15 of the 20 (75%, `unclear` counting as a disagreement). Otherwise the report gives only the hand check's share, of 20, with its interval, and says the model wasn't good enough to use.
- "Q2 is more noisily labelled than Q4" is said only when Q2's interval sits wholly above Q4's. Otherwise the report says they can't be told apart at this N.

# Nov 11: GAPS.md gap 16, the context builder: the rule, before any run

Today the writer's consumer task gets every file a stalled team has sites in. The context builder keeps the riskiest files and cuts the rest down to counts, so the agents read less.

## 12. Where to cut a team's context

Looked at before writing this (structure only): the real block `70dd1dabfed544b8` has call sites in 21 repos, all in the Django ecosystem, none in Spark. The champion was trained on Spark only, and P9 keeps lines × files for every other repo (Rachna), so **the champion's scores aren't used here**. A file's risk is its bug fixes in the last year (`file_bug_fixes`, the number the consumer facts already carry). The champion joins when a stalled team's sites are in Spark.

What's cut: each consumer task's `files` on the real block (68 teams). The stall-reason tasks list reasons with evidence counts, not files, so they have nothing to cut, and the report says so.

Rule:
- A team's files are ranked by bug fixes (most first), then sites left, then path. Files are kept down the ranking until the kept ones hold at least 90% of the team's bug fixes. At least 1 file is always kept, so a team whose files have no bug fixes keeps its file with the most sites.
- What's cut stays in the facts as counts (`files_cut`, `sites_cut`), so `left` and the text stay true. Nothing cut is ever named.
- Reported over all 68 teams: files, sites and facts characters (sorted-key JSON, what the prompt carries) kept against the full context. Also bug-fix coverage, and the same coverage reached by a random file order (seed 1, mean of 100 orders), to show what the ranking buys.
- There's nothing to pick. It's a measurement. It isn't switched on in `agents.py` here, since a new facts shape changes every cache key under Vishwa's running comparison. Switching it on is a separate decision, after gap 2.

## 12. Results: the cut has nothing to rank on the real block

`python -m meridian.context --run-id 70dd1dabfed544b8 --db ../published/70dd1dabfed544b8.sqlite` → `reports/context_cut.json`.

**On the real block no file has a risk signal.** `file_bug_fixes` holds 1,492 files, all in `apache/spark` (its Jira bug tickets). The 21 Django-ecosystem repos the call sites live in have no bug-fix history in the block, and all 1,526 call sites have `bug_fixes = 0`. The champion is Spark-only too. So the rule's fallback decides every team: each keeps its file with the most sites.

| Real block, 68 consumer tasks | All | Kept | Kept share |
|---|---:|---:|---:|
| Files | 367 | 68 | 18.5% |
| Call sites | 1,526 | 444 | 29.1% |
| Facts characters | 36,676 | 13,170 | 35.9% |
| Bug fixes covered | 0 | 0 | not defined |

42 of the 68 teams have more than one file and get cut, and 26 have one file and keep it. The cut would save 64% of the consumer facts. But with no bug fixes to cover, **nothing shows the kept files are the risky ones**, so "90% of the likely problems covered" can't be measured here. It isn't switched on.

**On the fixture** (`acme/shop`, which has bug-fix history): 5 teams, 6 files, 5 bug fixes. The ranking keeps all 5 bug fixes with 5 of the 6 files. A random order with the same files keeps 63% (seed 1, 100 orders). The ranking works where the signal exists, but at N = 6 files that shows the code works, not a result.

What would make it useful on the real block: bug-fix history for the package repos (their GitHub issues and fix commits, as `file_bug_fixes` has for Spark's Jira), or a Spark migration. That goes in the report as an open gap.

# Nov 11: GAPS.md gap 7, LLM bias on real texts: the rule, before computing

Vishwa's writer ran the real block's 204 tasks (`70dd1dab`) on 4 models, each alone (gap 2, PR #77). The answers are in `llm_cache/`, and Gemini 3.8 Flash (`gemini-flash`) won his rule at 98.0% gate pass. His calls were logged on his machine, so here the writer replays each model from the cache (`--models <m> --strict-cache --calls`), with 0 paid calls and a miss stopping the run. That gives each answer's row (repo, team, task, `gate_pass`, `source`) under today's gate (his decision 5b).

Rule, adding to decision 9:
- **The bar is judged on the model the writer ships, `gemini-flash`.** One text per (agent, `facts_hash`), as `llm_bias.py` counts it. It fell back when its answer didn't pass the gate and the template shipped.
- Slices: repo, owner team, task kind. `[bias] min_n` (30) and `max_gap` (0.10), harm upward. Every slice under 30 is listed with its N, not judged.
- The other 3 models get the same slices, for the record only: they don't ship, so they don't decide the bar.
- A flagged slice on `gemini-flash` is accepted only with a fix tried and written down (Vishwa's decision 5 and 5b are gate fixes, and count if they were scored on that slice). Otherwise it's an open flag, and the report says so.

## Gap 7: results: no fallback slice flagged, N > 0

Replayed from `llm_cache/` with 0 paid calls (`python -m meridian.agents --run-id 70dd1dabfed544b8 --db ../published/70dd1dabfed544b8.sqlite --models <m> --strict-cache --calls <m>.jsonl --quiet`, one run per model), then `python -m model.llm_bias --calls <the 4 files> --ship gemini-flash`. The replay gives Vishwa's own pass rate for the shipped model (98.0%), so it reproduces his run.

**The shipped model, `gemini-flash`: 4 of 204 texts fell back to the template (0.020). No slice is flagged.**

| Slice | Judged (N ≥ 30) | Fallback | 95% interval | Power at a 0.15 gap |
|---|---|---:|---|---:|
| repo `django/django` | 144 texts | 1 (0.007) | 0.001–0.038 | 0.48 |
| task `consumer` | 68 | 3 (0.044) | 0.015–0.122 | 0.26 |
| task `stall_reason` | 68 | 0 (0.000) | 0.000–0.053 | 0.26 |
| task `ticket_draft` | 68 | 1 (0.015) | 0.003–0.079 | 0.26 |

Not judged (decision 9: listed with N): the other 20 repos (60 texts between them) and all 43 owner teams (none reaches 30). So **the bar passes, but it can say little**: the judged slices' power is 0.26–0.48, so a true 0.15 gap would be missed more often than caught. And teams, the slice the bar names, can't be judged on this block at all.

The other models, for the record (they don't ship): Gemma 4 0.078, Llama 4 Scout 0.069, Llama 4 Maverick 0.093. None has a flagged slice. Their worst judged slice is `consumer` (Gemma 4: 12 of 68 = 0.176), the same task kind that's worst for `gemini-flash`.

Diff match is unchanged from Nov 10: oscar and `(other)` flagged and accepted. `reports/llm_bias.json` passes, with 0 open flags.

---

# Nov 12: PASS-BARS §6.5 "product jobs fail when they should": the rule, before the runs

The bar: a view that breaks with the LLM off fails the job, a cache miss in CI fails the job, and no job has an LLM key. Each is planted once on a branch.

What exists, looked at before writing this. `ci.yml` runs every Data-Pipeline test, and they run with the LLM off: `tests/test_views.py` renders every page that way, and `tests/test_agents.py` and `tests/test_diffs.py` check that `strict_cache` raises `CacheMiss`. Nothing checks the workflows for LLM keys. The only cloud sign-in in any workflow is `model.yml`'s, on main, after every step that could reach an LLM, for the registry push.

What's added: `tests/test_ci_keys.py`. It fails when any workflow names an LLM credential (`settings.toml [llm] project_env`, which is `MERIDIAN_GCP_PROJECT`, or a provider's API key variable), or when a step that runs an agent or `llm_bias` comes after the cloud sign-in.

The plants, on `chaitali-planted-product`, one commit at a time, each run left to finish before the next. Each commit undoes the one before, so every red run has one cause:
1. **LLM off:** `views.py` raises on a text whose source is the template, which is every text with the LLM off.
2. **Cache miss:** `agents.write` ignores `strict_cache` and ships the template on a miss.
3. **LLM key:** `ci.yml`'s `test` job gets `MERIDIAN_GCP_PROJECT` from a secret.

Rule: each plant passes the bar only if its own `ci.yml` run is red, failing on that plant's test on both Linux and Windows. A green run is a failed bar, and the report says so. The branch is never merged, and its draft PR is closed after the third run.

## Product failures: results: all three plants red

On `chaitali-planted-product` (#87, a draft, never merged), one plant at a time, each run finished before the next push. Each run failed only `ci.yml`'s `test` job, on both Linux and Windows, at the unit-test step. Every other job (`model`, `gx`, `pushcheck`, `airflow`) was green. The runs' logs need a GitHub sign-in, so which tests failed was checked by running the same suite on the same commit locally. Each plant failed only its own test:

| Plant | Commit | Run | `test` job, Linux / Windows | Failing tests (local, same commit) |
|---|---|---|---|---|
| 1. A view that raises with the LLM off | 66585fa | 43294003464 | red / red | 16 errors, all in `test_views` |
| 2. A cache miss ships the template | 83aaa3a | 43296868897 | red / red | 1: `test_agents` `test_a_miss_with_strict_cache_stops_the_run` |
| 3. `MERIDIAN_GCP_PROJECT` in `ci.yml` | 7b6357c | 43300353924 | red / red | 1: `test_ci_keys` `test_no_workflow_has_an_llm_key` |

**The bar passes for all three.** Without the plants (#86), every job is green, and `test_ci_keys` passes on today's workflows.

Limits: plant 3 shows a key *named* in a workflow is caught. It's a text check, so a key added some other way (an org-level variable, a credentials file) wouldn't be. Main's Workload Identity sign-in is allowed because it comes after every LLM step. Whether `meridian-ci`'s roles could call Vertex AI wasn't checked.

---

# Nov 13: the report's numbers on the shipped champion: the rule, before computing

Aniket's integration check (Nov 12): the champion CI built and committed (`registry/champion.json`, `model.joblib` md5 `2a0c9cc6…`) has `val` PR-AUC 0.4067. The `reports/` files in git, which the bias section quoted, are from the laptop's forest, at 0.4058. They're the same kind and the same params, and they differ by 0.0009, under one seed SD (0.0021). The report should quote the model that ships.

What's run: `registry/model.joblib` is scored as it is on today's `val` rows (`registry.rescore`'s path, no refit, S6). Its scores go through `model.bias` unchanged, into `reports/champion/`. The laptop's `reports/bias_report.json` stays as the record of the bias run judged on Nov 9.

Rule:
- The report's overall numbers, slice table and Bonferroni count become the champion's.
- If the champion is flagged on the same 5 slices as the laptop forest, `[bias] accepted` stands as it is. The mitigation table stays the laptop forest's, said so in the report. Its re-sampling options refit, and a refit to get scores isn't done.
- If any flag is new, it's an open flag. The bias report fails on it, and the report says so. Nothing is accepted after seeing it.

## The shipped champion: results: the same 5 flags, nothing new

`registry/model.joblib` (md5 `2a0c9cc6…`), scored on today's `val` rows (features md5 `9c0ab220…`, the same as `champion.json`'s): PR-AUC **0.406668**, equal to `champion.json`'s. `python -m model.bias --scores reports/champion/champion_val_scores.csv --column forest --out reports/champion`:

| | Laptop forest (Nov 9) | Champion (CI) |
|---|---:|---:|
| PR-AUC | 0.406 | 0.407 |
| Recall / false-positive rate at 634 alerts | 0.516 / 0.106 | 0.519 / 0.105 |
| Q2 recall, PR-AUC | 0.000, 0.161 | 0.000, 0.151 |
| Q3 recall, false-positive rate, PR-AUC | 0.096, 0.032, 0.220 | 0.096, 0.034, 0.219 |
| Q4 recall, false-positive rate, PR-AUC | 0.803, 0.488, 0.499 | 0.806, 0.485, 0.500 |
| Flags (of 62 judged tests); under Bonferroni | 5; 3 | 5; 3 |
| Newcomers caught (of 16) | 9 | 9 |

**The same 5 size flags and none new, so `[bias] accepted` stands, and the bias report passes (0 open flags).** The 3 that survive Bonferroni are the same: Q2 recall, Q3 recall, Q4 false-positive rate. Power at a 0.15 recall gap: 0.10–0.56. The report section now quotes the champion. Its mitigation table stays the laptop forest's, and says so.
