# Rochan: decisions

Each decision has its test and the rule that picks, set before the run. Results go under each one after the run, with N. Runs are tagged `decision=rochan-<n>` in MLflow. Decisions 6, 7 and 9–11 are added on the day each is due (packet dates).

All timings below: one MacBook (Apple silicon), Python 3.11.15, `mlflow==3.18.0`, local SQLite store.

## Tracking choices

Not in the packet's table, but each one changes how fast or how safely everyone logs, so each was measured.

### T1. How `log_llm_call` stores calls

Options:
- **a. one MLflow run per call**, nested under the agent's run
- **b. append one row per call** to a table in the run (`mlflow.log_table`)
- **c. keep rows in memory**, write them once as one table when the run ends (and every 500 calls)

Test: 200 calls of the same shape, each way, into a fresh store. Time per call.

Rule: the fastest option that still keeps one row per call, readable later as a table.

Result (Nov 5, N = 200 calls each):

| Option | Per call | 200 calls |
|---|---|---|
| a. run per call | 7.4 ms | 1.48 s |
| b. append per call | 12.3 ms, and it grows with the table: each append rereads it | 2.46 s |
| c. in memory, one write | 0.06 ms | 0.012 s |

Picked: **c**. About 120 times faster than a run per call, and the UI shows one run per agent batch instead of thousands. What it costs: rows not yet written are lost if the process is killed. The run's `with` writes them on the way out, even on an exception (`test_a_failed_run_keeps_its_calls`), an exit hook writes them for runs ended without `with`, and no more than 500 rows are ever held.

### T2. Parallel writers on one SQLite file

Question: SQLite locks the file while writing. Do parallel processes (Optuna workers, agent batches) fail with "database is locked"?

Test: 8 processes, 25 runs each through `tracking.start_run`, all at once, into one store.

Rule: if any run fails, document "log from the parent process only". If none does, parallel processes can log directly.

Result (Nov 5): 200 of 200 runs written, 0 errors, 3.5 s. Picked: **log directly**. Threads in one process work too: 400 calls from 8 threads land as 400 rows (`test_calls_from_worker_threads`).

## Evaluation: decisions 1–5 (Nov 6)

Written before any of them ran. Each runs with `python -m model.evaluate --decide <n>` on `val` only, and logs a run tagged `decision=rochan-<n>`.

The candidate: Rachna's `candidate.json` isn't written yet. So these run on her untuned LightGBM from `train.py` (PR #34, commit 80330fe), on the c5118 table (`features_md5` 9c0ab220…), seed 1. Decisions 4 and 5 depend on N and the metric far more than on the model. When the tuned candidate lands (Nov 8), all five are run again on it with the same rules. A different pick goes in a commit that says why.

### 1. Main metric

Options: **PR-AUC**, **ROC-AUC**.

Test: on `val`, both metrics for every score (the three baselines, the untuned forest and LightGBM) and a seeded random score, next to chance (PR-AUC: the positive rate; ROC-AUC: 0.5). Then the paired bootstrap of the candidate against lines × files under each metric: the difference over its standard deviation (z).

Rule: the metric with the larger z, because the bar's job is to tell the model from lines × files. If the two are within 10% of each other: PR-AUC. It's the metric PLAN §4's bar already uses, and it reads as "how many flagged commits are real". Both go in `metrics.json` either way.

Result (Nov 6, `val` N = 4,113, 486 bug-inducing, 11.8%; 2,000 resamples):

| Score | PR-AUC | above chance (0.118) | ROC-AUC | above chance (0.5) |
|---|---|---|---|---|
| LightGBM (candidate) | 0.403 | +0.285 | 0.813 | +0.313 |
| forest | 0.407 | +0.289 | 0.825 | +0.325 |
| lines × files | 0.319 | +0.201 | 0.797 | +0.297 |
| churn | 0.316 | +0.198 | 0.791 | +0.291 |
| bug fixes | 0.222 | +0.104 | 0.676 | +0.176 |
| random (seed 1) | 0.113 | −0.005 | 0.470 | −0.030 |

| Candidate − lines × files | Difference | SD | z | Paired 95% interval |
|---|---|---|---|---|
| PR-AUC | +0.084 | 0.017 | **4.9** | [+0.049, +0.117] |
| ROC-AUC | +0.016 | 0.007 | 2.2 | [+0.002, +0.031] |

Picked: **PR-AUC**. It puts more than twice as many standard errors between the model and lines × files. ROC-AUC makes every score look alike: lines × files at 0.80 sits within 0.02 of the model, and even the weak bug-fix count reaches 0.68. Both stay in `metrics.json`.

The random score's ROC-AUC (0.470) is about 2 standard errors below 0.5, a little unlucky. Its PR-AUC (0.113) sits at the positive rate, as it should.

### 2. Effort-aware ranking

Options: recall at 20% of changed lines, reading commits **by risk**, or **by risk per line** (score ÷ lines changed).

Test: the hand-made 10-commit example in `test_evaluate.py` checks the code: by risk 3 of 5 bugs, by risk per line 3.35 of 5. Then both rankings on `val`, for every score.

Rule, set before the run: **by risk per line.** With a budget of lines, that's the order that finds the most bugs per line read, the usual effort-aware setup (Kamei et al. 2013). The run shows how much the order matters. A score that does well only by risk finds its bugs by reading big commits.

Two details, set now: a commit with no changed lines counts as 1 line; commits with the same key are read as one group, and the group the budget ends in is read in part (the share of its lines that fits, and that share of its bugs).

Result (Nov 6, `val` N = 4,113, 486 bug-inducing; 20% of 984,446 changed lines):

| Score | Recall, by risk | Recall, by risk per line | Commits read (per line) |
|---|---|---|---|
| LightGBM (candidate) | 0.208 | **0.615** | 2,914 |
| forest | 0.206 | 0.621 | 3,423 |
| bug fixes | 0.203 | 0.516 | 2,300 |
| churn | 0.002 | 0.200 (every commit ties: random order) | |
| lines × files | 0.002 | 0.006 | |
| *no model: smallest commits first* | | *0.580* | *3,541* |

Picked: **by risk per line**, as set. By risk, the order doesn't matter much for the models (about 0.21) and it ruins lines × files: the 1% biggest commits hold 41% of the lines, so its first 4 commits use up the whole budget and find 1 bug.

What the run also shows, and the report has to say: this metric mostly rewards reading small commits. With no model, reading the smallest commits first finds 58% of the bugs. The model adds 3.5 points to that. And a 20% line budget is 71% of the commits (2,914 of 4,113), so it ignores the time each commit costs to open. `metrics.json` shows the smallest-first number next to every recall at 20%, the way PR-AUC sits next to the positive rate.

### 3. Threshold

Options:
- **best F1** on `val`
- **a fixed precision**: 0.80, the push-check bar (PLAN §4)
- **a fixed alert rate**: the share of `val` commits today's P9 rule flags (lines × files above 2,000, `Data-Pipeline/settings.toml`): 634 of 4,113, 15.4%. Counted from the features alone, with no labels

Test: each rule's threshold on the candidate's `val` scores: alerts, alert rate, median alerts per push (one push is one commit here), precision, recall, F1, each with its N.

Rule: the rule whose threshold meets the push-check bar on `val` (precision ≥ 0.80, median ≤ 1 alert per push) on at least 30 alerts (05-test: no claim below N = 30), and among those the one with the most recall. If none does, the fixed-precision rule is kept, and `metrics.json` says no threshold reached the bar: the push check keeps lines × files.

Result (Nov 6, `val` N = 4,113, 486 bug-inducing):

| Rule | Score | Threshold | Alerts (rate) | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| best F1 | LightGBM | 0.241 | 757 (18.4%) | 0.367 | 0.572 | 0.447 |
| fixed precision 0.80 | LightGBM | 0.838 | **30 (0.7%)** | **0.800** (24 of 30) | 0.049 | 0.093 |
| fixed alert rate 15.4% | LightGBM | 0.280 | 634 (15.4%) | 0.388 | 0.506 | 0.439 |
| best F1 | lines × files | 1,143 | 815 (19.8%) | 0.333 | 0.558 | 0.417 |
| fixed precision 0.80 | lines × files | none reaches it on 30 alerts | 0 | | 0 | 0 |
| fixed alert rate 15.4% | lines × files | 2,008 | 634 (15.4%) | 0.358 | 0.467 | 0.405 |

The median push gets 0 alerts under every rule (one push is one commit, and no rule flags half of them).

Picked: **fixed precision**, the only rule that meets the bar. It meets it only at the edge: exactly 30 alerts, 24 right. The Wilson 95% interval of that precision is 0.63–0.90, so 2024 may well land under 0.80. What it costs: the check flags 0.7% of commits and catches 5% of the bug-inducing ones.

The trade-off, in plain numbers:
- **At today's P9 alert volume (15.4% of commits),** the model's alerts are right 39% of the time against 36% for lines × files, and it catches 51% of the bugs against 47%.
- **Only the model reaches 0.80 precision at all.** lines × files never reaches 0.80 on 30 or more alerts.
- **An F1 threshold** would flag 18% of commits at 37% precision: the alert fatigue the packet warned about.

Rachna's decision 15 sets the P9 hook's own threshold. This row only sets the threshold `metrics.json` reports.

### 4. Bootstrap resamples

Options: **500**, **1,000**, **2,000**.

Test: the paired interval of the candidate against lines × files on `val`, at each size, with bootstrap seeds 1–20, timed. The packet's check is 3 seeds: both ends of the interval move less than 0.005. On 3 seeds alone, a size can pass or fail on luck, so the check is run on all 1,140 triples of the 20 seeds.

Rule: the smallest size whose 3-seed check passes in at least 95% of the triples. Then the pass bar's own check (seeds 1–3) at that size.

Result (Nov 6, `val` N = 4,113, 486 bug-inducing; LightGBM against lines × files; 20 seeds per size):

| Resamples | SD of the low end | SD of the high end | 3-seed triples that pass | One bootstrap |
|---|---|---|---|---|
| 500 | 0.0020 | 0.0023 | 57% of 1,140 | 0.07 s |
| 1,000 | 0.0015 | 0.0017 | 90% | 0.14 s |
| 2,000 | 0.0012 | 0.0014 | **100%** | 0.28 s |

Picked: **2,000** (`model.toml [eval] bootstrap_reps`). At 1,000, seeds 1–3 alone would have passed (the ends moved 0.0009 and 0.0017), but 1 seed triple in 10 fails: the packet's check would have picked 1,000 on luck. The pass bar's check at 2,000, seeds 1–3: the ends move 0.0011 and 0.0027, under 0.005. What it costs: 0.28 s a bootstrap, so the 60-second bar isn't at risk.

### 5. Paired or unpaired

Test: on `val`, the candidate against lines × files at decision 4's size. The paired interval (both scores on the same resamples), and the unpaired one (each score on its own resamples). Compare the widths.

Rule: **paired**, the right test for two scores on the same commits, if it's the narrower one. If it isn't, something is wrong in the code (both resample the same rows), and it gets fixed before the bar uses it.

Result (Nov 6, `val` N = 4,113; 2,000 resamples, seed 1): LightGBM − lines × files = +0.084.

| | 95% interval | Width |
|---|---|---|
| Paired | [+0.049, +0.117] | 0.068 |
| Unpaired | [+0.025, +0.140] | 0.114 |

Picked: **paired**, 41% narrower. Both scores rank the same commits, so a resample with many easy commits lifts both, and that shared part cancels in the difference. Either way the candidate beats lines × files on `val`: both intervals sit above 0.

## 8. MLflow store

Options: **file store** (`mlruns/`), **SQLite** (`sqlite:///mlflow.db`).

Test: on each store, in separate processes: register two versions of `defect-risk`, set `champion` on v2 and `previous` on v1, read both back from a new process; roll back (`champion` to v1, drop `previous`), read back again. The check script was written before it ran.

Rule: the store where every step passes with no extra settings. If both pass, SQLite (Contract 3's choice).

Result (Nov 5, `mlflow==3.18.0`):

| Store | Register, aliases | Read in a new process | Rollback, read again |
|---|---|---|---|
| File store, as installed | refused: "the filesystem tracking backend is in maintenance mode", needs `MLFLOW_ALLOW_FILE_STORE=true` | refused | refused |
| File store, with that setting | pass | pass | pass |
| SQLite | pass (0.04 s) | pass | pass |

Picked: **SQLite**. The packet's reason, that the file store can't hold aliases reliably, isn't what we found: with the opt-out set, it held them. The real reason is that MLflow 3.18 refuses the file store unless every laptop and CI sets an opt-out flag, and it gets no more updates. `tracking.py` defaults to `sqlite:///<repo root>/mlflow.db`.
