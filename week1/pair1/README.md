# Pair 1 · week 1

Django framework source (`ugettext` → `gettext`). Snapshot `4353640ea9`, conversion commit `c651331b34`. Run 2026-10-02.

## Verdict

- Tree-sitter is fast: 0.78 s for all 2,374 `.py` files, 0 parse errors.
- The edit set is 265 lines (113 import, 151 call, 1 alias) in 115 files. Our scanner finds 265 of 265 with 0 false finds, checked against the conversion commit.
- Aliased `_()` resolves: **yes**. Jedi and pyright each get 533 of 542 alias calls right (98.3%, bar 95%). They miss the same 9.
- Hand-checking is quick: 30 of 30 sampled lines confirmed in 9.3 min, so all 279 would take about 1.5 hours.

## Flags

- The migration was in **2017, not 2019**. `c651331b34` was authored 2017-01-26 and committed 2017-02-07, on top of `4353640ea9` (2017-02-03). 2019 is when Django 3.0 formally deprecated the old names.
- 03-data's 279 lines (110 imports, 169 calls) doesn't match the commit. The commit changed 285 `.py` lines in 117 files. 265 are code uses (113 import, 151 call, 1 alias) in 115 files. The other 20 aren't uses: 11 renamed tests/helpers with "ugettext" in their name, 5 rewrapped lines, 2 comments, 2 docstrings. Asked Aniket how 279 was counted.
- 03-data's ~480 alias calls is a line count. There are 542 alias calls on 485 lines in 96 files. Q2 scores per call, N = 542.
- Aliases are made two ways: `import ugettext_lazy as _` (533 calls) and `_ = ugettext_lazy` (9 calls, `tests/mail/tests.py:929`). Both resolvers stop at the `=` line for those 9. Neither follows assignments.
- The old names are second names for the new ones (`ugettext = gettext`, `gettext_lazy = ugettext_lazy = lazy(...)` in `django/utils/translation/__init__.py`). A resolver that follows `_` all the way back could report `gettext_lazy` and hide the call from the migration. Neither tool did (0 of 542), but scoring must require the old name.
- The Q2 answer key comes from our own tree-sitter scripts. Independent check: 533 rows confirmed by both resolvers, the other 9 by reading line 929.

## Impact on [05-test](../../docs/05-test.md)

| Test | Change |
|---|---|
| Daily pipeline speed | Passed. Parsing is under 1 s for all of Django. Each resolver adds about 1 s for all 542 calls |
| Django answer key | Freeze at 265 edit lines (113 import, 151 call, 1 alias) from the `c651331b34` diff, not 279. Alias key: 542 calls on 485 lines |
| Burndown "diff = edit set" | Doesn't hold exactly. 20 of 285 changed lines aren't uses. Count code uses, not diff lines |
| Resolver recall ≥ 0.95 | Passed: 533 of 542 (0.983) for both Jedi and pyright. Add one rule: if the answer lands on `x = old_name`, follow `old_name`. That fixes the 9 |
| Resolver choice | Tie on Django aliases. Decide on the method-call benchmark (`is_ajax()`), where type inference matters |
| Candidate goldens | Add `tests/mail/tests.py:929–940` (assignment alias, 9 calls) and the "old name aliases the new name" trap |
| Week-4 hand-check freeze | Holds. 0.31 min per site, about 87 min for 279 (82 for the real 265) |

## Next

1. Add the one-hop assignment rule and rerun Q2. Expect 542 of 542.
2. Run both resolvers on the `is_ajax()` sites to break the tie.
3. Ask Aniket how 279 / 110 / 169 and ~480 were counted, and update 03-data to 265 edit lines and 542 alias calls.
4. Rename `sites_279.csv` to `edit_sites.csv` once Q4 is done.

---

## Q1 · Speed

Every `.py` file parsed with tree-sitter, 3 runs, median reported.

| | |
|---|---|
| Files | 2,374 |
| Files with parse errors | 0 of 2,374 |
| Total, median of 3 | 0.78 s (runs: 0.92, 0.78, 0.78) |
| Parse only, median of 3 | 0.60 s |
| Machine | Apple M4, 16 GB, macOS 26.5.1, Python 3.9.6 |

Run 1 is slower because files aren't cached yet. The gap between total and parse time is finding and reading files.

## The edit set (scanner)

Tree-sitter finds every identifier named `ugettext`, `ugettext_lazy`, `ugettext_noop`, `ungettext`, or `ungettext_lazy`, and sorts it by role: **import**, **call** (`ugettext(...)` or `translation.ugettext(...)`), **alias** (right side of `=`), or **other**. Comments and strings are skipped because they aren't identifiers. One row per line.

| | Lines |
|---|---:|
| Edit set (import + call + alias) | 265 in 115 files |
| Import | 113 |
| Call | 151 |
| Alias | 1 |
| Other (definitions in `django/utils/translation/__init__.py`, rightly not changed) | 5 |

**Checked against the commit** (`compare_to_commit.py`). The commit removed or changed 285 `.py` lines in 117 files.

| Status | Lines |
|---|---:|
| Scanner found it, commit changed it | 265 |
| Scanner found it, commit didn't change it | 0 |
| Commit changed it, scanner skipped it | 20 (all non-uses, see Flags) |

## Q2 · Alias resolution

**Answer key.** `q2_find_alias_calls.py` finds, per file, every `import <old name> as X` and `X = <old name>`, then every call `X(...)`.

| | |
|---|---|
| Alias calls | 542 in 96 files, on 485 lines (46 in `tests/`) |
| Alias names | `_` only |
| Through import / through `=` | 533 / 9 |
| By function | `ugettext_lazy` 357, `ugettext` 156, `ugettext_noop` 29 |

`q2_check_alias_calls.py` redoes the matching with Python's scope rules: nearest enclosing function first, out to the module. 542 ok, 0 shadowed, 0 unbound, 0 mixed.

**Scoring.**
- **Right:** the tool names the old function, or lands on the line that defines it in `django/utils/translation/__init__.py` (lines from `sites_other.csv`).
- **Wrong:** anything else, including the new name.
- **No answer:** nothing returned, an error, or a timeout.

**How each tool was asked.**
- Jedi: `jedi.Script(...).goto(line, col, follow_imports=True)`, one `Script` per file.
- pyright: `pyright-langserver --stdio`, `textDocument/definition`, one `didOpen` per file.

| | Jedi 0.19.2 | pyright 1.1.414 |
|---|---:|---:|
| Right | 533 of 542 | 533 of 542 |
| Wrong | 9 | 9 |
| No answer | 0 | 0 |
| `ugettext` | 156 of 156 | 156 of 156 |
| `ugettext_lazy` | 348 of 357 | 348 of 357 |
| `ugettext_noop` | 29 of 29 | 29 of 29 |
| Total time | 1.3 s | 0.6 s startup + 0.4 s |
| Median / p95 per call | 0.3 / 1.7 ms | 0.1 / 2.1 ms |

**Agreement per call:** both right 533, only Jedi 0, only pyright 0, neither 9.

**The 9:** all in `BaseEmailBackendTests.test_lazy_addresses`, lines 930–940. Both tools land on line 929, `_ = ugettext_lazy`. That's the right binding, one step short of the function.

## Q3 · Hand check

30 of the 265 edit lines, picked with `random.seed(1)` (`q3_pick.py`; list saved before checking). Checked one at a time with `q3_check.py`, which opens each file in VS Code at the line and times each answer. The script shows only the file and line, not the scanner's verdict.

| | |
|---|---|
| Real edit lines | 30 of 30 |
| Total time | 9.3 min |
| Per site | 0.31 min (mean), 13 s (median) |
| Slowest | 82.8 s (`tests/forms_tests/tests/test_i18n.py:12`) |
| Projected for 279 | 87 min |
| Projected for 265 | 82 min |

- Pace improved: about 21 s per site for the first 15, 16 s for the last 15. The projections use the overall average, so they're conservative.
- 2 lines were marked unsure at the time (`tests/messages_tests/base.py:103`, `tests/i18n/tests.py:1514`). Both are among the lines `c651331b34` changed, so both are real.
- All 30 are among the lines the commit changed, so the hand check agrees with the commit 30 of 30.
- Caveats: one checker, who knew the lines came from the scanner list. Notes are free text; use `kind` in `sites_279.csv` for buckets.

## Rochan's sections (Q4, independent count, grep checks, is_ajax, push replay)

TODO

## Rerun

Needs a full clone of django/django checked out at `4353640ea9` (default path `~/MLOps/data/django`; pass another path as the first argument). Run from `week1/pair1/`.

```
pip install tree-sitter==0.23.2 tree-sitter-python==0.23.6 jedi==0.19.2 pyright==1.1.414
python scripts/q1_speed.py             # results/q1_speed.json, q1_parse_errors.txt
python scripts/scan_sites.py           # results/sites_279.csv, sites_other.csv
python scripts/compare_to_commit.py    # results/scanner_vs_commit.csv
python scripts/q2_find_alias_calls.py  # results/alias_calls.csv
python scripts/q2_check_alias_calls.py # results/alias_calls_checked.csv
python scripts/q2_jedi.py              # results/q2_jedi.csv
python scripts/q2_pyright.py           # results/q2_pyright.csv, alias_resolution.csv
python scripts/q3_pick.py              # results/hand_check_30_sample.csv
python scripts/q3_check.py             # interactive; results/hand_check_30.csv, hand_check_30_summary.json
```

| File | What |
|---|---|
| `scripts/q1_speed.py` | Q1 timing |
| `scripts/scan_sites.py` | The edit set |
| `scripts/compare_to_commit.py` | Edit set vs the lines `c651331b34` changed |
| `scripts/q2_find_alias_calls.py` | Q2 answer key |
| `scripts/q2_check_alias_calls.py` | Scope check of the answer key |
| `scripts/q2_jedi.py`, `q2_pyright.py` | Ask and score each resolver |
| `results/sites_279.csv` | The 265 edit lines (name kept for the hand-off; rename later) |
| `results/sites_other.csv` | The 5 definitions |
| `results/scanner_vs_commit.csv` | Line-by-line comparison with the commit |
| `results/alias_calls.csv` | The 542 alias calls with expected function |
| `results/alias_resolution.csv` | Both tools' answer for every alias call |
| `scripts/q3_pick.py`, `q3_check.py` | Q3 sample (seed 1) and timed hand check |
| `results/hand_check_30_sample.csv` | The 30 sampled lines |
| `results/hand_check_30.csv`, `hand_check_30_summary.json` | Answers, notes, seconds per site, and totals |
