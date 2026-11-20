# S5 drift results

Recorded **Thu Nov 19, 2026**, before any threshold was tuned (PLAN §5). Block `c5118b019d642a9b`, table md5 `9c0ab220…`. Thresholds as written in `monitor.toml`. Reports: `reports/`.

## 1. The 7,238 commits from 2025–26 against train (26,050 rows)

`python -m monitor.drift --days 0`

| | |
|---|---|
| PSI flags (top-5, ≥ 0.2) | `prior_bug_fixes` 0.33 (median 5 → 1). `la` 0.035, `ld` 0.006, `nf` 0.001, `nd` 0.002 |
| Drifted features | 8 of 16 (50%): `ndev`, `age`, `nuc`, `exp`, `sexp`, `prior_bug_fixes`, `fix`, `ticket_type` |
| **Retrain** | **Yes**, on both rules |

The last 30 days alone (454 commits, Sep 1 – Oct 1 2026) also retrains: PSI on `la` 0.28 (median 29 → 78.5) and `prior_bug_fixes` 0.22, 62% drifted.

**Decision for the demo: the trigger is real.** `--simulate` is kept for the "not better, kept the old model" branch and for the test (75% drifted, all five top features flagged).

## 2. Check: do the years the model was validated on drift?

The same rule, run on labelled years against train:

| Current | Rows | PSI flags | Drifted share | Retrain |
|---|---|---|---|---|
| val 2023 | 4,113 | none (`prior_bug_fixes` 0.155) | 56% | yes, dataset drift only |
| test 2024 | 3,762 | none (`prior_bug_fixes` 0.124) | 56% | yes, dataset drift only |
| train 2022 alone | 3,222 | `prior_bug_fixes` 0.22 | 50% | yes |

**The dataset-drift rule (≥ 50% of features) fires on every later year**, including val 2023, where the model beats lines × files by 0.088 PR-AUC. The history features (`exp`, `sexp`, `ndev`, `nuc`, `age`) grow with the repo's age, so a newer year always differs from a 2015–22 pool. Under the rule as written, every block retrains. The PSI rule on the top-5 features doesn't fire on val or test, but does fire on 2025–26.

`prior_bug_fixes` falls year on year (train median 5, val and test 3, 2025–26 1). That is worth a look on the data side before it is called drift: the fix rule needs a Jira ticket resolved as Fixed, and recent tickets may not be resolved yet at the snapshot.

**For Fri Nov 20 (thresholds checked on real data, signed by the team):** keep or change the 50% dataset-drift rule. Nothing is changed here until then.

## 3. Method notes

- Evidently 0.7.23 (the newest) fails to import under the pinned pandas 3.1.0, and downgrading pandas would change the table's bytes. `drift.py` reimplements Evidently's default stattests with scipy, per column: reference > 1,000 rows → normed Wasserstein (numeric, > 5 values) or Jensen-Shannon (the rest), drifted at ≥ 0.1; smaller → KS or chi-squared at p < 0.05.
- PSI: 10 bins at the reference's deciles (tied edges merge), empty bins at 1e-4.
- Columns: the model's inputs, `model.train.NUMERIC` + `ticket_type`. No labels read.
