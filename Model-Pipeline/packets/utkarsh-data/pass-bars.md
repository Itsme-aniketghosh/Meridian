# Utkarsh's pass-bar rows, for Model-Pipeline/PASS-BARS.md (Nov 5)

These are my rows for Chaitali's `PASS-BARS.md`. Each row gives a number and how it's measured.

docs/05-test.md says bars are fixed before the run and changing one needs a commit saying why. So the rows are split by timing:
- **Before the run:** the hand checks and the diff drafter (sections 2 and 5). Neither has run yet.
- **Recorded after the run:** steps B, K and L ran on Nov 4, before the bars were due (sections 1, 3 and 4). Their rows record the checks they were held to, with results, and say so.

## 1. Feature table (step B), recorded after the run

| What | Bar | Measured by | Result (Nov 4) |
|---|---|---|---|
| Determinism | Same block and config: the same parquet md5, 100% | `test_data` `test_same_input_same_md5`; two rebuilds | ✅ `9c0ab22048fff931ac89e27c20112c56` both times |
| No person data | 0 values containing `@`; no name, email, person or author column | `data.check()` refuses the table; `test_no_person_data` | ✅ |
| No future in features | `prior_bug_fixes` never counts a later fix; the label window is inclusive at `window_days` and exclusive after it | `test_prior_bug_fixes_never_sees_the_future`, `test_window_edges` | ✅ |
| Splits | Every split holds only its years; `none` holds only rows after `follow_up_end` | `test_splits_hold_their_years` | ✅ |
| No silent NaN | 0 missing values in any number column | `data.check()` | ✅ |
| Judged slices | Every module slice used in val and test has N ≥ 30, or is pooled | rows per `module` in val and test | ✅ 14 modules ≥ 30; 88 val and 137 test rows fall in smaller slices, to pool |

## 2. Labels: written before the hand checks

| What | Bar | Measured by |
|---|---|---|
| Label precision | At least **16 of 20** random positives (80%) are real. A positive is real when the fix's diff changes lines the inducing commit wrote, for the reason in the fix's ticket | `decisions.md`, 20 positives drawn with seed 1, each read by hand with `git show <fix>` in the snapshot clone |
| Start year (decision 2) | 2015 stays only if **at most 2 of 10** sampled 2015 positives are wrong. Otherwise the start year moves to 2016 and the table is rebuilt | `decisions.md`, 10 from 2015 and 10 from 2022, same check |
| Label window (decision 1) | The rule as written: the shortest window catching ≥ 80% of blames that leaves a full 2024 | the blame-delay CDF over 22,007 SZZ pairs. **No option meets it** (365 days: 54%; 730 days: 72%, and 730 breaks 2024). 365 is picked by the fallback. Needs Aniket's OK by Nov 6 |

## 3. Tracking tables (step K), recorded after the run

| What | Bar | Measured by | Result |
|---|---|---|---|
| Enums | A `suggestion` row whose `source` or `route` is outside the allowed values is refused, 100% | `test_tracking` | ✅ |
| References | An `outcome` for an unknown `suggestion_id`, or a `match` naming a missing policy, is refused, 100% | `test_tracking` | ✅ |
| No future rows | No tracking row known after the block's `snapshot_at` | `test_tracking` | ✅ |
| Empty is valid | A complete block with the tracking tables empty or left out loads and validates with 0 tracking findings | `test_tracking` | ✅ |
| KB queries | KNOWLEDGE-BASE.md's `outcome_*` queries return what the doc says, on schema.sql's tables | `test_kb` | ✅ |

## 4. Second real run (step L), recorded after the run

| What | Bar | Measured by | Result (run `5bc7deda64fcfa0a`) |
|---|---|---|---|
| Django | All 267 site lines are edited by the conversion commit; the only sites left are the `test_legacy_aliases` imports and its 4 new calls | `test_run2` | ✅ 265 removed + 2 rewritten imports; 4 new |
| Packages | Removed lines per repo equal the answer key | `test_run2` | ✅ 1,205 of 1,236. ⚠️ The key before the run was 1,160 (edited lines). The 45 extra are autocomplete-light calls that stop resolving after an aliased import. The key was changed after the run, for that reason |
| `known_at` | Every removed row is known at run 2's snapshot, 100% | `test_run2` | ✅ 1,491 of 1,491 |
| Validate | 0 errors | `meridian.validate --prev-run` | ✅ 0 errors, 8 expected drift warnings |
| Owner numbers | `owner_done` and `owner_eta` both non-NULL on run 2 | KB queries on the block | `owner_done` ✅ 1,491 of 1,530; `owner_eta` ❌ NULL (its 28-day window over 2017–2022 removals). Needs a query change, Aniket's call |

## 5. Diff drafter: written before any draft exists

| What | Bar | Measured by |
|---|---|---|
| Django match (05-test) | **≥ 90%** of Django's 267 site lines get a draft equal to the real after-line at `c651331b34`, whitespace ignored. Drafted from the parent; a line with no draft counts as a miss. N reported | `test_diffs` |
| Packages match | ≥ 90% on the 12 repos whose migration commit finished the job. The other 8 are reported, not judged (their real after-lines are partial) | `test_diffs`, per repo |
| Decision 10 | If the codemod alone clears 90% on Django, the LLM path is cut first (PLAN §9), and that is reported as a result | same test, codemod only |
| Decision 11 | `url()` gets the replacement each migration actually used, per line: `re_path`, `path`, or an aliased import (`re_path as url`, seen in autocomplete-light). Judged by the packages match bar above | same test |
| Only its own line | A draft that changes any other line is refused, 100% | `test_diffs` |
| Resolves | A draft whose new name doesn't resolve to the target symbol is refused, 100% | `test_diffs` |
| No LLM in `except` | A site inside `except` gets no LLM diff, 100% | `test_diffs` |
| LLM off | With the LLM off, the codemod still drafts and the run completes | `test_diffs`; also counts toward the team's "LLM off" bar |
| LLM bias | Diff match rate per repo and per team goes into Chaitali's LLM bias row (no judged slice, N ≥ 30, more than 0.10 below overall without a fix tried) | per-slice match rates from `test_diffs` |
