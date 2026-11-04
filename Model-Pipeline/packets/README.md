# Work packets

One folder per person. Your folder holds your packet (`README.md`) and is yours for `decisions.md` and notes. Code goes in `Model-Pipeline/model/` (the model) or `Data-Pipeline/meridian/` (the product), not here.

Read [PLAN.md](../PLAN.md) first, then your packet, then the contracts below. Who hands what to whom is in [PLAN.md §7](../PLAN.md#7-roles), the dates in [§10](../PLAN.md#10-schedule), and which guideline item each step covers in [§12](../PLAN.md#12-guideline-coverage).

| Folder | Who | Steps | Due |
|---|---|---|---|
| [utkarsh-data/](utkarsh-data/README.md) | Utkarsh, data engineer | A, B, K, L, diff drafter | v1 table Nov 5, c5118 Nov 7, run 2 Nov 8, diffs Nov 10 |
| [ai1-models/](ai1-models/README.md) | AI engineer 1 | C, E, Docker, Jev, Q | C Nov 8, E Nov 9, Jev and Q Nov 10 |
| [ai2-tracking/](ai2-tracking/README.md) | AI engineer 2 | tracking, D, G and the registry repo, P, the 2024 run | tracking Nov 5, D Nov 6, G Nov 9, P Nov 10 |
| [ai3-bias/](ai3-bias/README.md) | AI engineer 3 | the pass bars, F, gates, alerts, all CI | bars Nov 6, pick Nov 9, CI Nov 10 |
| [ai4-agents/](ai4-agents/README.md) | AI engineer 4 | N, writer and cache, O | N Nov 7, writer Nov 8, views Nov 10 |
| [aniket-lead/](aniket-lead/README.md) | Aniket, lead | the end of the graph: integration, faults, final runs, report | Nov 11–15 |

The work is split evenly, about 5 person-days each for Utkarsh and AI 1–4 ([PLAN.md §7](../PLAN.md#7-roles)). Aniket reviews until code freeze, then integrates. Day 1 is Thu Nov 5. Code freeze is Tue Nov 10 at the end of the day. Every report section is in by Thu Nov 12. Aniket and Utkarsh write the report Nov 13–15.

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

## Who writes which file

```
Model-Pipeline/
├── PLAN.md
├── PASS-BARS.md                 AI 3; everyone writes their own rows
├── packets/<you>/               your packet, decisions.md, notes
├── model.toml                   everyone adds their own section
├── model/
│   ├── data.py                  Utkarsh
│   ├── train.py, run_all.py     AI 1
│   ├── sensitivity.py           AI 1
│   ├── tracking.py              AI 2
│   ├── evaluate.py              AI 2
│   ├── registry.py              AI 2
│   ├── bias.py, gate.py         AI 3
├── data/                        features.parquet, features.schema.json (git): Utkarsh
├── registry/champion.json       written only by registry.py
├── reports/                     metrics.json (AI 2), bias_report.json (AI 3), seed_noise.json (AI 1), charts
├── tests/                       one test file per module, by its owner; faults/ by Aniket
├── Dockerfile, requirements.txt AI 1
└── report/REPORT.md             Aniket and Utkarsh; each owner writes their section

Data-Pipeline/
├── schema.sql                   tracking tables: Utkarsh
├── meridian/views.py            AI 4
├── meridian/fact_gate.py        AI 4
├── meridian/agents.py          AI 4 (the writer)
├── meridian/jev.py             AI 1
├── meridian/diffs.py           Utkarsh (the diff drafter)
├── meridian/llm_cache.py        AI 4
├── meridian/auditor.py          AI 2
├── meridian/pushcheck.py        AI 1 (the P9 hook only)
├── llm_cache/                   committed cache entries: AI 4
└── meridian.next.toml           the second real run's config: Utkarsh

.github/workflows/model.yml      AI 3
.github/workflows/ci.yml         AI 3 adds the product jobs
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

`Model-Pipeline/model.toml`. These keys are fixed. Each owner adds their own section: `[search.*]` (AI 1), `[eval]` and `[registry]` (AI 2), `[bias]` and `[gate]` (AI 3).

```toml
[data]
run_id = "c5118b019d642a9b"        # the block the table was built from
window_days = 365                  # Utkarsh's decision 1 can change this, by Nov 6
follow_up_end = "2025-10-01T00:00:00Z"

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
- Start runs with `model.tracking.start_run(name, owner)` (AI 2), never `mlflow.start_run` directly. It sets the tags every run needs: `block_run_id`, `git_sha`, `config_sha256`, `features_md5` (model runs), `owner`.
- Decision tests are runs too, tagged `decision=<packet>-<n>`.
- Your MLflow isn't shared. What the report needs from it goes in your `decisions.md` with the numbers. The report's final numbers come from Aniket's final runs on Nov 12, which rebuild everything from code, config and seed.

## Contract 4: what CI reads

| File | Written by | Read by | Holds |
|---|---|---|---|
| `reports/metrics.json` | `evaluate.py` (AI 2) | `gate.py`, `registry.py` | PR-AUC, ROC-AUC, F1, recall at 20% of lines, each with N, for the model and every baseline, on `val` or `test` |
| `reports/bias_report.json` | `bias.py` (AI 3) | `gate.py`, `registry.py` | every slice with N, interval and flag, plus `"pass": true` or `false` |
| `reports/seed_noise.json` | `train.py` (AI 1) | `gate.py`, `registry.py` | val PR-AUC of the chosen config over 5 seeds: mean, standard deviation |
| `registry/champion.json` | `registry.py` (AI 2) | `gate.py`, `registry.py`, the P9 hook | the champion's run id, Artifact Registry version, metrics, model md5 |

`gate.py` (AI 3) reads them and exits non-zero on a fail. A model reaches the registry only after the gate passes.

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
