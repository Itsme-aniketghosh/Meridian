# Work packets

One folder per person. Your folder holds your packet (`README.md`) and is yours for notes and drafts. Code goes in `Model-Pipeline/model/`, not here.

Read [PLAN.md](../PLAN.md) first, then your packet, then the contracts below. Who hands what to whom is in [PLAN.md §7](../PLAN.md#7-roles), and the dates are in [§10](../PLAN.md#10-schedule).

| Folder | Who | Steps | Terraform | Due |
|---|---|---|---|---|
| [utkarsh-data/](utkarsh-data/README.md) | Utkarsh, data engineer | GCP base, A, B | `backend`, `apis`, `iam`, `wif`, `budget`, `storage` | base Nov 4, table Nov 6 |
| [ai1-models/](ai1-models/README.md) | AI engineer 1 | C, Docker image | `registry` | Nov 8 |
| [ai2-tracking/](ai2-tracking/README.md) | AI engineer 2 | D, MLflow server, the 2024 run | `mlflow` | server Nov 5, eval Nov 7 |
| [ai3-bias/](ai3-bias/README.md) | AI engineer 3 | F, gates, alerts, CI workflow | | pick Nov 9, CI Nov 10 |
| [ai4-registry/](ai4-registry/README.md) | AI engineer 4 | E, G, I | | Nov 10 |
| [aniket-lead/](aniket-lead/README.md) | Aniket, lead | integration, simulated faults, final runs, report | | Nov 11–15 |

Hard dates: code freeze Tue Nov 10 at the end of the day. Every report section in by Thu Nov 12. Aniket writes the report Nov 13–15.

## How every decision gets made

Each packet has a "Decisions" table. Every row is a choice you'll face, the test that settles it, and why the choice matters. The rule:

1. Write the test and what result picks which option before you run it. One line in your packet folder (`decisions.md`) is enough.
2. Run it. Log it to MLflow if it's a model run.
3. Write down the result, the N and what you picked. That line goes into your report section as is.

A decision with no test behind it doesn't go in the report. If the test can't settle it in time, pick the simpler option and say so.

## Ground rules

From the phase 1 docs:

- Pass bars are written and committed before the run they judge. Changing one needs a commit that says why ([05-test](../../docs/05-test.md)).
- Split by time, never at random. Nobody looks at 2024 until the final run on Nov 12.
- Every metric carries its N.
- A model ships only if it beats its baseline. Not beating it is a result, and we report it.
- Nothing that changes a result is hard-coded. It lives in `Model-Pipeline/model.toml`, whose hash goes on every MLflow run.
- Seeds are fixed in `model.toml`. Same table, same config, same seed: same model.
- No names or emails leave the block. The feature table has none.
- To change a contract below, say so in the team chat first, then change this page in the same PR.

## Who writes which file

```
Model-Pipeline/
├── PLAN.md
├── packets/<you>/           your packet, decisions.md, notes
├── model.toml               everyone adds their own section
├── model/
│   ├── data.py              Utkarsh
│   ├── train.py             AI 1
│   ├── tracking.py          AI 2
│   ├── evaluate.py          AI 2
│   ├── bias.py              AI 3
│   ├── gate.py              AI 3
│   ├── sensitivity.py       AI 4
│   └── registry.py          AI 4
├── data/                    features.parquet (DVC), features.schema.json (git): Utkarsh
├── reports/                 metrics.json (AI 2), bias_report.json (AI 3), charts
├── tests/                   one test file per module, by its owner; faults/ by Aniket
├── Dockerfile               AI 1
├── requirements.txt         AI 1 keeps it pinned; everyone adds what they import
└── report/REPORT.md         Aniket edits; each owner writes their section

infra/terraform/             Utkarsh, except mlflow.tf (AI 2) and registry.tf (AI 1)
.github/workflows/model.yml  AI 3
```

## Contract 1: the feature table

`Model-Pipeline/data/features.parquet`, one row per Spark first-parent commit authored 2015-01-01 or later, sorted by `authored_at`, then `sha`. Every feature is computed from what was known at the commit's own time, never from the snapshot. Utkarsh owns this table and `features.schema.json`.

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

The feature names follow Kamei et al. 2013, the usual just-in-time defect prediction set, so readers can compare with published numbers. Utkarsh's decisions (the window, the start year, the module split) can change values but not columns.

## Contract 2: config

`Model-Pipeline/model.toml`. These keys are fixed. Each owner adds their own section: `[search.*]` (AI 1), `[eval]` (AI 2), `[bias]` and `[gate]` (AI 3), `[registry]` (AI 4).

```toml
[data]
run_id = "c5118b019d642a9b"        # the block the table was built from
window_days = 365                  # Utkarsh's decision 1 can change this, by Nov 5
follow_up_end = "2025-10-01T00:00:00Z"

[split]
train = [2015, 2022]
val = 2023
test = 2024

[seed]
value = 1
```

## Contract 3: MLflow

- One experiment: `meridian-defect-risk`. The duplicate-ticket model, if built, gets `meridian-duplicates`.
- Start runs with `model.tracking.start_run(...)` (AI 2), never `mlflow.start_run` directly. It sets the tags every run needs: `block_run_id`, `git_sha`, `model_toml_sha256`, `features_md5`, `owner`.
- Decision tests are runs too: tag them `decision=<packet>-<number>`, for example `decision=ai1-3`, so the report can link to each one.
- Registered model: `defect-risk`. Only `registry.py` (AI 4) moves the `champion` alias.

## Contract 4: what CI reads

| File | Written by | Read by | Holds |
|---|---|---|---|
| `reports/metrics.json` | `evaluate.py` (AI 2) | `gate.py`, `registry.py` | PR-AUC, ROC-AUC, F1, recall at 20% of lines, each with N, for the model and every baseline, on `val` or `test` |
| `reports/bias_report.json` | `bias.py` (AI 3) | `gate.py`, `registry.py` | every slice with N, interval and flag, plus `"pass": true` or `false` |
| `reports/seed_noise.json` | `train.py` (AI 1) | `gate.py`, `registry.py` | val PR-AUC of the chosen config over 5 seeds: mean, standard deviation |

`gate.py` (AI 3) reads all three and exits non-zero on a fail. A model reaches the registry only after the gate passes.
