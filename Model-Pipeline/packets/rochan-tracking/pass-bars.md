# Rochan's pass-bar rows, for Model-Pipeline/PASS-BARS.md (Nov 5)

These are my rows for Chaitali's `PASS-BARS.md`. Each row gives a number and how it's measured.

docs/05-test.md says bars are fixed before the run and changing one needs a commit saying why. So the rows are split by timing:
- **Recorded after the run:** tracking (section 1). It was built and tested today, before the bars were due. Its rows record the checks it was held to, with results, and say so.
- **Before the run:** evaluation, the registry and the auditor (sections 2–4). None of them has run.

PLAN §4's model bar is the team's row: the model ships only if its 2024 PR-AUC beats lines × files with the paired bootstrap interval of the difference above 0. Section 2 is how those numbers are made.

Every bar in sections 2–4 was checked first: would a correct implementation pass it, or could it fail on luck? Two came out fragile as first drafted. They're fixed below, and each says why.

## 1. Tracking (`tracking.py`), recorded after the run

| What | Bar | Measured by | Result (Nov 5) |
|---|---|---|---|
| Tags on every run | All of Contract 3's tags: `owner`, `block_run_id`, `git_sha`, `git_dirty`, `config_sha256`, `meridian_config_sha256`, and `features_md5` on model runs. 100% | `test_tracking.py`; on Nov 12, one query over Aniket's final store: runs missing a tag = 0, N = runs | ✅ 26 of 26 tests. The Nov 12 query is still to run |
| Bad calls make no run | An unknown experiment, a missing table or config, a table that doesn't match its schema, or a tag that tracking owns: refused before any run is made. 100% | `test_tracking.py`, `Refusals` | ✅ 8 of 8 |
| Cost per run | Tracking adds at most 10 ms to a run | 100 runs on a 2.16 MB table | ✅ 4.4 ms (10.2 ms against 5.8 ms for plain MLflow) |
| Cost per LLM call | At most 1 ms a call | 10,000 calls | ✅ 0.12 ms |
| Parallel writers | 8 processes into one store: 0 runs lost | [decisions.md](decisions.md) T2 | ✅ 200 of 200 |

## 2. Evaluation (`evaluate.py`), before the run

PR-AUC means average precision, as in Rachna's `train.py`, so there is one definition.

| What | Bar | Measured by |
|---|---|---|
| Right numbers | PR-AUC, F1 at the threshold and recall at 20% of changed lines equal a hand calculation on a 10-commit example. A perfect score gives PR-AUC 1.0. A random score on 10,000 rows gives PR-AUC within 0.02 of the positive rate (the widest gap in 2,000 simulated tables was 0.012) | `test_evaluate.py` |
| Every number has its N | Every metric in `metrics.json`, for the model and each baseline, has N and the positive rate next to it. 100% | `test_evaluate.py` reads the file |
| Repeatable | Same scores, same seed: every number in `metrics.json` is the same, intervals included | `test_evaluate.py`, run twice |
| Stable intervals | At the chosen number of resamples, both ends of the paired interval move less than 0.005 across 3 bootstrap seeds (decision 4) | `val`: the candidate against lines × files |
| Fast | `evaluate.py` on `val`, every model and baseline, bootstrap included: at most 60 s on a laptop | timed |
| 2024, once | `--split test` refuses unless `model.toml [split] test_allowed = true`. The pick, the threshold and these bars are all committed before the first `split=test` run, so nothing is chosen after 2024 is seen | `test_evaluate.py`; the run's `git_sha` holds those commits |
| Threshold | Fixed on `val` by decision 3 before 2024 is scored; 2024 uses the same number. `metrics.json` reports its alert rate and precision on `val`, with N. The push-check bar (median ≤ 1 alert per push, precision ≥ 0.80) stays PLAN §4's push-check row; this row only makes the numbers to check it | `metrics.json` |

## 3. Registry (`registry.py`), before the run

| What | Bar | Measured by |
|---|---|---|
| Noise never promotes | Rachna's 5 same-config seeds, fed in a row: `champion` moves 0 times. Decision 6 picks the rule that does this | `test_registry.py`, N = 5 |
| Real gains promote | A model better by more than the rule's margin, with bias passing, takes `champion`, and `previous` points at the old one. 100% | `test_registry.py` |
| What blocks | A worse model is registered, not promoted, with the reason as a tag. A failed `bias_report.json` blocks promotion. 100% | `test_registry.py` |
| First model | The first model ever is promoted if the gate and bias pass | `test_registry.py` |
| The pushed file is the model | The model in Artifact Registry has the md5 that `champion.json` records. 100% | the drill: download it, md5 it |
| Rollback | One command (`--rollback`) puts `champion` back on `previous`, in MLflow and in the `champion.json` in Artifact Registry | `test_registry.py`; the drill on the real repo, N = 1 |
| No credentials | `--push` without GCP credentials skips the upload and says so. Local work never needs GCP | `test_registry.py` |

## 4. Auditor (`meridian/auditor.py`), before the run

05-test's row, 100%, with the setup each check runs on written down:

| What | Bar | Measured by |
|---|---|---|
| Ranks right | 3 stall policies of known skill, 200 Elo apart (the better one wins 76% of head-to-heads), all ranking every stall in shadow (04 §9), K 32 then 16. After 200 matches, Elo orders all three right in 20 of 20 seeds | `test_auditor.py` |
| Recompute | Elo recomputed from the `match` log twice gives the same ratings, exactly | `test_auditor.py` |
| Seeded ε | The share of challenger picks is within ±2% of ε over **10,000** seeded draws, at ε = 0.2 and at 0.1 | `test_auditor.py` |
| No look-ahead | A replay never reads a row whose `known_at` is after the stall: 0 rows. The fixture has rows on both sides of the stall time | `test_auditor.py` |
| Promotion guard | Blocks at 29 live matches and at +49 Elo. Replay matches never count toward the 30. A policy written by an LLM is never promoted without a person | `test_auditor.py` |
| Runs | Runs on the fixture and on replay. With no live matches, the output says so | `test_auditor.py` |

Why two of these differ from a first reading of 05-test (simulated on Nov 5, before any auditor code exists):
- **Seeded ε over 10,000 draws, not 1,000.** At ε = 0.2, a correct sampler lands outside ±2% over 1,000 draws 1 time in 10 (exact binomial), so the bar would fail on luck. Over 10,000 draws that's about 1 in 2 million. The draws take milliseconds.
- **Skill 200 Elo apart.** With 200 Elo between policies, a correct Elo got the order right in 2,000 of 2,000 simulated seeds. At 100 apart (64% head-to-head) it gets it right in 94% of seeds, so "20 of 20" would fail 72% of the time with nothing wrong. Smaller gaps are decision 10's question (rank error after 30, 60, 100 matches), not a bar.

Results go in a Results section after each run. The bars above stay as written.

## Results

### Evaluation, Nov 6 (`val` only)

On Rachna's untuned LightGBM (her `candidate.json` isn't written yet), `val` N = 4,113, 486 bug-inducing. These are checked again on the tuned candidate (Nov 8) and on 2024 (Nov 12).

| Bar | Result |
|---|---|
| Right numbers | ✅ `test_evaluate.py`, against the hand calculation on the 10-commit example, to 12 decimals: PR-AUC 25/36, ROC-AUC 15/25, F1 at 0.6 = 2/3, recall at 20% of lines 3/5 by risk and 3.35/5 by risk per line. A perfect score: 1.0. A random score on 10,000 rows: within 0.02 of the positive rate |
| Every number has its N | ✅ 25 of 25 metric blocks in the `val` `metrics.json` carry N and the positive rate |
| Repeatable | ✅ Three runs on `val`: byte-identical `metrics.json` |
| Stable intervals | ✅ At 2,000 resamples (decision 4), seeds 1–3: the ends move 0.0011 and 0.0027 (bar: under 0.005) |
| Fast | ✅ 1.4 s for every model and baseline, 2,000 resamples and three charts (bar: 60 s). 5 s with Python's start and the MLflow run |
| 2024, once | ✅ for the refusal: `--split test` is refused without `test_allowed = true`, and on uncommitted code (tested). That the choices were committed first is checked on Nov 12, from the run's `git_sha` |
| Threshold | ✅ Fixed on `val` by decision 3 (precision 0.80 on at least 30 alerts). The test run reuses `val`'s number (tested). `metrics.json` holds its alerts, alert rate and precision, with N |

### Registry, Nov 9

| Bar | Result |
|---|---|
| Noise never promotes | ✅ Rachna's 5 seeds in a row, in seed order: `champion` moves 0 times (`test_registry.py`, N = 5). In 24 of the 120 orders it would move once (decisions.md 6) |
| Real gains promote | ✅ +0.05 over the champion, with a margin of 0.02, takes `champion`, and `previous` points at the old one |
| What blocks | ✅ A gain within the margin, a worse model and a failed bias report are each registered, not promoted, with the reason as a tag |
| First model | ✅ Promoted when its bias report passes. A failed one blocks even the first |
| The pushed file is the model | Not yet: the drill needs the GCP repo |
| Rollback | ✅ in the store: `champion` back on `previous`, `previous` a step back, `champion.json` the restored record. The drill on GCP: not yet |
| No credentials | ✅ With no project, no gcloud or no signed-in account, `--push` skips and says so. Uncommitted code is refused |

### Auditor, Nov 7 (policy v0 and Elo)

Only the rows today's code can meet. Seeded ε and the promotion guard come with the stall job (Nov 8), and no look-ahead with replay (Nov 10).

| Bar | Result |
|---|---|
| Ranks right | ✅ 20 of 20 seeds, `test_auditor.py`. Each stall draws one performance per policy, so a stall's three results agree with each other, and the better policy won 76.2% of 20,000 simulated head-to-heads (76.0% expected). Smallest gap between neighbours in the 20 seeds: 83 Elo, median 168. Over 2,000 seeds, 1,998 came out right. The Nov 5 estimate (2,000 of 2,000) simulated independent matches. So a different set of 20 seeds would fail about 2% of the time; seeds 1–20 are fixed, so the test always gives the same answer |
| Recompute | ✅ A 700-match log (600 live, 100 replay) gives the same ratings twice, and in shuffled order: equal to the last bit |
| Runs | Half done. Policy v0 and Elo run on the fixture and on the v1 block (70dd1dabfed544b8: 68 stalls in 25 repo and symbol pairs, 76 s), and say there are no live matches. Replay comes Nov 10 |

### Auditor, Nov 8 items (stall job), run Nov 7

| Bar | Result |
|---|---|
| Seeded ε | ✅ Over 10,000 seeded draws the challenger share is 19.36% at ε = 0.2 and 9.88% at ε = 0.1, within ±2 points (`test_auditor.py`, `Epsilon`) |
| Promotion guard | ✅ for what it checks (`test_auditor.py`, `Promotion`). It blocks at 29 live matches and at +49.9 Elo. 100 replay wins plus 29 live ones are still blocked, and the 30th live win passes. An LLM's policy is blocked until a person approves it. But the guard as 04 writes it promotes a policy no better than v0 by luck in 87% of simulated runs: decisions.md 10, raised for the team. This row stays as written until the team changes the guard |
| Runs | Fixture: the stall job runs. Three stalls ended between R1 and R2, and no rule fires for them, so each waits for a person. With a verdict added, the policies play live matches. v1 block (70dd1dabfed544b8): 68 stalls served, 60 with v0's ranking and 8 with v1's, in 80 s. That block holds one run, so no stall can have ended. Replay comes Nov 10 |
| No look-ahead | Comes with replay, Nov 10 |
