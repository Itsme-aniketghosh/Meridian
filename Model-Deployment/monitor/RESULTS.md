# S5 drift results

Recorded **Thu Nov 19, 2026**, before any threshold was tuned (PLAN §5). Block `c5118b019d642a9b`, table md5 `9c0ab220…`. Thresholds as written in PLAN §5 (then `monitor.toml`, now `deploy.toml` [drift] and [decay], contract D4). Reports: `reports/`.

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

**Checked on real data below (§4–7), with a proposal for sign-off.**

## 3. Method notes

- Evidently 0.7.23 (the newest) fails to import under the pinned pandas 3.1.0, and downgrading pandas would change the table's bytes. `drift.py` reimplements Evidently's default stattests with scipy, per column: reference > 1,000 rows → normed Wasserstein (numeric, > 5 values) or Jensen-Shannon (the rest), drifted at ≥ 0.1; smaller → KS or chi-squared at p < 0.05.
- PSI: 10 bins at the reference's deciles (tied edges merge), empty bins at 1e-4.
- Columns: the model's inputs, `model.train.NUMERIC` + `ticket_type`. No labels read.

# Threshold check on real data

Run **Sat Nov 21, 2026**, same block. `python -m monitor.check_thresholds --out reports/threshold_check.json` reproduces it in about 10 s.

**How a rule is judged, fixed before looking:** it must stay quiet on **val 2023** (the year the model was picked on, where it beats lines × files by +0.088 PR-AUC) and fire on 2025–26, which drifted in §1. As the docs/deployment/monitor packet says, nothing is judged on test 2024. Its commits appear only as the reference production would hold for 2025–26 (features, never labels), and its quarters are skipped.

The alternatives in §7 were chosen after seeing this data. They are a proposal for the team, not a change: `deploy.toml` [drift] and [decay] still hold the rules as written.

## 4. Drift by month (the window production checks)

Months a rule fires on. On val, every firing is a false alarm.

| Rule | val 2023 | 2025–26 |
|---|---|---|
| ≥ 50% of features drifted *(as written)* | **11/12** | 22/22 |
| PSI ≥ 0.2 on a top-5 feature, against train *(as written)* | **7/12** | 22/22 |
| PSI ≥ 0.25, against train | 5/12 | 21/22 |
| PSI ≥ 0.3, against train | 5/12 | 17/22 |
| PSI ≥ 0.2, against the latest labelled year | 2/12 | 14/22 |
| **PSI ≥ 0.25, against the latest labelled year** | **0/12** | **11/22** |
| PSI ≥ 0.3, against the latest labelled year | 0/12 | 8/22 |

"The latest labelled year" is the newest year with settled labels before the month: 2022 for val months, 2024 for 2025–26. The 2025–26 column counts Oct 2026, a 5-commit partial month at the snapshot. Without it, the proposed rule fires on 10 of 21 full months.

**Both rules as written fail.**
- *The share rule* fires on 11 of 12 val months. The history features (`exp`, `sexp`, `ndev`, `nuc`, `age`) grow with the repo's age, so any later month differs from a 2015–22 pool.
- *The PSI rule* fires on 7 of 12 val months, every time on `prior_bug_fixes` (up to 0.55 in Jan 2023). `la`, `nf`, `nd` and `ld` never reach 0.2 on a val month.
- Raising the PSI threshold against train doesn't fix it: 5/12 even at 0.3. The 2015–22 pool is too old to be "normal" for a month in 2023. Against 2022, the largest val PSI is 0.229 (May 2023).

`prior_bug_fixes` falls year on year: median 12 in 2016, 4 in 2019–21, 2–3 in 2022–24 and 1 in 2025–26. The share of commits that are bug fixes falls with it, from 0.35 to 0.11–0.16. Whether that is Spark maturing or fixes not yet found at the snapshot is open (packet §1, with Utkarsh).

## 5. Decay by quarter (champion vs lines × files, paired bootstrap, 1,000 reps)

Every quarter with a closed 365-day window has ≥ 30 positives (min 47), so the minimum is never the limit.

| Quarter | Positives | Positive rate | PR-AUC | SD | Model − L×F [95%] |
|---|---|---|---|---|---|
| 2023Q1 | 129 | 12.4% | 0.459 | 0.045 | +0.130 [+0.079, +0.189] |
| 2023Q2 | 126 | 13.9% | 0.426 | 0.043 | +0.052 [**−0.011**, +0.111] |
| 2023Q3 | 121 | 11.2% | 0.372 | 0.043 | +0.041 [**−0.005**, +0.085] |
| 2023Q4 | 110 | 10.2% | 0.389 | 0.046 | +0.080 [+0.038, +0.123] |
| 2025Q1 | 56 | 6.1% | 0.233 | 0.057 | +0.069 [+0.017, +0.140] |
| 2025Q2 | 47 | 7.2% | 0.217 | 0.049 | −0.004 [**−0.092**, +0.061] |
| 2025Q3 | 52 | 5.6% | 0.215 | 0.051 | +0.046 [**−0.018**, +0.100] |

**Both decay rules fail on quarters.**
- *Lower bound < 0* fires on 2 of 4 val quarters.
- *PR-AUC falls more than 0.05 under the last window*: one quarter's SD is 0.043–0.057, so 0.05 is about one SD. It fires on 1 of 3 val steps (Q2 → Q3, −0.054). PR-AUC also falls on its own when the positive rate does (12% in 2023, 6% in 2025): 2025's 0.215 against 2023's 0.389 is mostly base rate, and lines × files falls the same way (0.309 → 0.168).

## 6. Decay by half-year

| Half | Positives | Positive rate | PR-AUC | SD | Model − L×F [95%] |
|---|---|---|---|---|---|
| 2023H1 | 255 | 13.1% | 0.439 | 0.032 | +0.104 [+0.063, +0.141] |
| 2023H2 | 231 | 10.7% | 0.380 | 0.032 | +0.068 [+0.029, +0.102] |
| 2025H1 | 103 | 6.5% | 0.213 | 0.038 | +0.032 [**−0.016**, +0.083] |

Both val halves are quiet, and 2025H1 fires: the model's edge over lines × files is no longer clear of 0. **That is decay, and a second real trigger for the demo.** 2025H2 isn't closed until Dec 31, 2026. The evidence is thin: two val halves, and 2025H1 has 103 positives, just over the proposed 100.

## 7. Proposal for sign-off

| # | Rule as written | On val 2023 | Proposal | On val 2023 | Today (2025–26) |
|---|---|---|---|---|---|
| 1 | PSI ≥ 0.2 on a top-5 feature, last 30 days against train | 7/12 false alarms | **PSI ≥ 0.25 on a top-5 feature, last 30 days against the latest labelled year** (rolls forward with each refresh; `[drift] reference = "latest_year"`, `psi_threshold = 0.25`) | 0/12 | 10/21 full months fire |
| 2 | ≥ 50% of features drifted | 11/12 false alarms | **Report only**: in the D2 report and on the dashboard, never a retrain by itself | n/a | n/a |
| 3 | Decay: the newest quarter's paired lower bound < 0 | 2/4 false alarms | **The newest half-year** with a closed window and ≥ 100 positives; retrain when its paired lower bound vs lines × files is < 0 | 0/2 | fires (2025H1) |
| 4 | Decay: PR-AUC more than 0.05 under the last window | 1/3, and confounded by the positive rate | **Drop it.** Rule 3 already compares against lines × files on the same rows, which carries the base rate | n/a | n/a |

What stays: the top-5 features, PSI's 10 decile bins, the 30-day window, and the registry's rule (promote only on a gain above 2 seed-noise SDs). The service rules (5xx, p95, uptime) need the live endpoint and are S6's, now with Rochan.

**Retrain today under the proposal: yes, on both signals.** Drift: PSI against 2024 fires in 10 of 21 full 2025–26 months, from Jun 2025 to Dec 2025 in a row. The last 30 days (454 commits) fire on `la`, 0.347 (`prior_bug_fixes` 0.088). Decay: 2025H1. `--simulate` stays for the "not better, kept the old model" branch.

**Risks the team should weigh:**
- The proposals were picked on the same val months they are scored on. 0/12 at 0.25 against 0.229 at most is a small margin.
- The half-year rule needs ≥ 100 positives. At 2025's 6% rate that is about 1,600 commits, so it may skip a half. If it does, the D2 report says so rather than reading a smaller window.
- A latest-year reference follows slow drift: a change that creeps in over several years never fires. Decay (rule 3) is the backstop for that.

Once signed off: `deploy.toml` [drift] and [decay] (and its pinned test, `tests/test_deploy_config.py`), the latest-year reference in `drift.py`, `[decay] window = "half"` and `min_positives = 100`, PLAN §5, and the tests, in one PR.

# Decay on the newest settled window

Run **Sat Nov 21, 2026**: `python -m monitor.decay`, with the champion `registry/model.joblib`, `deploy.toml` [decay] as written (quarters, ≥ 30 positives), and model.toml's 2,000 bootstrap reps at seed 1. Report: `reports/decay_c5118.json`.

| Window | Commits | Positives | Model PR-AUC | Lines × files | Model − L×F [95%] | Flag |
|---|---|---|---|---|---|---|
| **2025Q3** | 927 | 52 | 0.215 | 0.168 | +0.046 [**−0.015**, +0.099] | **yes**: lower bound < 0 |
| 2025Q2 (previous) | 654 | 47 | 0.217 | | | no drop |

Under the rule as written, decay flags today. Under the proposal (§7: half-years, ≥ 100 positives) it would judge 2025H1 and flag too. The proposal is `[decay] window = "half"` in `deploy.toml`; `decay.py` already reads it.

# The signed thresholds, in deploy.toml

Applied **Wed Nov 25, 2026**: §7's proposal, as signed off, is now `deploy.toml` [drift] and [decay] (D4), PLAN §5, and `tests/test_deploy_config.py`.
- [drift]: `psi = 0.25`, `reference = "latest_year"`, `share_retrains = false`.
- [decay]: `window = "half"`, `min_positives = 100`, and `max_drop` removed.

`python -m monitor.report --out reports/d2_c5118_signed.json` on block `c5118`:

| Signal | Window | Result | Retrain |
|---|---|---|---|
| Drift | 454 commits, Sep 1 – Oct 1 2026, against 2024's 3,762 | PSI on `la` 0.347 ≥ 0.25 (62% drifted: reported only) | yes |
| Decay | 2025H1, 103 positives | model − lines × files [−0.010, +0.079], lower bound < 0 | yes |

`--simulate` still retrains (PSI ≥ 3 on `la`, `nf`, `nd`, `ld`). The decay interval is a little narrower than §6's [−0.016, +0.083] because this run uses model.toml's 2,000 reps at seed 1, against §6's 1,000.
