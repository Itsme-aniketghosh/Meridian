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

## Auditor: decisions 9–11 (Nov 8)

Written before any of them ran. Each runs with `python -m meridian.auditor_sim --decide <n>` and logs a run tagged `decision=rochan-<n>` in `meridian-auditor`.

The simulated world is the "Ranks right" bar's (pass-bars §4): three stall policies of known skill, v1 at 1700, v0 at 1500 (the rule table, champion at the start) and v2 at 1300, so the better of any two wins 76% of their matches. Each decided stall draws one performance per policy, and every policy ranks every stall in shadow (04 §9), so one stall makes 3 matches. Promotion uses 04 §9's guard, checked after every stall: at least 30 live matches, at least 50 Elo above the champion, and a winning live record against v0.

The packet says 20 seeds. Each test also runs on 2,000, because 20 seeds can't tell 5% from 10%. The picks are made on the 2,000.

### 9. ε schedule

Options: **0.2, then 0.1 after 30 matches** (04 §9), **0.1 from the start**.

Test: 200 matches a seed (67 stalls). For every stall, ε-greedy picks the ranking the team sees: the champion with 1 − ε, else a challenger at random. "30 matches" counts the job's live matches. Measured: matches until v1, the best, is champion; the share of stalls where a worse policy is champion; the share of stalls where the team sees a worse ranking than v1's.

Rule: in the stall job every policy ranks every stall in shadow, so ε can't change what Elo learns or when v1 is promoted. The first two numbers must come out the same for both options; if they don't, the simulation is wrong and gets fixed first. So the pick is on the third: the option under which the team sees a worse ranking less often. Within 0.5 percentage points of each other: 04's schedule stays.

Result (run Nov 7 with `python -m meridian.auditor_sim --decide 9 10 11`; 67 stalls, 201 matches a seed; 4 s for 2,000 seeds):

| Schedule | Matches until v1 is champion (median) | Seeds where it never is | Stalls with a worse champion | Stalls where the team sees a worse ranking than v1's |
|---|---|---|---|---|
| 0.2, then 0.1 after 30 | 45 | 0 of 2,000 | 23.1% | **28.7%** |
| 0.1 | 45 | 0 of 2,000 | 23.1% | 29.4% |

Seeds 1–20 alone: the same 45 and 0, then 26.8% for both, and 31.2% against 32.2%.

Picked: **0.2, then 0.1 after 30 matches** (04's). The first two columns agree seed by seed, as the rule said they must: every policy is judged in shadow, so ε can't change when v1 is promoted. That's always at match 45, when each policy has the guard's 30 live matches. With 04's schedule the team sees a worse ranking 0.7 points less often. Before v1 is promoted, v0 is champion, and an ε pick shows v1 half the time, so exploring early shows the team the better ranking more often.

### 10. Elo K

Options: **32, then 16 after 30 matches** (04 §9, each policy's own count, live and replay alike), **24 flat**.

Test: the same world without promotion: the share of seeds where Elo's order of the three is wrong after 30, 60 and 100 matches. Then a second world where all three policies have the same skill, so any promotion is luck: the share of seeds where any policy passes the guard within 200 matches.

Rule: an option that promotes in more than 5% of the same-skill seeds is out. Of the rest, the one with the lower wrong-order share, averaged over 30, 60 and 100 matches. Within 1 percentage point of each other: 04's 32 then 16 stays.

Result (run Nov 7; 2 s for 2,000 seeds):

| K | Wrong order after 30 matches | after 60 | after 100 | Mean | Same skill: seeds with a promotion |
|---|---|---|---|---|---|
| 32, then 16 | 9.3% | 2.2% | 0.3% | 4.0% | 87.0% |
| 24 flat | 8.6% | 3.0% | 1.4% | 4.3% | 89.7% |

Seeds 1–20 alone: a mean of 3.3% for both, and 85% and 80% promoted.

Picked: **32, then 16** (04's), but not the way the rule meant. Both options fail the luck check: a policy no better than v0 gets promoted in 87% and 90% of the seeds. The rule didn't say what happens when every option is out. No option won, so 04's K stays. On wrong order alone, 04's K is 0.3 points better, within the 1-point line, so the pick doesn't hang on that gap.

That both fail says the problem is the promotion guard, not K.

#### The promotion guard lets luck through (found by decision 10, not decided here)

04 §9's guard (30 live matches, +50 Elo, a winning live record against v0) is checked after every run. Under it, a policy with the same skill as v0 gets promoted in 87% of 201-match runs and 96% of 600-match runs (2,000 seeds). Two things add up:
- **Ratings swing more than 50 by chance.** A match moves a rating by up to 32, so after 30 matches two equal policies are often 50 apart.
- **One lucky check is enough.** The guard is checked after every run, so a challenger needs to look 50 ahead only once.

A bigger margin or more matches helps less than you'd expect:

| Guard: live matches, Elo margin | Same skill, promoted by luck: 201 / 600 matches | 200 Elo better: promoted at match (median) |
|---|---|---|
| 30, +50 (04) | 87% / 96% | 45 |
| 30, +100 | 64% / 89% | 45 |
| 60, +100 | 51% / 86% | 90 |
| 90, +150 | 8% / 39% | 135 |

The better policy is always found, and the worst one is never promoted. The problem is promotion by luck. Fixing it needs a guard that tests the head-to-head record against chance, allowing for being checked every run (a sequential test). That changes 04 §9 and the promotion row of PASS-BARS.md §3, so it's for the team, not this decision. Nothing can be promoted in phase 2 anyway: there are no live matches without users.

### 11. Replay weight

Options: **half K** (04 §9), **full K**, **no replay**.

Test: the same world. Before any live match, 100 replay matches from past stalls. A replay verdict is weaker than a live one, so 30% of them are wrong, and a wrong verdict makes each of its matches a coin flip. Then live matches, whose verdicts are right. Measured: the share of seeds where Elo's order is wrong after 30 and 60 live matches. Also run with 0% and 50% wrong, which don't decide.

Rule: the option with the lower wrong-order share, averaged over 30 and 60 live matches, at 30% wrong. Within 1 percentage point of half K: half K stays (04).

Result (run Nov 7; 3 s for 2,000 seeds):

| Wrong replay verdicts | Replay | Wrong order after 30 live matches | after 60 | Mean |
|---|---|---|---|---|
| **30%, decides** | half K | 1.5% | 0.3% | 0.9% |
| | full K | 1.6% | 0.2% | 0.9% |
| | no replay | 8.5% | 1.5% | 5.0% |
| 0% | half K | 0.3% | 0.2% | 0.3% |
| | full K | 0.1% | 0.2% | 0.2% |
| | no replay | 10.4% | 1.9% | 6.2% |
| 50% | half K | 1.7% | 0.7% | 1.2% |
| | full K | 1.8% | 0.6% | 1.2% |
| | no replay | 7.3% | 1.8% | 4.6% |

Picked: **half K** (04's). Replay matters: without it, Elo's order is wrong in 5.0% of seeds over the first 60 live matches; with replay at either weight, 0.9%. Half and full K are within 1 point at every noise level, so 04's half K stays. It also keeps wrong replay verdicts from steering as much: they move ratings half as far.

The three runs are in MLflow, `meridian-auditor`, tagged `decision=rochan-9`, `-10` and `-11`.

## Auditor: replay and explore (Nov 9)

No options to pick here, so no numbered decision: these are the rules the code follows, with the tests that hold it to them (`test_auditor.py`, `KnownAt`, `Replay`, `Explore`).

**What's known at a moment.** 04 §9 says replay reads "rows with `known_at` ≤ stall time only". Only `call_site` has a `known_at` column, so here is the rule for every table the stall reasons and verdict rules read:
- a call site: its newest version known by then (KNOWLEDGE-BASE.md's `site_now`, as before);
- tickets: created by then, with the status and resolution they had then, rebuilt from `ticket_event`. A ticket resolved later with no status change on record has no known status then, so it's not done;
- commits: authored by then, with their files;
- the tables a run derives from the whole repo (`blocked_by`, `file_owner`, `person_alias`, `team_map`, `module_import`, `resolution`, `deprecation`, `call_edge`): known from that run's snapshot on. A block keeps only its own run's (join carries `call_site` over, nothing else), so as of an earlier run they're unknown, and so is each reason that reads one: 1, 4, 7 and 8. Unknown isn't false: an empty `module_import` would make 7 Test gap fire for every team.

Kept as pulled: a ticket's summary, description, assignee and components. No stall reason reads them.

**Replay.** Every pair of consecutive runs in the block, oldest first. The stalls open at the first run and not at the second get verdicts as of the second, and every policy in the code plays every other on each, whenever it was written: that's how a new policy is tried on the past. A pair plays a stall once, so replay skips a pair that played it live or in an earlier replay. A verdict on a reason that was unknown when the stall was served makes no match: no policy could rank it, and a draw would only be noise.

**Explore v0.** KNOWLEDGE-BASE.md's `radar_deprecated_with_callers`, one repo at a time, the most called first, at most `explore_budget` (200, 04 §9) per repo and run. Unfiled means no present call site resolves to it: a deprecated symbol Meridian already tracks is filed. With a challenger, the two lists are team-draft interleaved (Radlinski, Kurup and Joachims, 2008), and the side whose picks get more accepts within `reply_days` (14) wins. No reply is no match.

### Results

- **No look-ahead.** On the fixture, one row of each kind the queries read was planted after R1, each one that would change R1's facts if it were read. R1's facts don't change; on Sep 20, once those rows are known, they do. Every table the queries read holds 0 rows from after the moment, while the block under it holds some. Each of four views broken on purpose (commits not cut, status as pulled, derived tables from every run, tickets not cut) fails at least one test.
- **What the old views let through, as of R1 on the fixture:** Bob's four late-September commits (8 Competing load counted 6 recent commits, not 2), PAY-10's close on Sep 15 (3 Ticket mismatch fired for payments), and R2's owners, imports and blocks.
- **Replay on the fixture.** 3 stalls ended between R1 and R2, and no rule fires for them as built. With checkout resumed by someone new (4 Orphaned owner), v0 and v1, written in November, play 1 replay match: a draw, since both rank 4 first. In the plain fixture, reasons 1, 4, 7 and 8 are unknown as of R1, so that verdict makes no match; with R1's own derived tables, it does.
- **Real block** 70dd1dabfed544b8. It holds one run, so there's nothing to replay, and the output says so. 68 stalls; 60 are served v0's ranking and 8 v1's, the same as Nov 8. 84 s, against 76–80 s on Nov 7: the views cost about 5–10%. A first version joined `known_run` inside the views, and SQLite stopped indexing `person_alias`: `stall_competing_load` took 4.9 s instead of 3.2 s for each of the 25 repo and symbol pairs, 121 s in all. Written as `IN`, it's back to 3.2 s.
- **Explore on the real block.** 52 deprecation-marked functions, none with a caller in `call_edge`, which holds only the tracked symbols' call graph (gap G3). So v0 nominates nothing until G3 is closed. On the fixture, the radar's only row is `verify_token` itself, which is tracked, so it's filed.
- **No live matches anywhere.** Every block's snapshots are older than the policies (Nov 7), and nobody has replied to anything yet.
