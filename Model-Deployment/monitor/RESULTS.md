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

# Threshold check on real data

Run **Sat Nov 21, 2026**, same block. `python -m monitor.check_thresholds --out reports/threshold_check.json` reproduces sections 4–6. The test for every rule was fixed before looking: **quiet on the months and quarters where the model is known to work (val 2023, test 2024), firing on 2025–26.** The proposals in section 7 were chosen after seeing this data, so they are proposals for the team to sign off, not changes. `monitor.toml` is unchanged.

## 4. Drift by year, against train

| Year | Split | PSI `prior_bug_fixes` | Drifted share | Fires |
|---|---|---|---|---|
| 2016 | train | 0.21 | 38% | PSI |
| 2022 | train | 0.22 | 50% | PSI, share |
| 2023 | val | 0.155 | 56% | share |
| 2024 | test | 0.124 | 56% | share |
| 2025 | none | 0.40 | 56% | PSI, share |
| 2026 | none | 0.33 | 50% | PSI, share |

The other four top features never pass 0.09 in any year. `prior_bug_fixes` swings even inside train (2016 and 2022 fire against their own pool). Its median goes 12 (2016) → 4 (2019–21) → 2–3 (2022–24) → 1 (2025–26), and the share of commits that are bug fixes falls with it (0.35 → 0.11–0.16).

## 5. Drift by month (the 30-day window production runs on), against train

| Period | Months | Median commits | PSI rule fires | Share rule fires |
|---|---|---|---|---|
| 2023 (val) | 12 | 337 | **7** | **11** |
| 2024 (test) | 12 | 303 | **5** | **12** |
| 2025–26 | 22 | 339 | 22 | 22 |

**Both rules fail the check.** On the 24 known-good months, the share rule fires 23 times and the PSI rule fires 12, every time on `prior_bug_fixes` alone. `la`, `nf`, `nd` and `ld` never reach 0.2 on a 2023–24 month (max 0.155).

Alternatives, scored on the same months:

| Reference | PSI threshold | val 2023 | test 2024 | 2025–26 |
|---|---|---|---|---|
| train (as written) | 0.2 | 7/12 | 5/12 | 21/21 |
| train | 0.25 | 5/12 | 2/12 | 20/21 |
| train | 0.3 | 5/12 | 2/12 | 16/21 |
| the previous labelled year | 0.2 | 2/12 | 0/12 | 13/21 |
| **the previous labelled year** | **0.25** | **0/12** | **0/12** | **10/21** |
| the previous labelled year | 0.3 | 0/12 | 0/12 | 7/21 |

Raising the threshold against train doesn't fix it: the 2015–22 pool is too old to be "normal". Comparing each month with the latest labelled year is quiet on all 24 known-good months and still fires on about half of the 2025–26 months, on `prior_bug_fixes` and `la`.

## 6. Decay by quarter (champion vs lines × files, paired bootstrap, 1,000 reps)

Every quarter up to 2025Q3 has a closed 365-day window and ≥ 30 positives (min 47), so the check can always run.

| Quarter | Positives | PR-AUC | SD | Model − L×F [95%] |
|---|---|---|---|---|
| 2023Q1 | 129 | 0.459 | 0.045 | +0.130 [+0.079, +0.189] |
| 2023Q2 | 126 | 0.426 | 0.043 | +0.052 [**−0.011**, +0.111] |
| 2023Q3 | 121 | 0.372 | 0.043 | +0.041 [**−0.005**, +0.085] |
| 2023Q4 | 110 | 0.389 | 0.046 | +0.080 [+0.038, +0.123] |
| 2024Q1 | 70 | 0.298 | 0.051 | +0.103 [+0.056, +0.158] |
| 2024Q2 | 89 | 0.347 | 0.049 | +0.113 [+0.067, +0.152] |
| 2024Q3 | 72 | 0.272 | 0.045 | +0.011 [**−0.053**, +0.059] |
| 2024Q4 | 52 | 0.202 | 0.046 | +0.011 [**−0.057**, +0.073] |
| 2025Q1 | 56 | 0.233 | 0.057 | +0.069 [+0.017, +0.140] |
| 2025Q2 | 47 | 0.217 | 0.049 | −0.004 [**−0.092**, +0.061] |
| 2025Q3 | 52 | 0.215 | 0.051 | +0.046 [**−0.018**, +0.100] |

**Both decay rules fail on quarters:**
- *Lower bound < 0* fires on 6 of 11 quarters, including two val quarters where the model is fine for the year.
- *PR-AUC falls more than 0.05* is about one SD of a single quarter (0.043–0.057), and it fires 3 times by 2024Q3. PR-AUC also falls on its own when the positive rate falls (12% in 2023 → 5–6% in 2025), so a drop in raw PR-AUC isn't decay by itself.

Pooled by half-year (SD 0.032–0.038):

| Half | Positives | PR-AUC | Model − L×F [95%] |
|---|---|---|---|
| 2023H1 | 255 | 0.439 | +0.104 [+0.063, +0.141] |
| 2023H2 | 231 | 0.380 | +0.068 [+0.029, +0.102] |
| 2024H1 | 159 | 0.317 | +0.105 [+0.069, +0.140] |
| 2024H2 | 124 | 0.232 | +0.007 [**−0.044**, +0.044] |
| 2025H1 | 103 | 0.213 | +0.032 [**−0.016**, +0.083] |
| 2025H2 (Q3 only) | 52 | 0.215 | +0.046 [**−0.018**, +0.100] |

By half-year, 2023H1 to 2024H1 are quiet. From 2024H2 on, the model's edge over lines × files is no longer clear of 0 in any half. 2024H2 is in the test year, so under the check's own test it counts as a false alarm, unless the decay started there. The test year as a whole still beat lines × files. Read it as: **the edge has been shrinking since late 2024, which is likely real decay and a second real trigger for the demo**, with 2024H2 the one known-good period where the proposed rule fires. 2025H2 (Q3 only, 52 positives) is under the proposed 100, so the newest half the rule would judge today is 2025H1.

## 7. Proposals for sign-off

| Rule as written | Check | Proposal |
|---|---|---|
| PSI ≥ 0.2 on a top-5 feature, against train | 12/24 false alarms | **Reference: the latest labelled year** (rolls forward with each refresh), **PSI ≥ 0.25**. 0/24 false alarms, 10/21 new months fire |
| ≥ 50% of features drifted (Evidently) | 23/24 false alarms | **Not a retrain trigger.** Keep it in the report and on the dashboard |
| Decay: lower bound < 0, newest quarter | 6/11 fire, 2 on val | **Newest half-year** with a closed window and ≥ 100 positives; retrain when the paired lower bound < 0. 1/4 known-good halves fire (2024H2); today it fires on 2025H1 |
| Decay: PR-AUC falls > 0.05 | ≈ 1 SD; confounded by the positive rate | **Drop it.** The paired difference against lines × files already covers it, on the same rows |

Open on the data side: is the fall in `prior_bug_fixes` and in the bug-fix share real (Spark maturing), or tickets not yet resolved as Fixed at the snapshot? Either way, the proposed reference compares like with like.
