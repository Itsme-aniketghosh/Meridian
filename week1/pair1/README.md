# Pair 1 · week 1

Django framework source (`ugettext` → `gettext`). Snapshot `4353640ea9`, conversion commit `c651331b34`. Run 2026-10-02.
Method-call benchmark: `is_ajax()` at `5d654e1e71` (conversion `7fa0fa45c5`) and `is_authenticated()` / `is_anonymous()` at `c16b9dd8e0` (conversion `c1aec0feda`).

## Verdict

- Tree-sitter is fast: 0.78 s for all 2,374 `.py` files, 0 parse errors.
- The edit set is 265 lines (113 import, 151 call, 1 alias) in 115 files. Our scanner finds 265 of 265 with 0 false finds, checked against the conversion commit.
- Aliased `_()` resolves: **yes**. Jedi and pyright each get 533 of 542 alias calls right (98.3%, bar 95%). They miss the same 9.
- Hand-checking is quick: 30 of 30 sampled lines confirmed in 9.3 min, so all 279 would take about 1.5 hours.
- Almost no junk: 187 of the 265 edit lines are in `django/`, 78 in `tests/`, and 0 in docs, translations, migrations, generated, or vendored code. Tests are real work: the commit changed all 78.
- A plain text search finds all 265 edit lines, plus 19 that aren't uses, and none of the 542 alias calls. 03-data's 279 is this search minus the 5 definition lines.
- Method calls don't resolve: Jedi gets 5 of 30, pyright 2 of 30, and each gets 0 of 12 in Django's own code. Neither is ever wrong. Jedi wins the tie, narrowly.

## The numbers

| What | Need | We got | |
|---|---|---|---|
| Tree-sitter scan of all of Django (Q1) | minutes, not hours | 0.78 s, 2,374 files, 0 parse errors | ✅ |
| Scanner finds the edit lines | recall ≥ 0.95 | 265 of 265 `.py` lines, 0 false finds. 265 of 267 counting 2 lines outside `.py` | ✅ |
| Resolvers on aliased `_()` calls (Q2) | ≥ 95% | 533 of 542, Jedi and pyright alike | ✅ |
| Hand-check time for the answer key (Q3) | done by week 4 | 9.3 min for 30, about 82 min for 265 | ✅ |
| Edit lines that are junk (Q4) | few | 0 of 265 | ✅ |
| Plain text search as the count | equals the edit set | 284 lines: all 265, plus 19 non-uses. 0 of 485 alias-call lines | 🟡 |
| Resolvers on method calls | recall ≥ 0.95 | Jedi 5 of 30, pyright 2 of 30. 0 of 12 in `django/` | 🔴 |
| Resolver answers that are right | precision ≥ 0.98 | Jedi 5 of 5, pyright 2 of 2 | ✅ |

## Flags

- The migration was in **2017, not 2019**. `c651331b34` was authored 2017-01-26 and committed 2017-02-07, on top of `4353640ea9` (2017-02-03). 2019 is when Django 3.0 formally deprecated the old names.
- 03-data's 279 lines (110 imports, 169 calls) doesn't match the commit. The commit changed 285 `.py` lines in 117 files. 265 are code uses (113 import, 151 call, 1 alias) in 115 files. The other 20 aren't uses: 11 renamed tests/helpers with "ugettext" in their name, 5 rewrapped lines, 2 comments, 2 docstrings. Asked Aniket how 279 was counted. Likely answer: a plain text search. It finds 284 `.py` lines, and without the 5 definition lines that's 279 lines in 118 files, 110 with the word "import" and 169 without. All four match 03-data (see [Grep check](#grep-check)).
- 03-data's ~480 alias calls is a line count. There are 542 alias calls on 485 lines in 96 files. Q2 scores per call, N = 542.
- Aliases are made two ways: `import ugettext_lazy as _` (533 calls) and `_ = ugettext_lazy` (9 calls, `tests/mail/tests.py:929`). Both resolvers stop at the `=` line for those 9. Neither follows assignments.
- The old names are second names for the new ones (`ugettext = gettext`, `gettext_lazy = ugettext_lazy = lazy(...)` in `django/utils/translation/__init__.py`). A resolver that follows `_` all the way back could report `gettext_lazy` and hide the call from the migration. Neither tool did (0 of 542), but scoring must require the old name.
- The Q2 answer key comes from our own tree-sitter scripts. Independent check: 533 rows confirmed by both resolvers, the other 9 by reading line 929.
- Python code lives outside `.py` too. `tests/i18n/commands/code.sample` holds 2 lines the commit changed (an import and a call). A `.py`-only scanner never opens it. We keep 265 as the key and report 265 of 267 overall.
- `is_ajax()` is too small to benchmark. Django's own code calls it twice. We added `is_authenticated()` / `is_anonymous()`, which Django turned into properties in `c1aec0feda`: 28 uses.
- Method uses aren't only calls. Two tests replace the method on one object (`user.is_anonymous = lambda: True`), and the commit had to change them, because a property can't be replaced that way. A call-only scanner misses them.
- Both resolvers give up whenever the object arrives as a function parameter. pyright says so: `(parameter) request: Unknown`. Django's code had no type hints then; typed code may do better (untested).
- Migration files can't be spotted by name. 32 of 128 aren't named like `0001_name.py`, and `tests/migrations/test_writer.py` is a test, not a migration.

## Impact on [05-test](../../docs/05-test.md)

| Test | Change |
|---|---|
| Daily pipeline speed | Passed. Parsing is under 1 s for all of Django. Each resolver adds about 1 s for all 542 calls |
| Django answer key | Freeze at 265 edit lines (113 import, 151 call, 1 alias) from the `c651331b34` diff, not 279. Alias key: 542 calls on 485 lines. Plus 2 lines outside `.py` (`tests/i18n/commands/code.sample`): report 265 of 267 overall |
| Burndown "diff = edit set" | Doesn't hold exactly. 20 of 285 changed lines aren't uses. Count code uses, not diff lines |
| Burndown check "grep = edit set" | Doesn't hold. Grep finds 284 lines and 265 are uses. The other 19: 5 definitions, 5 strings, 5 test names, 2 docstrings, 2 comments. Check against the commit's changed lines instead |
| Resolver recall ≥ 0.95 | Passed: 533 of 542 (0.983) for both Jedi and pyright. Add one rule: if the answer lands on `x = old_name`, follow `old_name`. That fixes the 9 |
| Resolver recall ≥ 0.95, method calls | Fails: Jedi 5 of 30, pyright 2 of 30, and 0 of 12 each in `django/`. Keep unbound name matches as `ast_unresolved` (here 30 of 30, 0 false) instead of dropping them |
| `ast_resolved` precision ≥ 0.98 | Passed on method calls: 5 of 5 for Jedi, 2 of 2 for pyright |
| Resolver choice | Jedi, narrowly. Tied on aliases (533 of 542 each). On method calls 5 vs 2 of 30, and all 3 extra are in tests. Recheck on a bigger benchmark |
| Week-1 method-call benchmark | `is_ajax()` has 2 uses in Django, too few. Use `is_authenticated()` / `is_anonymous()` (`c1aec0feda`, 28 uses), or find a larger one |
| Candidate goldens | Add `tests/mail/tests.py:929–940` (assignment alias, 9 calls) and the "old name aliases the new name" trap. Also: `code.sample` (Python outside `.py`), `user.is_anonymous = lambda: True` (assignment that replaces a method), `__all__` and `--keyword=ugettext_noop` strings, `def test_ungettext_lazy` (longer name), and an old name on the continuation line of a parenthesised import (`django/contrib/admin/utils.py:18`) |
| Collect and extract scope | Keep tests as call sites, labelled. Skip vendored code (`django/utils/six.py`) and non-code (docs, 2,250 `.po` / `.mo`). Spot migrations by `class Migration(migrations.Migration)`, not by file name |
| Week-4 hand-check freeze | Holds. 0.31 min per site, about 87 min for 279 (82 for the real 265) |

## Next

1. Add the one-hop assignment rule and rerun Q2. Expect 542 of 542.
2. ~~Run both resolvers on the `is_ajax()` sites to break the tie.~~ Done: [Method-call benchmark](#method-call-benchmark). Jedi, narrowly.
3. Confirm with Aniket that 279 / 110 / 169 is the plain-text-search count (our rebuild matches all four numbers) and ask how ~480 was counted. Update 03-data to 265 edit lines (+2 outside `.py`) and 542 alias calls.
4. Rename `sites_279.csv` to `edit_sites.csv` (Q4 is done).
5. For method calls, keep name matches the resolver can't bind, labelled unresolved. Then retest on typed code or with django-stubs, where pyright may do better.
6. Find a method-call deprecation with 100+ uses, so the resolver choice rests on more than 30.
7. Not run: the optional push replay.

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

## Q4 · Junk

`q4_junk_buckets.py` gives every `.py` file one bucket, the first that fits:

| Bucket | Rule |
|---|---|
| vendored | a `vendor/` folder, or `django/utils/six.py` (a copy of the outside `six` package) |
| generated | the first 5 lines say "generated" or "do not edit" |
| migrations | defines `class Migration(migrations.Migration)` at top level, which is how Django itself spots one |
| `django/conf/locale/`, `docs/`, `tests/` | by path |
| other | everything else: `django/` itself, `setup.py`, `scripts/` |

| Bucket | Edit lines | Files with edit lines | All `.py` files |
|---|---:|---:|---:|
| vendored | 0 | 0 | 1 |
| generated | 0 | 0 | 0 |
| migrations | 0 | 0 | 128 (110 in `tests/`) |
| `django/conf/locale/` | 0 | 0 | 141 |
| `docs/` | 0 | 0 | 4 |
| `tests/` | 78 | 31 | 1,424 |
| other | 187 | 84 | 676 |
| **Total** | **265** | **115** | **2,374** |

**What to skip, and why.**
- **Keep tests, labelled.** They're 78 of the 265 edit lines, and `c651331b34` changed all 78. The old function can't be deleted while tests still call it.
- **Skip vendored code.** `django/utils/six.py` is someone else's package, updated by copying in a new version, so git history would give it a false owner. Files adapted from other projects (e.g. `django/utils/autoreload.py`, "borrowed from CherryPy") are maintained in Django, so they stay.
- **Skip non-code.** Only 2,374 of 5,686 tracked files are Python. The rest include 1,130 `.po` and 1,120 `.mo` translation files and 424 `.txt` files.
- **Never edit release notes.** `docs/releases/1.0-porting-guide.txt` and `1.4.txt` mention the old names on 3 lines. They're history.

**Outside `.py`** (`junk_non_py_mentions.csv`). 15 files mention the old names, on 74 lines.
- 14 are docs (72 lines). The commit changed 11. It left the 2 release notes and `docs/ref/utils.txt`, which documents the old functions themselves.
- 1 is code: `tests/i18n/commands/code.sample`, input for a `makemessages` test. The commit changed both of its lines (`import ugettext`, `ugettext(...)`). The scanner reads only `.py` files, so it never sees them: 265 of 267 overall.

**All 279.** 03-data's 279 is the 265 edit lines plus 14 lines that aren't uses: 2 comments, 2 docstrings, 5 strings, 5 test names, 7 in `django/` and 7 in `tests/`. See [Grep check](#grep-check).

**Migrations can't be spotted by name.** 32 of the 128 migration files aren't named like `0001_name.py` (tests use names like `1_auto.py` and `thefirst.py`). And `tests/migrations/` also holds ordinary tests: `test_writer.py` has 1 edit line, which a path rule would wrongly skip.

## Grep check

`grep_check.py` runs the search a person would, `git grep -n -e ugettext -e ungettext -- '*.py'`, then labels every line it finds with tree-sitter (the first label that fits).

| Label | Lines | Example |
|---|---:|---|
| real use (in the edit set) | 265 | `from django.utils.translation import ugettext_lazy as _` |
| definition | 5 | `ugettext = gettext` |
| string | 5 | `'ugettext', 'ugettext_lazy', 'ugettext_noop',` in `__all__`; `'--keyword=ugettext_noop',` in `makemessages.py` |
| longer name | 5 | `def test_ungettext_lazy(self):` |
| docstring | 2 | `Test plurals with ungettext. French differs from English ...` |
| comment | 2 | `# ungettext calls require a count parameter ...` |
| **Total** | **284 in 118 files** | |

- It finds 265 of 265 edit lines. 19 of its 284 lines aren't uses.
- The commit changed 9 of the 14 non-uses anyway (2 comments, 2 docstrings, 5 test renames). It left the 5 strings, because the old names still existed.
- **Where 279 comes from.** Drop the 5 definition lines and you get 279 lines in 118 files: 110 with the word "import", 169 without. That's exactly 03-data. Counting imports by the word misses 3 real import lines, which sit on the continuation lines of parenthesised imports (`django/contrib/admin/utils.py:18`, `tests/i18n/tests.py:27`, `:28`). The parser counts 113.
- **Aliases are invisible.** The search finds 0 of the 485 lines that hold the 542 alias calls. That's fine for the edit set, since only the import changes, but useless for blast radius.
- **Re-exports: none.** 112 files bind an old name or a nickname at top level, and no file imports one from another file. 14 `import *` lines pull from such files, but each source's `__all__` leaves the old names out.
- **Verdict.** For this symbol, grep finds the edit set, but it over-counts by 19 and can't see aliases. Count with the parser, and check against the commit, not grep.

## Method-call benchmark

**Why two.** The instructions suggest `HttpRequest.is_ajax()`. Its conversion commit, `7fa0fa45c5` "Removed HttpRequest.is_ajax() usage" (authored 2019-12-15), changed every call in Django's own code, and there are only 2 (`django/views/debug.py:51`, `django/views/i18n.py:36`). Two can't break a tie. So we added `is_authenticated()` / `is_anonymous()`: in `c1aec0feda` (2016-04-02) Django made them properties and rewrote every use, 28 lines in 14 files.

**Finding the uses** (`method_sites.py`). Each snapshot is checked out as a git worktree next to the main clone (`django-is_ajax`, `django-is_auth`). Tree-sitter finds every `<receiver>.<method>` and every `def <method>`.

| | `is_ajax` | `is_authenticated` / `is_anonymous` |
|---|---:|---:|
| Snapshot (the conversion's parent) | `5d654e1e71` | `c16b9dd8e0` |
| Calls | 2 | 26 (11 / 15) |
| Assignments that replace the method | 0 | 2 |
| Uses the commit changed | 2 of 2 | 28 of 28 |
| Changed lines naming a method that we missed | 0 | 0 |
| Definitions | 1 (`django/http/request.py:258`) | 4 (`AbstractBaseUser` and `AnonymousUser`) |

The 2 assignments are `user.is_anonymous = lambda: True` (`tests/auth_tests/test_auth_backends.py:145`) and `user.is_authenticated = lambda: False` (`tests/auth_tests/test_mixins.py:83`). They aren't calls, but they had to change, because a property can't be replaced that way.

**Asking the tools** (`method_resolve.py`). Same calls as Q2. **Right** means an answer lands on a Django definition of that method. The `AbstractBaseUser` and `AnonymousUser` versions both count, since both are the old API.

| | Uses | Jedi right | pyright right |
|---|---:|---:|---:|
| `is_ajax`, calls | 2 | 0 | 0 |
| `is_authenticated` / `is_anonymous`, calls | 26 | 4 | 2 |
| `is_authenticated` / `is_anonymous`, assignments | 2 | 1 | 0 |
| **All** | **30** | **5** | **2** |
| In `django/` | 12 | 0 | 0 |
| In `tests/` | 18 | 5 | 2 |

- Wrong answers: 0 for both. Everything else is no answer: Jedi 25, pyright 28.
- Agreement per use: both right 2, only Jedi 3, only pyright 0, neither 25.
- Each tool takes under 1 s per benchmark.

**Why they fail** (`method_receivers.py`). We asked each tool what the receiver is: Jedi with `infer`, pyright with hover.

| | Knew it was a User, AnonymousUser, or HttpRequest | Didn't know |
|---|---|---|
| Jedi | 5 right, 0 no answer | 0 right, 25 no answer |
| pyright | 2 right, 0 no answer | 0 right, 28 no answer |

- A receiver that arrives as a function parameter is never resolved: `request` in a view, `user_obj` in a backend, `u` in `lambda u: u.is_authenticated()`. pyright says so: `(parameter) request: Unknown`.
- Both tools get receivers built in the same test: `a = AnonymousUser()`, and `user = get_user(request)`.
- Jedi's 3 extra follow assignments in tests: `self.request.user`, set by `AuthenticationMiddleware.process_request` (`tests/auth_tests/test_middleware.py:19`, `:27`), and `user = models.User.objects.create(...)` (`tests/auth_tests/test_mixins.py:83`).
- For the 7 `response.context['user']` receivers, the check asks at the closing bracket, which can't show a type. Their no answers come from `method_resolve.py`: neither tool can tell what's stored under `'user'`.

**Verdict.** Jedi wins the tie-break, 5 vs 2 of 30, all in tests. Neither tool gets near recall 0.95 on method calls, and neither is ever wrong. So the pipeline must keep name matches the resolver can't bind (here 30 of 30, 0 false), labelled unresolved, instead of dropping them. Django's code had no type hints then; typed code or django-stubs may help pyright (untested).

## Rerun

Needs a full clone of django/django checked out at `4353640ea9` (default path `~/MLOps/data/django`; pass another path as the first argument). Run from `week1/pair1/`, with the environment on so `pyright-langserver` is found. The method-call scripts check out two more snapshots as git worktrees next to the clone (`django-is_ajax`, `django-is_auth`); the main clone stays at `4353640ea9`.

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
python scripts/q4_junk_buckets.py      # results/junk_buckets.csv, junk_non_py_mentions.csv, junk_buckets_summary.json
python scripts/grep_check.py           # results/grep_hits.csv, grep_check_summary.json
python scripts/method_sites.py         # worktrees on first run; results/method_sites.csv, method_defs.csv
python scripts/method_resolve.py       # results/method_resolution.csv, method_resolution_summary.json
python scripts/method_receivers.py     # results/method_receivers.csv
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
| `scripts/q4_junk_buckets.py` | Q4 buckets for the edit set and for every `.py` file |
| `results/junk_buckets.csv` | The 265 edit lines with their bucket |
| `results/junk_non_py_mentions.csv` | Non-`.py` files that mention the old names, and whether the commit changed them |
| `results/junk_buckets_summary.json` | Counts per bucket, file types, migration and vendored files |
| `scripts/grep_check.py` | Grep check: the plain search, labelled |
| `results/grep_hits.csv` | Every line the search finds, its label, and whether the commit changed it |
| `results/grep_check_summary.json` | Counts, the 279 rebuild, import counting, the re-export check |
| `scripts/method_sites.py` | Method-call benchmark: uses and definitions at each snapshot |
| `results/method_sites.csv`, `method_defs.csv` | The 30 uses and 5 definitions |
| `scripts/method_resolve.py` | Ask and score Jedi and pyright on every use |
| `results/method_resolution.csv`, `method_resolution_summary.json` | Both tools' answers, counts, agreement, and timing |
| `scripts/method_receivers.py` | What each tool thinks each receiver is |
| `results/method_receivers.csv` | Per use: each tool's result and its idea of the receiver |
