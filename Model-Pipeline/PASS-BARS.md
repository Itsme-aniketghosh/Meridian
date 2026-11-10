# Pass bars · phase 2 · written before the runs they judge

docs/05-test.md: "Pass bars fixed before the run. Changing one needs a commit saying why." This file holds every bar for phase 2: the team's (PLAN §4) and each owner's. Chaitali owns the file; each owner wrote their own rows, everyone signs off at the end, Aniket reviews.

Some steps ran before the bars were due (Utkarsh's B, K, L; Rochan's tracking). Their rows say **recorded after the run**: they record the checks the work was held to, with results. Every other row was written before its run. Results go under **Results** in each owner's packet (`packets/<you>/pass-bars.md` or `decisions.md`); the bars here stay as written.

## 1. Team bars (PLAN §4)

| What | Bar | Owner |
|---|---|---|
| Model | Ships only if its 2024 PR-AUC beats lines × files, with the paired bootstrap interval of the difference above 0. Otherwise the rule stays | Rachna, Rochan |
| Bias | No judged slice flagged without a mitigation tried and written down | Chaitali |
| Model CI | Blocks registration below the champion by more than seed noise | Chaitali |
| Gate | 200 injected errors: 100% caught. 200 clean texts: at most 5% rejected | Vishwa |
| LLM off | Every view renders | Vishwa, Utkarsh |
| Determinism | Same snapshot twice, warm cache: 0 diffs, 0 LLM calls | Vishwa |
| Jev | Same problem twice: same pick. Jev down: rules pick. Beats the rules' pick on cost at an equal pass rate | Rachna |
| Diffs | Django, drafted from the parent: at least 90% match the real after-line | Utkarsh |
| LLM bias | Gate fallback rate and diff match rate per repo and per team: no judged slice (N ≥ 30) more than 0.10 worse than overall without a fix tried | Chaitali |
| Auditor | Elo simulation with known skill ranks right. Recompute twice, same result. Seeded ε within ±2% (Rochan §4.4: over 10,000 draws). No `known_at` leak. Promotion guard holds, 100% | Rochan |
| Views | Every number on a screen traces to a row, 100% | Vishwa |
| Maker | Each untracked site in exactly one draft | Vishwa |
| Push check | Replay: median alerts per push ≤ 1, precision ≥ 0.80 | Rachna |

## 2. Utkarsh: data, tracking tables, second run, diffs

From [packets/utkarsh-data/pass-bars.md](packets/utkarsh-data/pass-bars.md), where the results are.

### 2.1 Feature table (step B), recorded after the run

| What | Bar | Measured by |
|---|---|---|
| Determinism | Same block and config: the same parquet md5, 100% | `test_data` `test_same_input_same_md5`; two rebuilds |
| No person data | 0 values containing `@`; no name, email, person or author column | `data.check()` refuses the table; `test_no_person_data` |
| No future in features | `prior_bug_fixes` never counts a later fix; the label window is inclusive at `window_days` and exclusive after it | `test_prior_bug_fixes_never_sees_the_future`, `test_window_edges` |
| Splits | Every split holds only its years; `none` holds only rows after `follow_up_end` | `test_splits_hold_their_years` |
| No silent NaN | 0 missing values in any number column | `data.check()` |
| Judged slices | Every module slice used in val and test has N ≥ 30, or is pooled | rows per `module` in val and test |

### 2.2 Labels, before the hand checks

| What | Bar | Measured by |
|---|---|---|
| Label precision | At least **16 of 20** random positives (80%) are real. A positive is real when the fix's diff changes lines the inducing commit wrote, for the reason in the fix's ticket | `decisions.md`, 20 positives drawn with seed 1, each read by hand with `git show <fix>` |
| Start year (decision 2) | 2015 stays only if **at most 2 of 10** sampled 2015 positives are wrong. Otherwise the start year moves to 2016 and the table is rebuilt | `decisions.md`, 10 from 2015 and 10 from 2022, same check |
| Label window (decision 1) | The shortest window catching ≥ 80% of blames that leaves a full 2024. No option meets it (365 days: 54%; 730: 72%, and 730 breaks 2024), so 365 is picked by the fallback | the blame-delay CDF over 22,007 SZZ pairs |

### 2.3 Tracking tables (step K), recorded after the run

| What | Bar | Measured by |
|---|---|---|
| Enums | A `suggestion` row whose `source` or `route` is outside the allowed values is refused, 100% | `test_tracking` |
| References | An `outcome` for an unknown `suggestion_id`, or a `match` naming a missing policy, is refused, 100% | `test_tracking` |
| No future rows | No tracking row known after the block's `snapshot_at` | `test_tracking` |
| Empty is valid | A complete block with the tracking tables empty or left out loads and validates with 0 tracking findings | `test_tracking` |
| KB queries | KNOWLEDGE-BASE.md's `outcome_*` queries return what the doc says, on schema.sql's tables | `test_kb` |

### 2.4 Second real run (step L), recorded after the run

| What | Bar | Measured by |
|---|---|---|
| Django | All 267 site lines are edited by the conversion commit; the only sites left are the `test_legacy_aliases` imports and its 4 new calls | `test_run2` |
| Packages | Removed lines per repo equal the answer key (changed after the run, and says why, in Utkarsh's packet) | `test_run2` |
| `known_at` | Every removed row is known at run 2's snapshot, 100% | `test_run2` |
| Validate | 0 errors | `meridian.validate --prev-run` |
| Owner numbers | `owner_done` and `owner_eta` both non-NULL on run 2 | KB queries on the block |

### 2.5 Diff drafter, before any draft exists

| What | Bar | Measured by |
|---|---|---|
| Django match (05-test) | **≥ 90%** of Django's 267 site lines get a draft equal to the real after-line at `c651331b34`, whitespace ignored. Drafted from the parent; a line with no draft is a miss. N reported | `test_diffs` |
| Packages match | ≥ 90% on the 12 repos whose migration commit finished the job. The other 8 are reported, not judged | `test_diffs`, per repo |
| Decision 10 | If the codemod alone clears 90% on Django, the LLM path is cut first (PLAN §9), and that is reported as a result | same test, codemod only |
| Decision 11 | `url()` gets the replacement each migration actually used, per line. Judged by the packages match bar | same test |
| Only its own line | A draft that changes any other line is refused, 100% | `test_diffs` |
| Resolves | A draft whose new name doesn't resolve to the target symbol is refused, 100% | `test_diffs` |
| No LLM in `except` | A site inside `except` gets no LLM diff, 100% | `test_diffs` |
| LLM off | With the LLM off, the codemod still drafts and the run completes | `test_diffs` |
| LLM bias | Diff match rate per repo and per team feeds §6.4 | per-slice match rates from `test_diffs` |

## 3. Rochan: tracking, evaluation, registry, auditor

From [packets/rochan-tracking/pass-bars.md](packets/rochan-tracking/pass-bars.md), where the results and the reasons two bars changed before any run are.

### 3.1 Tracking (`tracking.py`), recorded after the run

| What | Bar | Measured by |
|---|---|---|
| Tags on every run | All of Contract 3's tags, and `features_md5` on model runs. 100% | `test_tracking.py`; on Nov 12, runs missing a tag = 0 in Aniket's final store |
| Bad calls make no run | An unknown experiment, a missing table or config, a table that doesn't match its schema, or a tag tracking owns: refused before any run. 100% | `test_tracking.py`, `Refusals` |
| Cost per run | At most 10 ms a run | 100 runs on a 2.16 MB table |
| Cost per LLM call | At most 1 ms a call | 10,000 calls |
| Parallel writers | 8 processes into one store: 0 runs lost | decisions.md T2 |

### 3.2 Evaluation (`evaluate.py`), before the run

| What | Bar | Measured by |
|---|---|---|
| Right numbers | PR-AUC, F1 at the threshold and recall at 20% of changed lines equal a hand calculation on a 10-commit example. A perfect score gives 1.0. A random score on 10,000 rows lands within 0.02 of the positive rate | `test_evaluate.py` |
| Every number has its N | Every metric in `metrics.json` has N and the positive rate. 100% | `test_evaluate.py` |
| Repeatable | Same scores, same seed: every number in `metrics.json` the same, intervals included | `test_evaluate.py`, twice |
| Stable intervals | Both ends of the paired interval move less than 0.005 across 3 bootstrap seeds | `val`: candidate against lines × files |
| Fast | At most 60 s on a laptop, bootstrap included | timed |
| 2024, once | `--split test` refuses unless `test_allowed = true`. Pick, threshold and bars committed before the first `split=test` run | `test_evaluate.py`; the run's `git_sha` |
| Threshold | Fixed on `val` before 2024 is scored; 2024 uses the same number. Alert rate and precision on `val` reported with N | `metrics.json` |

### 3.3 Registry (`registry.py`), before the run

| What | Bar | Measured by |
|---|---|---|
| Noise never promotes | Rachna's 5 same-config seeds in a row: `champion` moves 0 times | `test_registry.py`, N = 5 |
| Real gains promote | A model better by more than the margin, with bias passing, takes `champion`; `previous` points at the old one. 100% | `test_registry.py` |
| What blocks | A worse model is registered, not promoted, with the reason as a tag. A failed `bias_report.json` blocks promotion. 100% | `test_registry.py` |
| First model | Promoted if the gate and bias pass | `test_registry.py` |
| The pushed file is the model | Artifact Registry's model has the md5 `champion.json` records. 100% | the drill |
| Rollback | `--rollback` puts `champion` back on `previous`, in MLflow and in Artifact Registry | `test_registry.py`; the drill, N = 1 |
| No credentials | `--push` without GCP credentials skips the upload and says so | `test_registry.py` |

### 3.4 Auditor (`meridian/auditor.py`), before the run

| What | Bar | Measured by |
|---|---|---|
| Ranks right | 3 stall policies 200 Elo apart, K 32 then 16. After 200 matches, the order is right in 20 of 20 seeds | `test_auditor.py` |
| Recompute | Elo recomputed from the `match` log twice: the same ratings, exactly | `test_auditor.py` |
| Seeded ε | Challenger share within ±2% of ε over **10,000** seeded draws, at ε = 0.2 and 0.1 | `test_auditor.py` |
| No look-ahead | A replay never reads a row whose `known_at` is after the stall: 0 rows | `test_auditor.py` |
| Promotion guard | Blocks at 29 live matches and at +49 Elo. Replay matches never count. An LLM-written policy is never promoted without a person | `test_auditor.py` |
| Runs | On the fixture and on replay. With no live matches, the output says so | `test_auditor.py` |

## 4. Rachna: models, Jev, push check

From [packets/rachna-models/pass-bars.md](packets/rachna-models/pass-bars.md), which says why one bar changed before any run.

| What | Bar | Measured by |
|---|---|---|
| Model ships | The team's model bar (§1) | Rochan's one 2024 run, Nov 12 |
| Model determinism | Same data, config and seed: every `val` prediction within 1e-9, laptop and image | `test_models.py`, `test_parallel.py`; image against laptop |
| No 2024 in training | No `test` row reaches any fit, transformer, calibrator or tuning fold. 100% | `test_train.py`, `test_models.py`, `test_tune.py` |
| Model sanity | Shuffled labels: `val` PR-AUC below the 99th percentile random scores reach on the same rows, and ROC-AUC within 0.5 ± 0.03, median of 3 shuffles | `test_models.py` |
| Seed noise | 5 seeds of the chosen config, mean and SD of `val` PR-AUC in `reports/seed_noise.json` | `train.py --seed-noise` |
| One command | `python -m model.run_all` in the image runs every step in order and stops at the first failure, naming it | `test_run_all.py`; the image at integration |
| Jev | Same problem twice: same pick. Jev down or over the spend cap: rules pick, the run completes. Beats rules on cost at an equal pass rate (within 2 points) | `test_jev.py` |
| P9 with the model | Replay: median alerts per push ≤ 1, precision ≥ 0.80. A model that fails to load falls back to lines × files and says so | `test_pushcheck.py` |

## 5. Vishwa: fact gate, writer, cache, views

Confirmed by Vishwa, Nov 7. Taken from the tests in [packets/vishwa-agents/README.md](packets/vishwa-agents/README.md) and the team bars.

| What | Bar | Measured by |
|---|---|---|
| Gate | On every seed 0–9 (each 200 injected + 200 clean): 100% of injected errors caught, at most 5% of clean texts rejected. A miss gives the template, never a stripped text | `test_fact_gate.py` (`TeamRule`, decision 11) |
| No person in facts | 0 emails or person names in any fact, prompt or cache entry | a scan of every cache entry |
| LLM off | With no key, every task returns a template and the run completes; every page renders with the `suggestion` table empty | `test_agents.py`, `test_views.py` |
| Warm cache | 0 LLM calls on a second run | `test_agents.py` |
| Bad JSON | Retries, then falls back to the template | `test_agents.py` |
| Maker | Each untracked site in exactly one draft | `test_agents.py` |
| Views | Every number on every page traces to a row, 100%. `run_id`, `snapshot_at` and coverage on every page and JSON response | `test_views.py` |
| Fixture | `acme/shop` renders 02-product's examples: "31 of 52 done", Checkout "STALLED", Fulfillment "blocked" | `test_views.py` |

## 6. Chaitali: bias, final pick, gate, CI

Written Nov 7, before any bias, gate or CI run. Like Rochan's, every bar was checked first: could a correct implementation fail it on luck?

**Why the bias bars look the way they do.** Phase 1's rule (a slice is judged at N ≥ 30, flagged when its whole Wilson 95% interval sits more than 0.10 below the overall value) never falsely flags a fair slice, but it rarely catches a real gap at this table's sizes. Simulated on Nov 7, 2,000 draws per row: a slice whose recall is truly 0.15 below an overall 0.50 is flagged 5.8% of the time at 30 positives, 16% at 100, 54% at 400, 90% at 1,000. `val` has 486 positives in all. So:
- The planted-gap test uses **exact counts**, not sampled ones, so it checks the code and can't fail on luck.
- The report gives each judged slice's **power**, the chance the rule flags a true 0.15 gap at that slice's N, so "no flag" isn't read as "no bias".
- Whether a stricter rule replaces phase 1's is decision 3, not a bar.

### 6.1 Bias (`bias.py`)

| What | Bar | Measured by |
|---|---|---|
| Flag rule | A planted slice of 500 positives with recall exactly 0.35, against an overall recall of 0.50, is flagged. The same slice at exactly 0.50 is not. Same for the false-positive rate (a slice 0.15 above overall, on 500 negatives) | `test_bias.py` |
| Judged | A slice is judged only when the count its metric is a share of reaches 30: positives for recall, negatives for the false-positive rate, rows with at least one positive for PR-AUC. A slice at 29 is listed, not judged | `test_bias.py` |
| Same interval as phase 1 | Wilson intervals equal `Data-Pipeline/meridian/bias.py`'s on the same counts, to 1e-12 | `test_bias.py` |
| Every number has its N | Every slice in `bias_report.json` has N, its positives, the interval, judged or not, flagged or not, and its power at a 0.15 gap. 100% | `test_bias.py` reads the file |
| Repeatable | Same scores, config and seed: `bias_report.json` byte-identical | `test_bias.py`, twice |
| Reads, never refits | Bias reads `reports/<tag>_val_scores.csv` and joins slices on `sha` (S6). A scores file whose `sha`s don't match the table's `val` rows is refused | `test_bias.py` |
| The team's bias bar | Every flagged judged slice has a mitigation tried and written down in `decisions.md` with its numbers | `bias_report.json` against `decisions.md` |

### 6.2 Mitigation and the final pick

| What | Bar | Measured by |
|---|---|---|
| Mitigation rule (decision 5) | A mitigation is adopted when it closes at least half of the worst flagged gap for at most 0.01 `val` PR-AUC and at most 0.02 overall precision at the alert budget. All are scored on the same `val` rows. None qualifies: the model ships unmitigated, and the report says so with the numbers. **The precision limit was added on Nov 8, after the first results:** a per-group threshold leaves PR-AUC unchanged by construction, so the 0.01 PR-AUC limit can't see its cost. Under the bar as first written, "equal recall by size" qualified (closes 97–104% of the gap at 0 PR-AUC cost) while cutting overall precision by 0.10–0.12 and recall from 0.50 to 0.33–0.37. The report says the limit came after the results | `decisions.md` |
| Only what push time knows | A per-group threshold is used only on a slice known when the commit is pushed (`module`, `size_bucket`, `exp_bucket`; not `component`, which needs a ticket) | `decisions.md` |
| Final pick | The best `val` PR-AUC model whose bias report passes. If the best fails and its mitigation costs more than 0.01 PR-AUC, the pick and its reason are written down | `decisions.md`, handed to Rochan |
| Newcomers (decision 4) | Recall for `exp_bucket = 0`, from the model and from lines × files, both with N, in the report whatever they show | `bias_report.json` |

### 6.3 Gate (`gate.py`) and alerts

| What | Bar | Measured by |
|---|---|---|
| Noise passes | The gate on Rachna's 5 same-config seeds: 5 of 5 pass | `test_gate.py` on `seed_noise.json` |
| Bad models fail | Planted models (shuffled labels, size only, half the features): 3 of 3 fail | `test_gate.py` |
| Every failure exits 1 | Below the bar, below the champion by more than the tolerance, bias fail, a missing or malformed input: each exits 1. A passing set exits 0 | `test_gate.py` |
| One alert per fail | Each failing run sends exactly 1 message, to the local Slack and SMTP servers in `tests/alert_sinks.py`, no mocks | `test_gate.py` |
| A broken channel never decides | With the webhook down, the gate's exit code is the same and the error is logged | `test_gate.py` |
| Thresholds in config | Every number the gate uses is in `model.toml [gate]`; none in code | `test_gate.py` changes one and the decision follows |

### 6.4 LLM bias

| What | Bar | Measured by |
|---|---|---|
| The team's LLM bias bar | Gate fallback rate per repo and per owner team, diff match rate per repo: no judged slice (N ≥ 30) more than 0.10 worse than overall, by §6.1's rule, without a fix tried | `reports/llm_bias.json` |
| Unjudged said so | Every slice under 30 is listed with its N as not judged (decision 9) | `reports/llm_bias.json` |

### 6.5 CI (`model.yml`, product jobs in `ci.yml`)

| What | Bar | Measured by |
|---|---|---|
| Green and red | `model.yml` green on main end to end, and red at the gate on a planted bad model on a branch, by Nov 10 | two real runs, linked in `decisions.md` |
| PRs never push | On a PR the workflow stops after the gate: 0 registry pushes. On main it pushes after the gate passes | the workflow's job graph; one PR run and one main run |
| Forks get no secrets | A PR from a fork runs without secrets and its job summary says so | one fork PR run |
| Feedback time | A PR's `model.yml` run takes at most 15 minutes on a GitHub runner (decision 7 picks what CI trains to meet it) | median of 3 runs |
| Slack | Slack gets a fail, a done and a push message from real CI runs | the channel, linked in `decisions.md` |
| Product jobs fail when they should | A view that breaks with the LLM off fails the job; a cache miss in CI fails the job; no LLM key in any job | each planted once on a branch |

## Sign-off

Each owner signs for their own rows and reads the rest. Aniket reviews. Changing a bar after this needs a commit that says why.

| Who | Rows | Signed |
|---|---|---|
| Utkarsh | §2 | |
| Rochan | §3 | Nov 7 |
| Rachna | §4 | Rachna, Nov 7 |
| Vishwa | §5 | Vishwa, Nov 7 |
| Chaitali | §6 | Chaitali, Nov 9 |
| Aniket | review | |
