# The defect-risk model (Rachna)

The push check's P9 asks one question of every push: is this commit likely to introduce a bug? Today's rule is lines × files (lines changed times files changed). The model pipeline asked whether a trained model answers it better. Every choice below was written down, with the rule that picks it, before its run ([decisions.md](decisions.md)). The 2024 test year was never read by any of it.

## Data and split

The table is Utkarsh's Contract 1 table on the c5118 run: one row per first-parent Apache Spark commit, 15 Kamei-style features (size, history, experience) plus its ticket's type, and an SZZ label (did a later bug fix blame this commit's lines?). The split is by time, so a model never trains on a year it's judged on: train 2015–2022 (26,050 commits), val 2023 (4,113 commits, 486 bug-inducing, 11.8%), test 2024 (3,762, unread). Inside train, three expanding time folds tune the models.

## What the models reach

| Val 2023, N = 4,113 | PR-AUC | vs lines × files, paired bootstrap |
|---|---|---|
| Chance (the positive rate) | 0.118 | |
| **Lines × files** (today's rule) | **0.319** | |
| Churn (lines only) | 0.316 | −0.003 [−0.015, +0.009]: tied |
| Prior bug fixes | 0.222 | −0.097 [−0.130, −0.063] |
| Logistic regression, untuned | 0.390 | +0.071 [+0.043, +0.098] |
| Random forest, untuned | 0.407 | +0.088 [+0.060, +0.116] |
| LightGBM, untuned | 0.394 | +0.075 [+0.041, +0.107] |
| LightGBM, tuned | 0.413 | |
| **Random forest, tuned (the champion)** | **0.406** | |
| Logistic regression, tuned | 0.382 | |

All three model families beat lines × files with their intervals above 0, even untuned. Optuna tuned each one on the train folds, adding 50 trials at a time while the last 30 still improved the best by at least 0.002: logistic regression stopped at 50 trials, the forest and LightGBM at 100. The tuned forest is the champion: LightGBM is 0.007 higher, but the paired interval of the difference, [−0.005, +0.019], contains 0, and decision 1 gives a tie to the simpler model. Five seeds of the forest give PR-AUC 0.4044 with SD 0.0021, so a gap under about 0.004 between two runs is noise; Rochan's promotion rule and Chaitali's gate both use that number.

Tuned logistic regression (0.382) came out below untuned (0.390). Tuning maximises the train folds (2020–2022), not 2023, and we didn't re-tune on val, which would leak it.

## The ceiling: it's the data

**Accuracy is meaningless here.** The forest is 88.5% accurate, and always saying "no bug" is 88.2%. That's why every number above is PR-AUC.

The forest's ROC-AUC is 0.826 and its PR-AUC is 0.407: **3.4× chance**. Three different model families land within 0.004 ROC-AUC of each other, and tuning flattened after 50 to 100 trials. When very different models stop at the same place, the limit is the data, not the model. Three checks point the same way:

- **The labels are noisy.** Against 130 hand-verified bug-inducing commits (Defects4J and Fonte), SZZ picks the right commit only **44%** of the time. Most positive labels in the table point at the wrong commit.
- **Recent labels are incomplete.** A bug fixed after the snapshot never labels the commit that caused it: 38% of 2015's commits are blamed, 9% of 2022's. That's why training on more years helps: 2015–2022 reaches 0.406 against 0.364 for 2019–2022, a difference of −0.041 [−0.059, −0.022] (decision 5), and more rows of the same years add little (a random 25% of 2015–2022 gets 0.389).
- **The features never see the code.** They describe a commit's size, history and author, not what it changes.

## What the model learned

SHAP values on all of val are exact here (they add up to the model's output to 1.9e-15). The top five features are `la` (lines added), `nf` (files), `prior_bug_fixes`, `nd` (directories) and `ld` (lines deleted), and **63% of the model's attention is on commit size**. Permutation importance with correlated features shuffled together agrees (Spearman 0.77 with SHAP): shuffling lines added and deleted together costs 0.135 PR-AUC, and files, directories and subsystems together 0.070. 500 rows already give SHAP's full ranking (Spearman 0.988 against all of val, decision 10).

That matters for the honest question behind decision 2: SZZ blames the lines a fix changed, so big commits look risky partly by construction. The forest beats lines × files, but much of what it uses is size, measured better.

Of the forest's settings, `max_features` carries 0.91 of fANOVA's parameter importance. Moving `min_samples_leaf` ±50% changes val PR-AUC by 0.0028, under the 2 × seed-noise bar of 0.0043: safe to retrain. `n_estimators` moves it 0.0069, over the bar by the rule, although fANOVA gives it no importance and five noisy points span about 0.005 by chance alone.

## Why P9 stays lines × files

P9's bar is precision ≥ 0.80 on at least 30 alerts. On val, the model alone tops out at 0.62 on 42 alerts in our grid (0.70 on exactly 30 in Rochan's finer threshold search), and lines × files alone at 0.42. Decision 15b, written and committed before its run, tried alerting only when the model **and** lines × files both flag, over 900 cutoff pairs: the best is 0.62 on 42 alerts, the same commits the model flags alone, because the model's riskiest commits are already large. It isn't adopted, so P9 stays lines × files, and the push check never uses the model alone. Replaying the 21 real migration commits through the push check gives 7 P9 alerts on 19 checked pushes, a median of 0 per push, which meets 05-test's median ≤ 1.

## Recall-first models

Aniket's `model.recall` asked a different question: if an LLM agent reviews every flagged commit, a false alarm costs one agent call, but a missed bug is gone, so how few commits must be flagged to catch 90% of the bugs? The best, logistic regression, flags 46.9% against the champion's 48.6%, a difference of −4.6 to +3.0 points: a tie. The ceiling is the same either way.

## Engineering

The champion is saved with joblib only after a refit is shown to be the judged model: every val prediction matches its scores file to 2.2e-16. Its md5 is recorded for the registry and P9. `python -m model.run_all` runs data check, training, seed noise, evaluation and bias in order and stops at the first failing step; its fake mode runs every step on a synthetic table in 22 s on every pull request. No test-year row can reach any fit: the code refuses it, and tests check it.

## Jev, the LLM router

Jev picks which model starts each LLM task. Only picks from an allowed table count, and its one-line reason goes through the same fact gate as every LLM text. If anything fails, the rules pick (always start on the smallest model), so a run always completes. To test it, 60 real writer tasks from the block were labelled by hand with the model a person would start on, and the labels were committed before any model ran (decision 19). On the 30 held-out tasks, a person picked the smallest model 29 times. So the rules are already right on 29 of 30 for $0, and no router can beat them: Phi-4-mini running locally ties (29/30, $0, 2.3 s a pick), Gemma 4 ties (29/30), and Gemini 3.8 Flash does worse (26/30), over-picking big models three times, the costly kind of mistake. Every model gave the same picks when asked twice. By the rules written before the run, the rules route, with Phi-4-mini as Jev's model when a router is used (decisions 13, 14, 21). The whole comparison cost $0.055, and its answers are cached, so a rerun is free.

## Not done, and next

- Decision 2's size ablation (the model without size features against lines × files) isn't run. It's the most direct test of the question above.
- The 2024 test year runs once, on Nov 12, and changes no choice.
- For the next version: features from the diff itself (a small local code-embedding model, cached per commit, $0 a push), and a cleaner SZZ variant (Utkarsh's gap 13).
