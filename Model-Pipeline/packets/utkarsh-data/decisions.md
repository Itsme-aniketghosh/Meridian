# Utkarsh: decisions

Each decision lists the test and the rule that picks an option, followed by the result with its N and the pick. Every number comes from block `c5118b019d642a9b` (snapshot 2026-10-01T01:23:03Z), Spark first-parent commits. The model's population is the 41,163 commits authored in 2015 or later; none of them are merges.

| # | Decision | Pick | Status |
|---|---|---|---|
| 1 | Label window | 365 days | **The rule can't be met as written.** Needs Aniket's OK |
| 2 | Start year | 2015, for now | Waiting on the hand check below |
| 3 | Author identity | merged person | settled |
| 4 | `[MINOR]` commits | keep, with `is_minor` | settled |
| 5 | Module for slices | second level inside `sql/` | settled |
| 6 | Time column | `authored_at` | settled |
| 7 | Binary files | keep, counted as 0 lines | settled |
| 8 | Feature table in git | full table | settled |
| 9 | Second run's scope | | step L, Nov 8 |
| 10 | Codemod or LLM for diffs | | diff drafter, Nov 9–10 |
| 11 | `url()` replacement | | diff drafter, Nov 9–10 |

## 1. Label window

**Test.** Measure the CDF of days from each inducing commit to the fix that blames it, over all SZZ pairs. Then measure the positive share per year under each window.
**Rule.** Pick the shortest window that catches 80% or more of blames and still leaves a full 2024.

**Result.** All 22,007 distinct (fix, inducing) pairs:

| days | 30 | 90 | 180 | 365 | 730 | 1,095 | 1,825 |
|---|---|---|---|---|---|---|---|
| share of blames caught | 12% | 25% | 37% | 54% | 72% | 82% | 93% |

The median delay is 312 days and the 80th percentile is 988. Censoring doesn't explain this. On inducing commits from 2015–2018, which all have 8+ years of follow-up (N = 11,808), the shares are 43% at 180 days, 59% at 365 and 73% at 730.

A full 2024 needs 2024-12-31 + window ≤ the snapshot (2026-10-01), so the window can be at most 638 days. **No listed option meets both halves of the rule.** 730 days leaves 2024 incomplete, and 180 or 365 catch under 80%.

Positive share by year:

| year | N | 180 d | 365 d | 730 d |
|---|---|---|---|---|
| 2015 | 5,030 | 26.0% | 30.8% | 34.2% |
| 2016 | 4,345 | 22.2% | 27.7% | 31.0% |
| 2017 | 2,527 | 16.0% | 19.8% | 24.1% |
| 2018 | 2,356 | 11.9% | 15.5% | 20.4% |
| 2019 | 2,623 | 10.8% | 15.2% | 20.2% |
| 2020 | 2,862 | 10.9% | 14.5% | 17.7% |
| 2021 | 3,085 | 7.5% | 10.2% | 12.9% |
| 2022 | 3,222 | 5.6% | 9.0% | 11.8% |
| 2023 | 4,113 | 8.3% | 11.8% | 14.5% |
| 2024 | 3,762 | 6.0% | 7.5% | 9.9% |

The slide by year appears under every window. It follows the number of fix commits per year (1,309 with blame in 2015, 340 in 2022), not the window. A shorter window doesn't flatten it.

**Pick: 365**, the longest listed option that leaves a full 2024. It catches 59% of blames on fully observed years, against 43% at 180. That gives 283 positives in 2024 (7.5%) and 486 in 2023. Since the written rule can't be met, the packet's fallback applies ("pick the simpler option and say so"), and Aniket should confirm. The largest window that would still be allowed is 638 days, which catches 71%, but it isn't one of the listed options.

## 2. Start year

**Test.** Count fix commits per year, compare the positive share in 2015 with 2016–2018, and hand-check 10 positives from 2015 and 10 from 2022.
**Rule.** If 3 or more of the 2015 positives are wrong (the fix isn't about the blamed lines) and 2022's are mostly right, start at 2016.

**Result so far.** Fix commits with blame by year: 1,309 in 2015, 1,139 in 2016, 751 in 2017, 593 in 2018. The positive share at 365 days is 30.8% in 2015 against 21.3% averaged over 2016–2018.

SZZ only counts fixes from 2015 on. As a result, 2015 commits also lose `prior_bug_fixes` history: it starts at 0 on 2015-01-01.

Hand check (seed 1). To check each pair, run `git show <fix>` in `snapshots/c5118b019d642a9b/repos/apache__spark` and ask whether the fix changes lines the inducing commit wrote, for the reason in the ticket. Mark each row ✓ or ✗.

| inducing | year | fix | ticket | days | ok? |
|---|---|---|---|---|---|
| 9d117cee0b | 2015 | e6c3f7462b | SPARK-8650 | 77 | |
| 5d45e1f600 | 2015 | ab7e721cfe | SPARK-9826 | 106 | |
| 1fd31ba089 | 2015 | 18c2c92580 | SPARK-14861 | 356 | |
| 431ca39be5 | 2015 | 0171b71e95 | SPARK-12421 | 164 | |
| 1b0317f64c | 2015 | 0419d63169 | SPARK-14791 | 261 | |
| dfe347d2ca | 2015 | 75ee12f09c | SPARK-8658 | 97 | |
| afe9f03fd9 | 2015 | f5d154bc73 | SPARK-10966 | 47 | |
| 1d97332715 | 2015 | 877dc712e6 | SPARK-14138 | 163 | |
| 4e38defae1 | 2015 | 3e7e05f5ee | SPARK-12160 | 45 | |
| 9df24624af | 2015 | 1ed354a536 | SPARK-12939 | 66 | |
| bbfd058b55 | 2022 | 91407958b4 | SPARK-40798 | 249 | |
| 175e429cca | 2022 | 488f3923c7 | SPARK-38404 | 1 | |
| 8894e785ed | 2022 | 19afe1341d | SPARK-39412 | 7 | |
| 4e4eb6f1ed | 2022 | 41c26b61e6 | SPARK-42616 | 265 | |
| 9ebd7fb731 | 2022 | d62c18b749 | SPARK-21138 | 144 | |
| 3a1136aa05 | 2022 | d718867a16 | SPARK-39184 | 6 | |
| 96b5d50f3e | 2022 | 87a235c214 | SPARK-41733 | 65 | |
| b38a115887 | 2022 | 0df4c01b7c | SPARK-43502 | 174 | |
| 5fc482eb59 | 2022 | c5c4ec7d9c | SPARK-44370 | 223 | |
| 36f9e392e2 | 2022 | c5c4ec7d9c | SPARK-44370 | 201 | |

**Pick: 2015 for now** (`start_year` in model.toml). It changes to 2016 if the hand check fails.

## 3. Author identity for `exp`, `sexp`

**Test.** Count train commits whose author has 2+ emails, and compare the `exp` distribution under each identity.
**Rule.** Use merged persons if over 10% of train commits come from multi-email authors.

**Result.** 17,146 of 26,050 train commits (65.8%) come from an author with 2+ emails. Median `exp` is 49 with raw emails and 67 with merged persons, and the 90th percentile is 347 against 473. Raw emails would put 2,357 train commits in the `0` (newcomer) bucket, against 2,086 with merged persons, which mislabels about 11% of "newcomers". Every author email maps to a person, and 17 are generic.
**Pick: merged person.** `ndev` uses persons too.

## 4. `[MINOR]` commits

**Test.** Measure their share among positives and among negatives.
**Rule.** Drop them only if they never turn out to be positive.

**Result.** `[MINOR]` commits are 3.5% of train (N = 906, exact case). They make up 0.7% of positives and 4.1% of negatives, and 3.9% of them are positive. Some are, so they carry real risk, and they're a strong low-risk signal.
**Pick: keep, with `is_minor`.** The match ignores case, which adds `[minor]` and `[Minor]`.

## 5. Module for slices

**Test.** Count rows per module in `val` and `test`.
**Rule.** If one module holds over 40% of rows, go one level down. Slices under 30 rows can't be judged.

**Result.**
- **Top level:** `sql` holds 31.7% of val (1,302 of 4,113) and **41.8% of test** (1,571 of 3,762), which is over the bar.
- **Second level inside `sql/`:** the largest slice is `python` in val (27.8%) and `sql/core` in test (25.7%). 14 modules have 30 or more rows. The rows left in modules under 30 are 88 in val and 137 in test.

**Pick: second level inside `sql/`** (`sql/core`, `sql/catalyst`, `sql/hive`, …). Everywhere else, `module` stays the top-level directory. Chaitali should pool the slices under 30 rows or leave them out.

## 6. Time column

**Test.** Measure the gap between author time and commit (merge) time (median, p90), and count how many commits cross a split boundary because of it. Times come from `git log --first-parent` in the snapshot's Spark clone at `f868de69`.
**Rule.** If any commit crosses a split boundary, use merge time.

**Result.** The gap is 0 days at the median, p90 and p99 across all 41,163 commits, and 0 commits cross a split. Spark's merge script squashes each PR into one commit, so author time is merge time.
**Pick: `authored_at`.**

## 7. Binary files

**Test.** Count the commits that touch a binary file, and check whether they get blamed.
**Rule.** Keep them unless they cause NaNs or behave unlike other commits.

**Result.** 6,387 binary file rows fall in 373 commits (0.9%). 69 of those commits are positive (18.5%, close to the train rate), and 10 commits change only binary files.
**Pick: keep.** A binary file counts as a file (`nf`, `nd`, `ns`) with 0 lines (`la`, `ld`, entropy), and no NaN can reach the table: `check()` refuses one.

107 commits have no `commit_file` rows (empty commits). They're kept with 0 change features and module `(none)`.

## 8. Feature table in git

**Test.** Check the file size.
**Rule.** Under 10 MB, commit the full table.

**Result.** 2.16 MB with zstd: 41,163 rows and 25 columns.
**Pick: full table.** `.gitignore` now makes an exception for `Model-Pipeline/data/features.parquet`.

## Label numbers (365 days)

| split | N | positives | share |
|---|---|---|---|
| train (2015–2022) | 26,050 | 5,038 | 19.3% |
| val (2023) | 4,113 | 486 | 11.8% |
| test (2024) | 3,762 | 283 | 7.5% |
| none (2025 on) | 7,238 | 424 | (follow-up incomplete) |

The per-year counts are in `data/features.schema.json` under `labels.by_year`.

**20 random positives (seed 1, val/test/train), hand check.** Same check as decision 2:

| inducing | year | fix | ticket | days | ok? |
|---|---|---|---|---|---|
| 117121a4ec | 2015 | 7ff16e8abe | SPARK-7567 | 84 | |
| 1e43851d64 | 2015 | 8a94eb23d5 | SPARK-9069 | 98 | |
| bfcd528d6f | 2015 | 06b6a074fb | SPARK-9437 | 88 | |
| d5f18de165 | 2015 | c12dff9b82 | SPARK-7652 | 5 | |
| c043a3e9df | 2015 | 72c1d68b4a | SPARK-10181 | 121 | |
| 5743c6476d | 2016 | 4435de1bd3 | SPARK-16179 | 81 | |
| 214adb14b8 | 2016 | 0917c8ee07 | SPARK-18888 | 183 | |
| 7d19b6ab7d | 2016 | 0ef1421a64 | SPARK-19284 | 25 | |
| f3ed62a381 | 2017 | 0e2c266de7 | SPARK-22977 | 265 | |
| 14bdb25fd7 | 2017 | d9f286d261 | SPARK-22326 | 66 | |
| 4855d5c4b9 | 2018 | c08021cd87 | SPARK-26776 | 171 | |
| 6207360b00 | 2019 | d2f21b0199 | SPARK-27468 | 216 | |
| cb06209fc9 | 2019 | e3a88a9767 | SPARK-32516 | 363 | |
| 1ff93ae93d | 2023 | 5291c6c927 | SPARK-44980 | 179 | |
| 6c74e35571 | 2023 | cc249781da | SPARK-43742 | 22 | |
| 5d69388c82 | 2024 | 090ad9f3f4 | SPARK-48961 | 147 | |
| b299b2bc06 | 2024 | 94a31bd9c1 | SPARK-48809 | 281 | |
| e7e082663b | 2024 | 8e1d317307 | SPARK-49615 | 59 | |
| d3170028da | 2024 | 3fab712f69 | SPARK-50441 | 14 | |
| 92948e7371 | 2024 | 1d8c11120f | SPARK-54652 | 357 | |

**v1 vs c5118 comparison.** This isn't done because the v1 block isn't on this machine. The plan built from v1 only because it assumed c5118 wouldn't be available on day 1. Here it was available, so the table went straight to c5118.

## Known limits

- `fix`, `ticket_type`, `component` and the fix rule behind `prior_bug_fixes` read ticket fields as of the snapshot (Data-Pipeline's rule). A ticket re-typed after the commit would leak slightly. It's the same for every split.
- `prior_bug_fixes` counts no fixes before 2015 (SZZ's `fix_since`).
