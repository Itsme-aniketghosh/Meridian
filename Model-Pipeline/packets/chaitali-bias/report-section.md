# Bias, the final pick, the gate and CI (Chaitali)

This section asks whether the model and the agents serve some modules, components, newcomers or commit sizes worse than others, what fixing that would cost, and how CI checks every model change before it reaches the registry. Every choice below was written down, with its test and the rule that picks it, before its run ([decisions.md](decisions.md)). Everything is on `val` (2023). 2024 was never read.

## How bias is judged

The slices are `module`, `component`, `exp_bucket` (the author's earlier commits) and `size_bucket` (lines changed, in quarters). A slice is **judged** only when the count its metric is a share of reaches 30: positives for recall, negatives for the false-positive rate. It's **flagged** when its whole Wilson 95% interval is more than 0.10 worse than overall. That's phase 1's rule, kept so the model can be compared with the rules it replaces (decision 3). A planted slice of 500 positives at recall 0.35 against 0.50 is flagged, and the same slice at 0.50 isn't, on exact counts, so the test can't pass or fail on luck. Groups under 30 rows are pooled into `(other)`, which adds two judged slices and hides none (decision 1). That makes 62 judged tests.

Recall and the false-positive rate are measured at the alert rate today's P9 rule produces, 15.4% of commits (634 of 4,113). `[eval]`'s own threshold, precision 0.80 on at least 30 alerts, flags 0 `val` commits for every model, and at 0 alerts every recall is 0 (decision 10).

**A judged slice without a flag isn't evidence of no bias.** At these sizes the rule catches a true 0.15 recall gap only 10% to 56% of the time (power per slice, in `bias_report.json`). The report gives each slice's power next to its result.

## What the final model does by slice

The champion is the tuned forest that CI built and committed (`registry/model.joblib`), scored as it is on `val`: N = 4,113, 486 bug-inducing. Overall recall 0.519, false-positive rate 0.105, PR-AUC 0.407.

| `size_bucket` | Commits | Bug-inducing | Recall | False-positive rate | PR-AUC |
|---|---:|---:|---:|---:|---:|
| Q1 (smallest) | 1,179 | 22 (not judged) | 0.000 | 0.000 | |
| Q2 | 986 | 51 | **0.000**, flagged | 0.000 | **0.151**, flagged |
| Q3 | 917 | 114 | **0.096**, flagged | 0.034 | **0.219**, flagged |
| Q4 (largest) | 1,031 | 299 | 0.806 | **0.485**, flagged (of 732 negatives) | 0.500 |

**All five flags are on commit size, and no `module`, `component` or `exp_bucket` slice is flagged.** Three survive a Bonferroni correction over the 62 tests (Q2 recall, Q3 recall, Q4 false-positive rate); the two PR-AUC flags don't. The two harms land on different commits. Small commits get their bugs missed: the model finds **0 of the 51** in Q2. Large commits get the false alarms: half of Q4's clean commits are flagged.

Part of this is the base rate. Bug-inducing commits are 1.9% of Q1 and 29.0% of Q4, so at a fixed alert budget most alerts go to large commits. Lines × files, the rule the model replaces, is more skewed still at the same alert rate: Q2 recall 0.000, Q3 0.018, Q4 false-positive rate 0.540, plus a sixth flag on `component=Pandas API on Spark`.

**Newcomers** (`exp_bucket = 0`): 176 commits, 16 bug-inducing, too few to judge. The model catches 9 of the 16 (0.56), lines × files 7 (0.44). The model doesn't treat newcomers worse than the rule it replaces.

## Are the small commits' labels right?

SZZ labels a commit bug-inducing when a later fix changed lines it wrote, so a small commit can be blamed for a bug it didn't cause (GAPS gap 6). An LLM (Gemini 3.1 Pro on Vertex, $2.52) read each Q2 positive's fix and blamed diff. A person (Chaitali) judged 20 of them **blind**, from the commits and the Jira ticket, without seeing the model's answer. The rule (decision 11) used the model only if it agreed with the person on at least 15 of 20. **It agreed on 10.** It called 9 of the 13 labels the person judged right wrong, so it wasn't used.

From the hand check: **7 of the 20 Q2 labels are wrong, 0.35 (Wilson interval 0.18–0.57).** About a third of Q2's positives may not be bug-inducing at all. That accounts for part of the 0-of-51, not all of it, since about two thirds hold. No label was changed, and none of this was used for training.

This fits what limits the model overall (GAPS gap 14, and Rachna's section):
- **Accuracy is meaningless here.** The forest is 88.5% accurate, and always saying "no bug" is 88.2%.
- **The model is 3.4× chance:** PR-AUC 0.407 against a positive rate of 0.118.
- **SZZ picks the right commit only 44% of the time**, on 130 hand-verified Defects4J and Fonte commits.

## Mitigation, and what it would cost

Each fix was scored on the forest judged on Nov 9 (`val` PR-AUC 0.406, the same kind and params as the champion, 0.0009 lower, under one seed SD), not re-run on the champion: two of the options refit, and nothing is refit to get scores. Each was scored on the same `val` rows, at the same alert budget, against the worst flagged gap (Q2 recall, 0.516 below overall). The rule (decision 5): adopt a fix only if it closes at least half that gap for at most 0.01 PR-AUC **and** at most 0.02 overall precision. Per-group thresholds use only groups known when a commit is pushed (size, not `component`, which needs a ticket), and each is fitted on one half of `val` and scored on the other.

| Option (tuned forest) | Alerts | PR-AUC | Recall | Precision | Q2 recall | Gap closed | Meets the rule |
|---|---:|---:|---:|---:|---:|---:|---|
| none | 634 | 0.406 | 0.516 | 0.396 | 0.000 | | |
| thresholds, equal recall by size | 593 | 0.406 | 0.335 | 0.275 | 0.294 | 92% | no: −0.121 precision |
| thresholds, Fairlearn TPR parity | 1,645 | 0.406 | 0.652 | 0.193 | 0.667 | 103% | no: −0.203 precision, 2.6× the alerts |
| re-sampling Q1–Q3 positives ×2 | 634 | 0.398 | 0.504 | 0.386 | 0.059 | 14% | no: closes too little |
| re-sampling ×4 | 634 | 0.345 | 0.422 | 0.323 | 0.255 | 68% | no: −0.061 PR-AUC |

**No fix qualifies, so the model ships unmitigated.** The five size flags are accepted in `model.toml [bias] accepted`, each pointing at this table. The trade-off is the base rate again. To catch small commits' bugs at a fixed budget, alerts have to come from large commits, where bugs are 15 times as common. Equal recall by size costs a third of all the bugs caught (recall 0.52 → 0.34). The same held on both LightGBM candidates.

**The precision limit was added after the first results.** As first written, the rule had only the 0.01 PR-AUC limit. A per-group threshold leaves PR-AUC unchanged by construction, so "equal recall by size" passed it while cutting overall recall from 0.50 to 0.33. The 0.02 precision limit was added on Nov 8 to catch that, and PASS-BARS §6.2 says so.

## The final pick

**The tuned forest:** `val` PR-AUC 0.407 (interval 0.365–0.451) against lines × files 0.319, a paired difference of +0.088 (+0.061 to +0.113), for the champion CI built and committed. The forest Rachna judged on her laptop has the same params and scores 0.406. Rachna picked it over the tuned LightGBM (0.413) because the two are tied and the simpler model wins a tie. Bias would have reopened that tie only if the forest had more flagged slices. Both have the same five, so the forest stands, and its bias report passes with the five accepted and written down. It's the registry's first champion.

## The gate

`gate.py` decides whether a candidate may be registered. It reads `metrics.json`, `bias_report.json`, `seed_noise.json` and `registry/champion.json`, and runs five checks: every input is there with the keys it needs; they describe the same split, N and candidate; PR-AUC beats lines × files; the bias report passes; and the candidate isn't below the champion by more than the tolerance. A fail exits 1 and sends one alert. A broken alert channel is logged and never changes the answer. Every number is in `model.toml [gate]`. Its tests send real messages to local Slack and SMTP servers.

The tolerance came from Rachna's 5 same-config seeds (SD 0.0021), decision 6:

| Tolerance | Seed pairs passing (of 20) | Planted bad models failing (of 3) |
|---|---:|---:|
| any drop | 10 | 3 |
| 2 SD | 19 | 3 |
| **2√2 SD** (2 SD of a difference of two seeds) | **20** | **3** |

The planted models are shuffled labels (0.154), size only (0.388) and half the features (0.384). **Two of them beat lines × files (0.319)**, so the bar alone wouldn't stop them. The champion check does.

## CI

`model.yml` runs on every pull request and push to main that touches `Model-Pipeline/`. It builds the model's Docker image, runs data check → train → seed noise → evaluate → bias → gate → LLM bias inside it, and, on main only, registers and pushes the model to Artifact Registry. Main signs in to Google Cloud without a key, through Workload Identity trusted for this repo's main branch. Pull requests stop at the gate and never push.

**What CI trains (decision 7):** the full Optuna search took 5.2 minutes on a laptop but **27.7 minutes** on a GitHub runner, over the 15-minute bar for PR feedback. So PRs and pushes **retrain** each model with its committed best parameters, which reproduces the same forest. A PR's whole run then takes **4.0 minutes** (median of the first 3 green runs; 24 runs across 15 branches range 3.4–5.3 minutes), against the 15-minute bar. About two thirds of that is building the image, and the pipeline itself takes about a minute. The full search is a run started by hand. An earlier version compared a refit with the laptop's committed scores to 1e-9, and failed on the Linux runner, since the runner doesn't reproduce a laptop's floats that exactly. So the scores and the check are now made on the same machine.

**When it runs (decision 8):** on PRs plus main. At most about 40 runs a day, and the repo is public, so runner time is free.

**Shown on real runs:** green on a PR, end to end. **Red at the gate on a planted bad model** (shuffled labels, a draft PR never merged): the bar failed at 0.174 against 0.319, and bias failed with 2 open flags. On main, the registry pushed the champion to Artifact Registry, and Rochan committed the same bytes to `registry/`. **The product jobs fail when they should:** three failures planted one at a time on a draft PR each turned `ci.yml` red on Linux and Windows. They were a view that breaks with the LLM off, a cache miss that ships the template instead of stopping, and an LLM key in a job, which a new test checks every workflow for.

## LLM bias

Pretrained models need bias checks too. The writer's texts and the diff drafter's lines are sliced with the same rule.

**Gate fallback** is how often the fact gate rejects the LLM's text and the template ships instead. Vishwa's writer ran the real block's 204 tasks on four models. The shipped model, Gemini 3.8 Flash, falls back on **4 of 204 (0.020)**, and **no slice is flagged**: `django/django` 1 of 144, consumer texts 3 of 68, stall reasons 0 of 68, ticket drafts 1 of 68. Owner team, the slice the bar names, **can't be judged**: none of the 43 teams has 30 texts, and neither do 20 of the 21 repos. With power of 0.26–0.48 on the judged slices, the pass says little. The other three models fall back on 6.9% to 9.3% of texts, with no flags either.

**Diff match** is the share of drafted lines equal to the real after-line, from Utkarsh's counts: 866 of 1,503 overall (0.576). Two slices are flagged: `django-oscar` (81 of 247, 0.33) and the 15 packages without published per-repo counts (140 of 488, 0.29). They're accepted because Utkarsh's decision 11 scored every `url()` style on all 20 packages. No single style closes the gap, since each real migration chose its own.

## The context builder

GAPS gap 16 asked for each stalled team's files ranked by risk, cut where 90% of the likely problems are covered. Risk is a file's bug fixes in the last year. The champion isn't used, since it's Spark-only and every call site in the migration is in a Django-ecosystem repo. At first the real block had no bug-fix history outside Spark, so there was nothing to rank. Gap 24 (Utkarsh) now reads the package repos' fixes from their commit subjects. Those rows land in the next block built, so the numbers below are a **preview**: gap 24's history recomputed from the real block's own files, on a copy of the block.

| Real block, 68 consumer tasks | All | Kept | Kept share |
|---|---:|---:|---:|
| Files | 367 | 127 | 34.6% |
| Call sites | 1,526 | 495 | 32.4% |
| Facts characters | 40,996 | 21,920 | 53.5% |
| Bug fixes covered (48 teams have any) | 591 | 570 | **96.4%** |

**The ranking keeps 96.4% of the bug fixes with a third of the files. A random order with the same files keeps 73.3%.** That meets the bar written before the run (at least 90%, and at least 10 points over random), so switching it on is recommended. It would cut the consumer facts by 46%. Switching it on is Vishwa's call, since it changes every consumer cache key. The numbers get rerun on the first block built with gap 24's code.

## Not done, and limits

- **Slack:** the repo has no `MERIDIAN_SLACK_WEBHOOK` secret, so CI's fail, done and push messages have gone to each run's summary, not to Slack. The guideline's notification bar isn't shown on a real channel yet.
- **Not run on a real fork:** that a fork's PR gets no secrets. The workflow handles it, and says so in the summary, but no fork PR has run.
- **Small slices.** Most modules, components and every owner team are too small to judge at N ≥ 30, so the bias report can rarely tell a fair slice from an unfair one.
- **Q2's 35% label noise** rests on 20 hand-checked commits (interval 0.18–0.57). Q4 wasn't hand-checked, so whether small commits are labelled worse than large ones can't be said.
