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
- Start runs with `model.tracking.start_run(name, owner)` (Rochan), never `mlflow.start_run` directly. It sets the tags every run needs: `block_run_id`, `git_sha`, `git_dirty`, `config_sha256` (model.toml), `meridian_config_sha256` (meridian.toml), `features_md5` (model runs), `owner`.
- Decision tests are runs too, tagged `decision=<packet>-<n>`.
- Your MLflow isn't shared. What the report needs from it goes in your `decisions.md` with the numbers. The report's final numbers come from Aniket's final runs on Nov 12, which rebuild everything from code, config and seed.

### How to log a run

Install `Model-Pipeline/requirements.txt` (it pins `mlflow==3.18.0`). Then:

```python
import mlflow
from model import tracking

# a model run, in meridian-defect-risk. Pass the paths your command was given, so the hashes match
with tracking.start_run("lgbm-tuned", owner="rachna", decision="rachna-1", config=a.config, features=a.features):
    mlflow.log_params(params)
    mlflow.log_metric("val_pr_auc", 0.41)
    with tracking.start_run("trial-7", owner="rachna", nested=True, config=a.config, features=a.features):
        ...  # one Optuna trial

# LLM calls, in meridian-agents. block_run_id is the --run-id the agent reads
with tracking.start_run("writer", owner="vishwa", experiment="meridian-agents", block_run_id=run_id):
    tracking.log_llm_call(agent="writer", model=m, prompt_version="v1", tokens_in=812, tokens_out=96,
                          cost_usd=0.0004, cache_hit=False, gate_pass=True, task="rewrite", repo=repo, team=team)
```

| | |
|---|---|
| Which store | `$MLFLOW_TRACKING_URI` if set, else `mlflow.db` at the repo root from any folder. Artifacts in `mlruns/` next to it. `mlflow ui` at the repo root to look |
| What it refuses | an unknown experiment, an owner that isn't a lower-case packet name, a decision not shaped `<packet>-<n>`, a model run with no `model.toml` or feature table, a table whose md5 or block doesn't match its `features.schema.json`, an agents or auditor run without `block_run_id`, a tag you try to set that `tracking.py` owns. Each fails before a run is made, with what to fix |
| LLM calls | One row per call in the run's `llm_calls.json` table, written when the `with` ends, with totals as metrics: `llm_calls`, `llm_api_calls`, `cache_hit_rate`, `gate_pass_rate` (N = `gate_checked`; `gate_pass=None` isn't counted), `tokens_in`, `tokens_out`, `cost_usd`. No prompt or response text is logged, and an email in `task`, `repo` or `team` is refused. `tracking.load_llm_calls()` returns every call as a DataFrame for slicing |
| Threads | Calls from worker threads find the one open agents run. With two open, pass `run_id=` |
| Without `with` | `run = tracking.start_run(...)`, then `tracking.end_run()` |
| In tests | Set `MLFLOW_TRACKING_URI` to a temporary `sqlite:///.../mlflow.db`, or your tests write to the real store |
| From `Data-Pipeline/` | `sys.path.insert(0, str(<repo root> / "Model-Pipeline"))`, then `from model import tracking`. The Data-Pipeline image doesn't have mlflow yet |
| To check | `python -m model.tracking --owner <you>` prints the tags a run would get, and logs nothing |

## Contract 4: what CI reads

| File | Written by | Read by | Holds |
|---|---|---|---|
| `reports/metrics.json` | `evaluate.py` (Rochan) | `gate.py`, `registry.py` | PR-AUC, ROC-AUC, F1, recall at 20% of lines, each with N, for the model and every baseline, on `val` or `test` |
| `reports/bias_report.json` | `bias.py` (Chaitali) | `gate.py`, `registry.py` | every slice with N, interval and flag, plus `"pass": true` or `false` |
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
