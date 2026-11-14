# The 2024 run: full log (Nov 14, 01:57 UTC)

Every step of `packets/rochan-tracking/RUN-2024.md`, in order: the command, what it printed, and PASS / FAIL / WARN. Nothing is left out.

**Who and why:**
- **Run by:** Utkarsh, on his Mac (macOS, Apple silicon), from `Model-Pipeline/` with the repo's `.venv` (Python 3.11).
- **Why not Rochan:** the checklist names Rochan as runner, but he couldn't be reached. **Aniket gave the go-ahead** for Utkarsh to run it (relayed by Utkarsh, Nov 13 evening).
- **Watching:** Aniket watched the run live, as the checklist asks (confirmed by Aniket, Nov 14).
- **Before the run (Aniket, Phase 4):** the freeze tag `freeze-nov13` exists (on #117, 978af89). The fault table and go/no-go are Aniket's; the go was given as above. They aren't re-recorded here.

## Step 1: open 2024 — PASS
```
git switch -c run-2024 freeze-nov13          # 978af89 Merge pull request #117
sed: model.toml line 17  test_allowed = false -> true
git commit -m "Open 2024 for the one test run"   # df12728
git push -u origin run-2024                    # pushed, no workflow started
```

## Step 2: check before scoring
| Check | Command | Output | Verdict |
|---|---|---|---|
| Only `model.toml` differs | `git diff --stat freeze-nov13 HEAD` | `Model-Pipeline/model.toml | 2 +-`, 1 file | PASS |
| No tracked changes | `git status --short` | (empty) | PASS |
| Feature table frozen | `md5 -q data/features.parquet` | `9c0ab22048fff931ac89e27c20112c56` (expected the same) | PASS |
| Champion frozen | `md5 -q registry/model.joblib` | `2a0c9cc656b43fc4e471c70223c4b4ca` (expected the same) | PASS |
| MLflow tags | `python -m model.tracking --owner utkarsh` | `git_dirty: false`, `git_sha: df12728b6c588614ee5b01ae50d7df9bcc1cb2c0`, `block_run_id: c5118b019d642a9b`, `features_md5: 9c0ab220…`, `config_sha256: 3eb495db…`, `meridian_config_sha256: 748f841a…`, store `sqlite:///…/Meridian/mlflow.db` | PASS |
| SHA, time | `git rev-parse HEAD`, `date -u` | `df12728b6c588614ee5b01ae50d7df9bcc1cb2c0`, Sat Nov 14 01:57:43 UTC 2026 | recorded |

**WARN (this machine, not the run):** `import lightgbm` fails here because `libomp.dylib` isn't installed (no Homebrew). The champion is a random forest, `evaluate.py`'s 2024 path doesn't load LightGBM (`train.py` imports it only to build an `lgbm` model), and the run completed. The same missing library makes 10 LightGBM unit tests error on this Mac; CI's Linux runners have it.

**Note on `--owner`:** the checklist says `--owner rochan`. It was `--owner utkarsh` here, since Utkarsh ran it, so the MLflow run's `owner` tag is `utkarsh`.

## Step 3: score the champion — PASS
```
python -m model.evaluate --score-champion --column forest --split test --out reports/2024
champion 85b4684a (2a0c9cc656b43fc4e471c70223c4b4ca): val PR-AUC 0.4067, as recorded. Wrote reports/2024/champion_val_scores.csv, reports/2024/champion_test_scores.csv, reports/2024/champion_candidate.json
```
The model md5, the table and the recorded val PR-AUC all matched before any 2024 row was scored.

## Step 4: evaluate 2024, once — PASS (ran once; no rerun)
```
python -m model.evaluate --split test --candidate reports/2024/champion_candidate.json --scores reports/2024/champion_test_scores.csv --out reports/2024
test: N = 3,762, positives = 283 (7.5%); candidate forest
                 PR-AUC      95% interval  ROC-AUC recall@20%   prec recall alerts   vs lines x files (paired)
lines_x_files     0.214 [0.179, 0.259]    0.770      0.027   n/a  0.000      0
churn             0.196 [0.165, 0.238]    0.762      0.200   n/a  0.000      0   -0.018 [-0.034, -0.004]
bug_fixes         0.166 [0.136, 0.211]    0.686      0.546   n/a  0.000      0   -0.048 [-0.078, -0.015]
forest            0.268 [0.224, 0.320]    0.808      0.625   n/a  0.000      0   +0.054 [+0.028, +0.081]
recall at 20% of the lines with no model: 0.608 reading the smallest commits first, 0.200 in random order
wrote reports/2024/metrics.json, reports/2024/pr_auc_test.png, reports/2024/confusion_test.png, reports/2024/pr_curves_test.png in 1.7 s
MLflow run 4d107a397eab41528e9396ab2480648b in meridian-defect-risk
```
`prec n/a`, `recall 0.000` and `alerts 0` are expected: the champion has no alert threshold (decision 3: no cutoff reached precision 0.80 on val).

## Step 5: the result
- N = 3,762, positives = 283 (7.5%).
- Forest PR-AUC 0.268 [0.224, 0.320], ROC-AUC 0.808, recall at 20% of lines 0.625.
- lines × files PR-AUC 0.214 [0.179, 0.259].
- Forest − lines × files, paired: +0.054 [+0.028, +0.081] (`metrics.json`: `models.forest.vs_lines_x_files`, n = 3,762).
- **Verdict (PLAN §4): the interval is above 0, so the model ships.**

## Step 6: golden md5s — PASS
`golden/2024.json`:

| file | md5 |
|---|---|
| data/features.parquet | 9c0ab22048fff931ac89e27c20112c56 |
| registry/champion.json | ad6bbe9d3fe31ae8f12a2ea045b34ad8 |
| registry/model.joblib | 2a0c9cc656b43fc4e471c70223c4b4ca |
| reports/2024/champion_candidate.json | 81f1e1b417da8db69cfa2415090eb465 |
| reports/2024/champion_val_scores.csv | ceea99785c2117c773eda33562c97e0b |
| reports/2024/champion_test_scores.csv | 7dc54029ea989d7da689df6e6c705e71 |
| reports/2024/metrics.json | bdb4c6c67441f55d077e8ec6c1482abf |
| reports/champion/bias_report.json | 109a96f71197a02cbc1095e8739eac8e |
| reports/llm_bias.json | 420af9371f3ab28f9bf71c29eb348803 |

## Step 7: commit and hand over
- Committed on `run-2024`: `reports/2024/` (the two scores CSVs with `git add -f`), `golden/2024.json`, the results in `packets/rochan-tracking/pass-bars.md` ("Evaluation, Nov 14 (2024, once)"), and this log.
- `test_allowed` set back to `false` in the same branch, in its own commit.
- Pushed. **Aniket opens the PR and merges it** (the checklist's step). After that, Aniket's final product runs.

## Nothing else ran on 2024
No other `--split test` run, no rerun, no other model or threshold. The scores were computed once (step 3) and evaluated once (step 4).
