# Work packets

One folder per person. Your folder holds your packet (`README.md`) and is yours for `decisions.md` and notes. Code goes in `Model-Pipeline/model/` (the model) or `Data-Pipeline/meridian/` (the product), not here.

Read [PLAN.md](../PLAN.md) first, then your packet, then the contracts below. Who hands what to whom is in [PLAN.md §7](../PLAN.md#7-roles), the dates in [§10](../PLAN.md#10-schedule), and which guideline item each step covers in [§12](../PLAN.md#12-guideline-coverage).

| Folder | Who | Steps | Due |
|---|---|---|---|
| [utkarsh-data/](utkarsh-data/README.md) | Utkarsh, data engineer | A, B, K, L, diff drafter | v1 table Nov 5, c5118 Nov 7, run 2 Nov 8, diffs Nov 10 |
| [rachna-models/](rachna-models/README.md) | Rachna, AI engineer | C, E, Docker, Jev, Q | C Nov 8, E Nov 9, Jev and Q Nov 10 |
| [rochan-tracking/](rochan-tracking/README.md) | Rochan, AI engineer | tracking, D, G and the registry repo, P, the 2024 run | tracking Nov 5, D Nov 6, G Nov 9, P Nov 10 |
| [chaitali-bias/](chaitali-bias/README.md) | Chaitali, AI engineer | the pass bars, F, gates, alerts, all CI | bars Nov 6, pick Nov 9, CI Nov 10 |
| [vishwa-agents/](vishwa-agents/README.md) | Vishwa, AI engineer | N, writer and cache, O | N Nov 7, writer Nov 8, views Nov 10 |
| [aniket-lead/](aniket-lead/README.md) | Aniket, lead | the end of the graph: integration, faults, final runs, report | Nov 11–15 |

The work is split evenly, about 5 person-days each for Utkarsh, Rachna, Rochan, Chaitali and Vishwa ([PLAN.md §7](../PLAN.md#7-roles)). Aniket reviews until code freeze, then integrates. Day 1 is Thu Nov 5. Code freeze is Tue Nov 10 at the end of the day. Every report section is in by Thu Nov 12. Aniket and Utkarsh write the report Nov 13–15.

Phase 2 runs locally and on GitHub Actions. The only cloud piece is the GCP Artifact Registry repo the model is pushed to. Terraform and everything else on GCP is phase 3.

## How every decision gets made

Each packet has a Decisions table. Every row is a choice you'll face, the test that settles it, and why the choice matters. The rule:

1. Write the test and the result that picks each option before you run it. One line in your folder's `decisions.md` is enough.
2. Run it. Log it to MLflow if it's a model or agent run, tagged `decision=<packet>-<n>`.
3. Write down the result, its N and what you picked. That line goes into your report section as is.

A decision with no test behind it doesn't go in the report. If the test can't settle it in time, pick the simpler option and say so.

## Ground rules

From the phase 1 docs:

- Pass bars are written and committed before the run they judge. Changing one needs a commit that says why ([05-test](../../docs/05-test.md)).
- Split by time, never at random. Nobody looks at 2024 until the final run on Nov 12.
- Every metric carries its N.
- A model ships only if it beats its baseline. Not beating it is a result, and we report it.
- Nothing that changes a result is hard-coded: model settings in `Model-Pipeline/model.toml`, product settings in `Data-Pipeline/meridian.toml`. Both hashes go on every run.
- Seeds are fixed. Same input, same config, same seed: same output.
- LLMs write text and code, never facts. Every view works with the LLM off.
- No names or emails leave the block. The feature table has none, and agent facts never include people.
- To change a contract below, say so in the team chat first, then change this page in the same PR.

## System decisions (Nov 6)

Settled now so integration on Nov 11 is a check, not a repair. Each one came out of reviewing PRs #28–#32.

| # | Decision | Why |
|---|---|---|
| S1 | **One environment, two lockfiles.** `pip install -r requirements.txt` at the repo root installs both. A package used by both pipelines (numpy, pandas, scikit-learn, pyarrow) is pinned only in `Data-Pipeline/requirements.txt`; a model-only one (lightgbm, optuna, shap, mlflow, matplotlib) only in `Model-Pipeline/requirements.txt`, which includes the first. Never pin a package twice. Python 3.11 | Two pins of pyarrow (22.0.0 and 26.0.1) were about to land. A different pyarrow writes different parquet bytes, so `features_md5` breaks on someone's machine |
| S2 | **Every Model-Pipeline test runs in CI.** `ci.yml`'s `model` job runs `Model-Pipeline/tests` and flake8 (`Data-Pipeline/setup.cfg`) on Linux and Windows. A new test file in `Model-Pipeline/tests/` runs with no workflow change | Until #29 no job ran them: model code merged green without its tests running |
| S3 | **One copy of each shared helper**, owned by one person. Config: `model.data.load_config` (Utkarsh). Paired bootstrap: Rochan's, in `model/evaluate.py`; Rachna's stand-in in `train.py` is deleted the day it merges. Tracking: `model.tracking` (Rochan). Fact gate: `meridian.fact_gate` (Vishwa). Import them, don't copy them | Two bootstraps or two config readers drift, and then two numbers for the same thing reach the report |
| S4 | **Imports.** `model` is one package, run from `Model-Pipeline/` as `python -m model.<module>`. Product code in `Data-Pipeline/` that needs `model.tracking` puts `Model-Pipeline` on `sys.path` (Contract 3). Nothing in `Model-Pipeline` imports `meridian` | One direction only, so neither pipeline can break the other's tests |
| S5 | **Owners and decision tags.** `owner` is one of `aniket`, `chaitali`, `rachna`, `rochan`, `utkarsh`, `vishwa`. A decision tag is `<owner>-<n>`, passed to `tracking.start_run(decision=...)`, never set with `mlflow.set_tag` | `tracking.py` refuses anything else, so a typo can't hide a decision run from the report |
| S6 | **Scores are written once, read everywhere.** `train.py` writes every model's `val` scores (Contract 4 below); `evaluate.py`, `bias.py` and `gate.py` read them and join the slice columns from `features.parquet` on `sha`. Nobody refits a model to get its scores | A refit with another thread count or library version is a different model. Then evaluation, bias and the gate judge three different things |
| S7 | **Speed knobs never change a number.** `MERIDIAN_WORKERS` sets how many independent fits run at once (model families, seeds, tuning studies; default: every core). `MERIDIAN_THREADS` sets threads per forest or LightGBM fit (default up to 8; `pmap` gives each worker its share). Predictions are identical at any setting (`tests/test_parallel.py`: max difference 0.0). Trials inside one Optuna study stay one at a time, because a seeded TPE sampler must see them in order. On a GCP or Linux machine raise both | Speed is a priority, but the final numbers must come out the same on a laptop, in CI and in the image |
| S8 | **The final numbers come from the Docker image.** Laptops are for development. The image (Linux, pinned) gives the numbers in the report, and CI checks the same image | Windows and macOS differ in small ways (git rename detection, Jedi's folder order: phase 1 Appendix D) |
| S9 | **A PR that needs another PR says so** in its first line ("depends on #29") and is merged after it. Merge order this week: #29 → #32 → #30 → #31 → #28 → Rachna's models PR → the lead's system PR | Stacked work merges cleanly; out of order it fails CI on a missing file |
| S10 | **A contract change is announced in the team chat and made on this page in the same PR**, as the ground rules say. #28 (Contract 5: `check` takes `teams` and `people`) and #32 (Contract 3: two tags, the store at the repo root) are this week's | Everyone builds against this page |

## Integration tests (before Nov 11)

Integration day only works if every hand-off was tested before it. Each producer commits a small example of its output, and each consumer tests against that example, not against the producer's code:

| By | Who | What |
|---|---|---|
| Nov 8 | each producer | An example file in `Model-Pipeline/tests/contracts/`: `val_scores.csv` and `candidate.json` (Rachna), `seed_noise.json` (Rachna), `metrics.json` (Rochan), `bias_report.json` (Chaitali), `champion.json` (Rochan). Each comes from a run on `tests/fake_table.py`, so it has the real shape |
| Nov 8 | each consumer | A test that reads the example and fails on a missing key or column. `gate.py` reads all four; `registry.py` reads metrics, bias and seed noise; P9 reads the champion |
| Nov 9 | Rachna | `python -m model.run_all --fake` runs data → train → evaluate → bias → gate on `fake_table` in under 2 minutes, and CI's `model` job runs it on every PR |
| Nov 9 | Vishwa, Utkarsh, Rachna | The product hand-off: the writer, the diff drafter and Jev call `fact_gate.check(text, facts, teams=..., people=...)` and `tracking.log_llm_call(..., facts_hash=...)` in a test on the fixture, with the LLM off |
| Nov 10 | Chaitali | `model.yml` runs `run_all` in the image on a branch, end to end |

## Who writes which file

```
Model-Pipeline/
├── PLAN.md
├── PASS-BARS.md                 Chaitali; everyone writes their own rows
├── packets/<you>/               your packet, decisions.md, notes
├── model.toml                   everyone adds their own section
├── model/
│   ├── data.py                  Utkarsh
│   ├── train.py, run_all.py     Rachna
│   ├── sensitivity.py           Rachna
│   ├── tracking.py              Rochan
│   ├── evaluate.py              Rochan
│   ├── registry.py              Rochan
│   ├── bias.py, gate.py         Chaitali
├── data/                        features.parquet, features.schema.json (git): Utkarsh
├── registry/champion.json       written only by registry.py
├── reports/                     metrics.json (Rochan), bias_report.json (Chaitali), seed_noise.json (Rachna), charts
├── tests/                       one test file per module, by its owner; faults/ by Aniket
├── Dockerfile, requirements.txt Rachna
└── report/REPORT.md             Aniket and Utkarsh; each owner writes their section

Data-Pipeline/
├── schema.sql                   tracking tables: Utkarsh
├── meridian/views.py            Vishwa
├── meridian/fact_gate.py        Vishwa
├── meridian/agents.py          Vishwa (the writer)
├── meridian/jev.py             Rachna
├── meridian/diffs.py           Utkarsh (the diff drafter)
├── meridian/llm_cache.py        Vishwa
├── meridian/auditor.py          Rochan
├── meridian/pushcheck.py        Rachna (the P9 hook only)
├── llm_cache/                   committed cache entries: Vishwa
└── meridian.next.toml           the second real run's config: Utkarsh

.github/workflows/model.yml      Chaitali
.github/workflows/ci.yml         Chaitali adds the product jobs
```

## Contract 1: the feature table

`Model-Pipeline/data/features.parquet`, in git, one row per Spark first-parent commit authored 2015-01-01 or later, sorted by `authored_at`, then `sha`. Every feature is computed from what was known at the commit's own time, never from the snapshot. Utkarsh owns this table and `features.schema.json`.

| Column | Type | Meaning |
|---|---|---|
| `sha` | str | commit SHA |
| `authored_at` | str | UTC, `%Y-%m-%dT%H:%M:%SZ` |
| `year` | int | from `authored_at` |
| `split` | str | `train` (2015–2022), `val` (2023), `test` (2024), `none` (2025 on: not enough follow-up for a label) |
| `label` | int | 1 if a fix commit authored within `window_days` after this one blames it (`bug_inducing`), else 0 |
| `la`, `ld` | int | lines added, deleted |
| `nf`, `nd`, `ns` | int | files, directories, top-level modules touched |
| `entropy` | float | how spread the change is across its files |
| `ndev` | int | distinct earlier authors on these files |
| `age` | float | mean days since these files last changed |
| `nuc` | int | earlier commits on these files |
| `exp`, `sexp` | int | the author's earlier commits, overall and in this module |
| `prior_bug_fixes` | int | fix commits on these files in the 365 days before this commit |
| `fix` | int | 1 if the linked ticket is a Bug |
| `has_ticket` | int | 1 if the commit carries a ticket key |
| `ticket_type` | str | linked ticket's `effective_type`, or `none` |
| `is_minor` | int | subject starts with `[MINOR]` |
| `module` | str | slice: top-level directory with the most changed lines |
| `component` | str | slice: linked ticket's first Jira component, or `none` |
| `exp_bucket` | str | slice: `0`, `1`, `2-10`, `>10` earlier commits |
| `size_bucket` | str | slice: lines changed, by quartile on train |

The feature names follow Kamei et al. 2013, the usual just-in-time defect prediction set, so readers can compare with published numbers.

## Contract 2: config

`Model-Pipeline/model.toml`. These keys are fixed. Each owner adds their own section: `[search.*]` (Rachna), `[eval]` and `[registry]` (Rochan), `[bias]` and `[gate]` (Chaitali).

```toml
[data]
run_id = "c5118b019d642a9b"        # the block the table was built from
window_days = 365                  # Utkarsh's decision 1 can change this, by Nov 6
follow_up_end = "2025-10-01T00:00:00Z"
start_year = 2015                  # Utkarsh decision 2: the first year with rows
repo = "apache/spark"
prior_fix_days = 365               # prior_bug_fixes looks back this far

[split]
train = [2015, 2022]
val = 2023
test = 2024
test_allowed = false               # Aniket flips it on Nov 12, once

[seed]
value = 1
```

Product settings (models, prompts, spend cap, auditor ε and K) go in `Data-Pipeline/meridian.toml`, new sections `[llm]` and `[auditor]`.

## Contract 3: MLflow

- Local on each machine: `MLFLOW_TRACKING_URI=sqlite:///mlflow.db` at the repo root, which git ignores. `mlflow ui` to look.
- Experiments: `meridian-defect-risk` for the model, `meridian-agents` for LLM calls, `meridian-auditor` for auditor runs.
- Start runs with `model.tracking.start_run(name, owner)` (Rochan), never `mlflow.start_run` directly. It sets the tags every run needs: `block_run_id`, `git_sha`, `config_sha256`, `features_md5` (model runs), `owner`.
- Decision tests are runs too, tagged `decision=<packet>-<n>`.
- Your MLflow isn't shared. What the report needs from it goes in your `decisions.md` with the numbers. The report's final numbers come from Aniket's final runs on Nov 12, which rebuild everything from code, config and seed.

## Contract 4: what CI reads

| File | Written by | Read by | Holds |
|---|---|---|---|
| `reports/metrics.json` | `evaluate.py` (Rochan) | `gate.py`, `registry.py` | PR-AUC, ROC-AUC, F1, recall at 20% of lines, each with N, for the model and every baseline, on `val` or `test` |
| `reports/bias_report.json` | `bias.py` (Chaitali) | `gate.py`, `registry.py` | every slice with N, interval and flag, plus `"pass": true` or `false` |
| `reports/<tag>_val_scores.csv` | `train.py` (Rachna) | `evaluate.py`, `bias.py`, `gate.py` | `sha`, `label`, then one score column per model and baseline (`lines_x_files`, `logreg`, ...), one row per `val` row in the feature table's order. Never refit to get these (S6) |
| `reports/candidate.json` | `train.py` (Rachna) | `evaluate.py`, `bias.py`, `gate.py` | the candidate: `kind`, `params`, `scores_file`, `column` (which column of the scores file is it), `seed` |
| `reports/seed_noise.json` | `train.py` (Rachna) | `gate.py`, `registry.py` | val PR-AUC of the chosen config over 5 seeds: mean, standard deviation |
| `registry/champion.json` | `registry.py` (Rochan) | `gate.py`, `registry.py`, the P9 hook | the champion's run id, Artifact Registry version, metrics, model md5 |

`gate.py` (Chaitali) reads them and exits non-zero on a fail. A model reaches the registry only after the gate passes.

## Contract 5: agent facts and suggestions

- An agent gets a facts object: `{"facts": {<name>: <value>}, "source": {<name>: "<table>:<row key>"}}`. Every fact comes from a block row. No person's name or email is ever a fact.
- It returns JSON: `{"text": "...", "used": [<fact names>]}`, or a diff for the diff drafter.
- `fact_gate.check(text, facts)` passes only if every number, path, SHA, ticket ID and team in the text is a fact value. A miss means the template ships instead (`source = template`).
- Each offer is one `suggestion` row (Utkarsh's table): `kind`, `target`, `owner_team`, `source` (`codemod`, `llm`, `template`, `rules`), `body`, `facts_hash`, plus `agent`, `model`, `route` (`jev` or `rules`).
- Cache key: sha256 of (model, prompt version, facts JSON with sorted keys). One JSON file per entry in `Data-Pipeline/llm_cache/`.

## Contract 6: views and API

`python -m meridian.views --run-id <id> --config meridian.toml --serve 8765`, local only, read-only. Built on `tests/dashboard.py`'s pattern (standard library, no web framework).

| Path | Shows |
|---|---|
| `/` | the staff view |
| `/owner/<symbol>` | the API owner view |
| `/consumer/<team>` | the consumer view |
| `/push/<sha>` | the developer push-check notes |
| `/tickets` | the ticket tools: vague tickets with rewrites, maker drafts |
| `/api/<view>` | the same data as JSON |

Every page and JSON response carries `run_id`, `snapshot_at` and coverage, and says whether a number comes from the real block or the fixture.
