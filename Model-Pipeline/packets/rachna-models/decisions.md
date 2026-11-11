# Rachna decisions

Each rule below was written before its run, as [packets/README.md](../README.md) requires. Each run is tagged
`decision=rachna-<n>` in MLflow. The Result and Picked columns are filled in after the run, with N.

Common terms:
- **PR-AUC** is on `val` (2023), with its N and positive rate.
- **Paired bootstrap interval** means Rochan's helper: 1,000 resamples of the same `val` rows, 95% interval of the
  difference. "Tied" means that interval contains 0.
- **Simpler** means logistic regression < random forest < LightGBM; fewer features < more; raw < calibrated.
- When a rule can't be run in time, the simpler option is picked and the reason is written here.

| # | Decision | Options | Test | Rule that picks (written before the run) | Result (N) | Picked |
|---|---|---|---|---|---|---|
| 1 | Model family | LR, RF, LightGBM | `val` PR-AUC of each, tuned the same way; paired bootstrap of each against the best | Highest PR-AUC wins. If a simpler model is tied with it, the simplest tied model wins | Tuned, `val` 2023, N = 4,113 (486 bug-inducing): LightGBM 0.413, forest 0.406, logistic regression 0.382. LightGBM − forest +0.007 [−0.005, +0.019]: tied. LightGBM − logistic regression +0.031 [+0.006, +0.052]: worse. Each against lines × files (0.319): `reports/tuned_val.json`. 2,000 resamples (Rochan's `[eval] bootstrap_reps`) | **Random forest**: tied with the best, and simpler. `reports/candidate.json` |
| 2 | Real reason to beat lines × files? | — (a finding, not a choice) | The winner on (a) all features, (b) no size features (`la`, `ld`, `nf`, `nd`, `ns`, `entropy`), (c) size features only; each against lines × files, paired bootstrap | "Beats for a real reason" only if (b) beats lines × files (interval above 0). If (c) is tied with (a), the report says the model is mostly size | | |
| 3 | Class imbalance | none, `class_weight="balanced"`, `scale_pos_weight` | `val` PR-AUC and Brier score of each | Among options tied with the best PR-AUC, the lowest Brier wins; if Brier differs by < 0.001, `none` | | |
| 4 | Calibration | raw, isotonic, Platt (fitted on 2022 only) | Reliability curve and Brier on `val` | Lowest Brier wins; if within 0.001 of raw, raw. Calibration never changes the ranking, so PR-AUC must not move | | |
| 5 | Training window | 2015–2022, 2019–2022 | `val` PR-AUC of each, paired bootstrap; fold-to-fold spread | The shorter window wins only if its interval is above 0. Tied: 2015–2022 (the plan's default, more data) | Val N = 4,113: 2015–2022 (26,050 train rows) PR-AUC 0.406; 2019–2022 (11,792) 0.364. 2019–22 minus 2015–22: −0.041 [−0.059, −0.022], 2,000 paired resamples. Matches the learning curve in GAPS.md: the older years carry more complete labels. `reports/window.json` | 2015–2022: the shorter window is clearly worse, not tied |
| 6 | Number of trials | 50, 100, 200 | Optuna best-so-far curve | Start at 50. Add 50 at a time, up to 200, while the last 30 trials improved the best by ≥ 0.002. Stop at the first batch that doesn't | Logistic regression 50 trials, forest 100, LightGBM 100: each stopped `flat` (the last 30 trials added < 0.002). Best mean fold PR-AUC 0.315, 0.328, 0.328. Curves in `reports/tuning_<kind>.json` | 50, 100, 100: the rule stopped each one; none needed 200 |
| 7 | Seed noise | — | Chosen config, seeds 1–5 | Mean and standard deviation of `val` PR-AUC into `reports/seed_noise.json`. No choice; Rochan and Chaitali use the SD | Forest, the candidate's config, seeds 1–5, `val` N = 4,113: mean 0.4044, SD 0.0021 (2 SD = 0.0042). `reports/seed_noise.json` | — |
| 8 | Ticket features | keep `fix`, `ticket_type`, `has_ticket`; drop | `val` PR-AUC with and without, paired bootstrap | Drop unless keeping them is better with an interval above 0 (they can leak: ticket type is as of the pull) | | |
| 9 | Determinism | — | Train twice in the image, once on a laptop; compare every `val` prediction | Pass if the max absolute difference is ≤ 1e-9. Otherwise set `deterministic=true`, fixed `num_threads`, and rerun until it passes | | |
| 10 | SHAP sample size | all of `val`, 5,000, 2,000 | Spearman correlation of the top-10 feature ranking against all of `val` | The smallest sample with Spearman ≥ 0.9 | All of val (N = 4,113) against seeded samples: Spearman of the top-10 mean \|SHAP\| 1.000 at 2,000, 1.000 at 1,000, 0.988 at 500. SHAP additivity error 1.9e-15. `reports/sensitivity.json` | 500 |
| 11 | Correlated features | SHAP alone, plus grouped permutation | Permutation with `la`/`ld` and `nf`/`nd`/`ns` permuted as groups | If a group ranks in the top 3 by permutation but not by SHAP, the report uses the grouped ranking for that group and shows both | Grouped permutation on val: lines (la+ld) −0.135 PR-AUC, spread (nf+nd+ns) −0.070, age −0.023. Rankings agree with SHAP's grouped importance (Spearman 0.77); no group in permutation's top 3 is missing from SHAP's | SHAP alone is fair here; both rankings in the report |
| 12 | Sweep range | ±25%, ±50%, full range | One-at-a-time sweep of the top 2 parameters | "Safe to retrain" if PR-AUC varies by ≤ 2 × seed-noise SD across ±50%. Otherwise the report names the peak | fANOVA: max_features 0.91, max_depth 0.07, min_samples_leaf 0.01, n_estimators 0.00. ±50% spreads of val PR-AUC: min_samples_leaf 0.0028, n_estimators 0.0069 (bar 2 × SD = 0.0043). The two categorical settings have no ±%: choice spreads 0.0065 (max_features), 0.0048 (max_depth), reported only | min_samples_leaf safe; n_estimators over the bar by the rule. Note: fANOVA gives it 0.00, and five noisy points span ~0.005 by chance alone |
| 13 | Jev against rules | Jev picks, rules pick | Same fixture tasks both ways: pass rate and cost | Jev stays only if its pass rate is within 2 percentage points of rules' and its cost is lower (05-test). Otherwise rules, and the report says so | | |
| 14 | Jev's model | the small model, the mid model (`meridian.toml [llm]`) | Pick accuracy on the held-out half of the Jev task set (decision 19), and cost per task | The small model unless the mid model gets ≥ 3 more of the 30 held-out tasks right | | |
| 15 | P9 threshold | 05-test's alert budget, F1-tuned | Replay of the 21 migration commits and `val`: alerts per push, precision | The threshold must meet median ≤ 1 alert per push and precision ≥ 0.80. F1-tuned only if it also meets both; otherwise the budget threshold | Replay of the 21 migration commits through the push check (`python -m meridian.replay`, v1 block): 19 checked, 2 skipped over the scope limit (Django's conversion, django-cms), 7 alerts, all P9 lines × files, 0 to 1 per push: median 0, meets 05-test's median ≤ 1. Precision ≥ 0.80 isn't reached on val by any rule: lines × files alone 0.42 (206 alerts), the model alone 0.62 (42), both 0.62 (decision 15b). Same pushes as phase 1's replay, same result | The alert-budget threshold stays (lines × files > 2,000): it meets the alerts-per-push bar; the 0.80 precision bar isn't met, and the report says so. F1 tuning wasn't run: it would only flag more |
| 16 | Cheaper model | each model in decision 1, at its tuned size | Train time, time to score 10,000 commits, model file size, all on the same laptop; `val` PR-AUC | Already folded into decision 1: a cheaper model that's tied wins. This row records the cost table so the report can answer "could it be cheaper?" with numbers | | |
| 17 | Feature pruning | all features, the smallest set that holds | Backward elimination on the time folds inside train (never `val`): drop the least important group, refit, repeat | The smallest set whose mean fold PR-AUC is within 1 seed-noise SD of all features. Then one check on `val` | | |
| 18 | Tree compression | LightGBM `max_bin` 255, 63, 15 (feature quantization); trees capped by early stopping on the last train fold (pruning) | `val` PR-AUC, model file size, scoring time | The smallest, fastest setting within 1 seed-noise SD of the full model | | |
| 19 | Jev's task set | — | 60 fixture tasks labelled by hand (which agent and model a person would pick): 30 to write the prompt, 30 held out | The prompt is tuned only on the first 30. Decisions 13 and 14 are scored only on the held-out 30, so the prompt can't overfit its own test | | |
| 20 | More model families | decision 1's three, plus `HistGradientBoostingClassifier`, `ExtraTreesClassifier`, `MLPClassifier` (all scikit-learn, nothing new to install) | `val` PR-AUC of each, tuned the same way; paired bootstrap against the best | Same as decision 1: highest PR-AUC wins, and the simplest model tied with it wins. If a new family wins, decisions 3-18 are rerun on it | | |
| 21 | Quantized open model for Jev | the hosted small model, a 7-8B open instruct model at 4-bit (GGUF, llama.cpp or Ollama), run on CPU | Pick accuracy on decision 19's held-out 30 tasks, cost per task, p95 latency on a CPU runner | Local only if it gets within 3 of the 30 tasks of the hosted model and its p95 latency fits CI's budget. Otherwise the hosted model | | |

## A rule added after the first results (written before its own run, and before 2024)

| # | Decision | Why it was added | Test | Rule (written Nov 11, before this run) | Result (N) | Picked |
|---|---|---|---|---|---|---|
| 15b | P9 with the model: model AND lines × files | The model alone reaches precision 0.70 on 30 val alerts, under the 0.80 bar, so P9 can't alert on it alone (GAPS.md gap 4) | `python -m model.p9rule` on the champion's judged val scores, no refit | Alert only when the model's score ≥ its cutoff AND lines × files ≥ its cutoff. Cutoffs: each signal's score at the val commit ranked in the top 1%, 2%, ..., 30% (900 pairs). Eligible: ≥ 30 val alerts. Pick: the highest precision; ties go to more alerts, then the larger lines × files cutoff. Adopted only if its precision ≥ 0.80; otherwise P9 stays lines × files and the report gives 0.70. The best of 900 on the same rows is optimistic: the 2024 run checks it and doesn't change it | 890 of 900 pairs have ≥ 30 val alerts. Best: precision 0.619 on 42 alerts (Wilson 0.47–0.75), recall 0.053, at the model's top 1% and lines × files ≥ 7,364: the same 42 commits the model flags alone, since all are already large. Model alone on this grid: 0.619 (42); lines × files alone: 0.422 (206). Rochan's finer search reaches 0.70 on exactly 30. `reports/p9_rule.json` | Not adopted: P9 stays lines × files. The two signals agree on the risky commits rather than catching different false alarms |

## Data notes

| Date | What | Handling |
|---|---|---|
| Nov 6 | `age` is below 0 on 8 of 41,163 rows of the c5118 table (train 6, val 1, test 1), min −1.94 days. Found by the first real `--untuned` run: `log1p` of the one value below −1 made a NaN, and logistic regression stopped | `feature_matrix` reads `age` below 0 as 0 for every model: "days since the files last changed" can't be negative, and under 2 days below 0 is author dates out of order (rebases, cherry-picks). `check_table` now refuses a negative count or flag, and `--check` reports the negative `age` rows. Told Utkarsh, who owns the table |
| Nov 7 | Tuned logistic regression scored 0.382 on `val`, below untuned (0.390). Tuning maximises the mean over the folds inside train (2020–2022), not 2023 | Kept as measured, not re-tuned on `val` (that would leak it). A small sign that 2023 differs from earlier years: decision 5 (training window) tests it |

## Options considered and not run

| Idea | Why not |
|---|---|
| Fine-tuning a language model for Jev | The hosted models can't be fine-tuned on our account, and 60 labelled tasks are far too few to fine-tune anything. Rules already cost $0, so the cheapest option is measured in decision 13 |
| Deep learning on the commit features | 25 tabular features and about 30,000 labelled commits: gradient-boosted trees are the standard for this, and Kamei et al.'s set is tabular |

## My pass-bar rows (for Chaitali's file)

| What | Bar |
|---|---|
| Model ships | 2024 PR-AUC beats lines × files, paired bootstrap interval of the difference above 0. Otherwise the rule stays |
| Model determinism | Same data, config and seed: every prediction within 1e-9, laptop and image |
| Model sanity | Shuffled labels: `val` PR-AUC below the 99th percentile that random scores reach on the same rows (`chance_ceiling`), and ROC-AUC within 0.5 ± 0.03, median of 3 shuffles |
| Jev | Same problem twice: same pick. Jev down or over the spend cap: rules pick, the run completes. Beats rules on cost at an equal pass rate |
| P9 with the model | Replay: median alerts per push ≤ 1, precision ≥ 0.80. A model that fails to load falls back to lines × files and says so |
