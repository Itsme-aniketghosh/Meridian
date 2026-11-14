# Validation, tracking, the registry and the auditor

This section covers the guideline rows owned here (PLAN §12): validation on held-out data (§2.3, §8.4), experiment tracking (§4, with the training owner), the charts that compare the models (§4), the registry push to GCP (§2.6, §8.7), the automatic push after the gate (§7.4, with the CI owner), and rollback (§7.6). It ends with the auditor, the part of the product that learns which stall reasons to show first. Every number carries its N and its source. The choices behind them, with the rule each was picked by, are in `packets/rochan-tracking/decisions.md`; the bars they're held to are in `pass-bars.md`.

## 1. Validation on held-out data (§2.3, §8.4)

**The split.** Train on 2015–2022, choose on 2023 (`val`), and test on 2024 once (`model.toml [split]`). 2024 is scored a single time, on frozen code, and nothing is picked after it's seen.

**The choices, each made on `val` before 2024 was opened:**

| # | Choice | Picked | Why |
|---|---|---|---|
| 1 | Main metric | PR-AUC | 11.8% of commits are positive. ROC-AUC makes every score look alike: lines × files reaches 0.80, within 0.02 of the model |
| 2 | Effort-aware metric | recall at 20% of changed lines, reading commits by risk per line | Ranking by raw risk spends the whole budget on a few huge commits |
| 3 | Threshold | a fixed precision of 0.80 with at least 30 alerts | The push-check bar. On the champion, no cutoff reaches it, so the threshold is empty and P9 keeps lines × files |
| 4 | Bootstrap resamples | 2,000 | The smallest count whose interval ends moved under 0.005 across seeds |
| 5 | Paired or unpaired | paired | 41% narrower: both scores rank the same commits |

**The champion on `val`** (`Model-Pipeline/registry/champion.json`, written by `evaluate.py`; N = 4,113 commits, 486 bug-inducing, 11.8%):

| | Value | Interval (95%) |
|---|---|---|
| PR-AUC, forest | 0.4067 | 0.365 – 0.451 |
| ROC-AUC, forest | 0.826 | |
| Recall at 20% of lines | 0.621 | |
| PR-AUC difference against lines × files (paired) | +0.0875 | +0.0606 – +0.1128 |
| Alerts at precision 0.80 (≥ 30 alerts) | none: no cutoff reaches it | |

**2024, once (Nov 14).**
- **How it was run:** step by step from `RUN-2024.md`, by Utkarsh with Aniket watching.
- **Where it ran from:** commit `df12728` (only `model.toml` differs from the freeze tag), at 01:57 UTC.
- **Where it's recorded:** MLflow run `4d107a39…` in `meridian-defect-risk`. Full log in `reports/2024/RUN-LOG.md`, fingerprints in `golden/2024.json`.
- **The first check passed:** the champion first reproduced its recorded val PR-AUC, 0.4067.

N = 3,762 commits, 283 bug-inducing (7.5%). Source `reports/2024/metrics.json`:

| | Value | Interval (95%) |
|---|---|---|
| PR-AUC, forest | **0.268** | 0.224 – 0.320 |
| PR-AUC, lines × files | 0.214 | 0.179 – 0.259 |
| Difference against lines × files (paired) | **+0.054** | +0.028 – +0.081 |
| ROC-AUC, forest (lines × files: 0.770) | 0.808 | |
| Recall at 20% of lines (smallest commits first: 0.608; random: 0.200) | 0.625 | |
| Alerts | 0: no alert threshold (decision 3) | |

**Verdict, by PLAN §4's bar** (the model ships only if its 2024 PR-AUC beats lines × files with the paired interval above 0): **the interval is above 0, so the model ships.**
- The other two baselines fall below lines × files on 2024: churn 0.196, bug-fix count 0.166, both with paired intervals below 0.
- Every PR-AUC is lower than on 2023 because only 7.5% of 2024's commits are labelled bug-inducing, against 11.8% in 2023. Recent labels are incomplete: a bug introduced in 2024 may not have been found and fixed by the time the data was pulled.
- The model's lead over the bar holds on the year it never saw.

**How 2024 stayed unseen:**
- `evaluate.py --split test` refuses unless `model.toml [split] test_allowed` is true and the code is committed (fault 14 checks it).
- The scoring step for the run (`--score-champion`) also refuses until the model is the champion's (md5), the table is its own, and the model reproduces its recorded val PR-AUC.
- The steps are in `RUN-2024.md`.

## 2. Comparing the models, and why the forest (§4)

On `val` (`reports/tuned_val.json`; N = 4,113, seed 1, 1,000 resamples), every tuned model beats lines × files with its paired interval above 0. Two of the baselines can't:

| Model | PR-AUC | Difference against lines × files (paired 95%) |
|---|---|---|
| lines × files (the bar) | 0.3191 | |
| churn | 0.3162 | −0.0029 (−0.0151 – +0.0093) |
| bug-fix count | 0.2221 | −0.0970 (−0.1304 – −0.0630) |
| logistic regression | 0.3823 | +0.0632 (+0.0428 – +0.0848) |
| **forest** | **0.4058** | +0.0867 (+0.0595 – +0.1140) |
| LightGBM | 0.4130 | +0.0938 (+0.0640 – +0.1220) |

**Why the forest and not LightGBM.**
- The two tie: the paired bootstrap of their difference covers 0. By the training owner's pick rule, the simpler model wins a tie.
- Its bias report passes, with its flagged size slices accepted after a mitigation was tried (the bias owner's final pick).
- The champion is that same forest configuration, retrained in CI's image: val PR-AUC 0.4067. Five seeds of it give a mean of 0.4044 with a standard deviation of 0.0021 (`reports/seed_noise.json`, N = 4,113), so 0.4058 and 0.4067 are the same model within seed noise.

**The charts** come from `evaluate.py`:
- on `val`: PR-AUC with its interval for every model and baseline, the confusion matrix at the threshold, and the PR curves;
- on 2024, the same three in `reports/2024/` after the run.

## 3. Experiment tracking (§4)

- **One way to start a run.** Every training, evaluation, bias, decision and agent run starts through `model/tracking.py`. It stamps each run with the same tags: `owner`, `block_run_id`, `git_sha`, `git_dirty`, `config_sha256`, `meridian_config_sha256`, `features_md5` on model runs, and `decision` on decision tests.
- **Every number traces back.** Any number in this report traces to the code (`git_sha`, with `git_dirty` false), the data block and the feature table it came from.
- **Three experiments:** `meridian-defect-risk` (the model), `meridian-agents` (LLM calls) and `meridian-auditor` (the auditor's decisions).
- **The store:** local SQLite (decision 8). MLflow 3.18 refuses the file store unless every machine sets an opt-out, and the model registry needs a database.
- **LLM calls** are logged in batches, one run per agent batch, about 120 times faster than a run per call (decision T1).
- **The bar:** every run carries every tag. Result: 26 of 26 runs (`pass-bars.md`, Tracking).

## 4. The registry on GCP (§2.6, §8.7) and the automatic push (§7.4)

**Promotion is decided by rules written before any model was registered:**
- **Decision 6.** A candidate replaces the champion only on a val PR-AUC gain above 2 standard deviations of seed noise. Fed five same-configuration seeds in a row, the champion moved 0 times (`test_registry.py`, N = 5).
- **Decision 7.** When the feature table changes, the champion is re-scored on the new table before it's compared. Stored numbers would describe other rows.

**What the registry holds:**
- The MLflow registered model `defect-risk`, with `champion` and `previous` aliases.
- `registry/champion.json` and the champion's `model.joblib`, in git. CI and the push check read them.
- GCP Artifact Registry repository `meridian-models` (us-east1):
  - the package `defect-risk` holds each promoted model's `model.joblib` and `champion.json` under its git SHA;
  - the package `champion` is a pointer whose newest version is the champion in force.

  On Nov 13 it held 8 model versions and 9 pointers, and the newest pointer named the committed champion (version `85b4684…`, model md5 `2a0c9cc6…`). Source: `gcloud artifacts versions list` and a download of the newest pointer.

**The automatic push (§7.4).** On every push to main, `model.yml` runs the pipeline (train, evaluate, bias, gate) and then the registry with `--push`. The registry promotes only past the gate and the rules above, and uploads only files Artifact Registry doesn't already hold.
- **Checked:** every file is checked by md5, and a different file under an existing version stops the push before anything is sent.
- **The login:** CI logs in to GCP without a key, through Workload Identity, trusted for this repository's main branch only. The university's organization blocks service-account keys.

**Checked on the real repository:**
- **Nov 9:** two throwaway models pushed. Each downloads back with the md5 its `champion.json` records, 2 of 2.
- **Nov 10:** eight steps, all passing:
  - register without pushing;
  - push;
  - push again (nothing sent);
  - a better model;
  - a rollback;
  - both models downloaded back byte for byte;
  - CI's case of a new store (nothing sent);
  - a conflicting file (stopped).

## 5. Rollback (§7.6)

- **The champion moves only on a real gain** (decision 6), so a worse or noise-level model is registered but never promoted or pushed (faults 7 and 8).
- **`registry.py --rollback`** moves `champion` back to `previous`. It puts back `champion.json` and the model file next to it, and adds a pointer version in Artifact Registry.
- **Checked:**
  - fault 10: locally, and against a stand-in for Artifact Registry;
  - the Nov 10 drill, on the real repository: the rollback added only a pointer, and both models downloaded back byte for byte.

## 6. The auditor

**What it does.** The auditor learns which stall reasons to show a team first.
- **Policies.** Versioned policies rank the reasons for each stalled team. The team sees the champion's ranking. A challenger's ranking is shown with chance ε: 0.2, then 0.1 after 30 matches (decision 9).
- **Matches.** When a stall ends, verdict rules say why. Every pair of policies plays a match on it: the one that ranked the true reason higher wins.
- **Ratings.** Elo is recomputed from the match log every run: K is 32, then 16 after 30 matches (decision 10). Replays of past stalls count at half K (decision 11).

**What's checked** (`pass-bars.md`, Auditor):
- Elo orders three policies of known skill right in 20 of 20 simulated seeds.
- Recomputing a 700-match log gives the same ratings to the last bit, in any order.
- Seeded ε lands at 19.36% and 9.88% over 10,000 draws.
- A replay never reads a row known after the stall: call sites, tickets, commits and the derived tables are cut by time.

**The promotion guard (decision 12).** The guard in the original design (30 live matches, +50 Elo, beats v0) promoted a policy no better than v0 by luck in 87.0% of simulated 201-match runs and 95.8% of 600-match runs. N = 2,000 seeds; source `auditor_sim.py --decide 12`, MLflow `meridian-auditor`, `decision=rochan-12`.

A sweep of 27 guards, picked by a rule committed before it ran, chose the original guard plus a luck test on the head-to-head record against the champion, with draws left out. Luck now promotes a no-better policy:
- in 3.7% of 201-match runs, 4.0% of 600-match runs, and still 4.0% over 3,000 matches;
- exactly 4.22% for one challenger, however often it's checked.

A policy 200 Elo better is promoted in 100% of runs, at a median of 69 matches instead of 45.

The cost: a policy only 80 Elo better is promoted in 49.5% of 3,000-match runs, and one 60 better in 21.9%. `[auditor] promote_gain = 100` trades speed for those.

## Limits, stated plainly

- **No alert threshold.** The model can't alert at precision 0.80 on `val`, so the push check (P9) stays on lines × files. The model's value shows as a ranking: PR-AUC and recall at 20% of lines.
- **The ceiling is the data.** The labels come from SZZ, which picks the right bug-inducing commit 44% of the time on the 130 checked labels, and recent labels are incomplete. The model and data sections give these numbers.
- **One time cut is loose.** Ticket maps aren't cut by the run that made them, so a replay can read a later run's map early (`decisions.md`, second look). There's no effect on the real block, which holds one run with no maps to call sites.
- **Nothing promoted yet.** The auditor has had no live matches (no users yet), so in this phase nothing could be promoted. The guard is checked in simulation.
- **2024 is final.** Its numbers are reported as they came out of the one run.
