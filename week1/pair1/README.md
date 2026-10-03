# Pair 1 · week 1

Django framework source (`ugettext` → `gettext`). Snapshot `4353640ea9`, conversion commit `c651331b34`. Method-call benchmark: `is_ajax()` (`7fa0fa45c5`) and `is_authenticated()` / `is_anonymous()` (`c1aec0feda`). Run 2026-10-02.

## Verdict

- **Strong. Go ahead.** The scanner finds all 265 edit lines with 0 false finds, and Jedi and pyright each resolve 533 of 542 aliased `_()` calls (98.3%).
- 03-data's numbers need fixing: 279 is a plain text search, not the edit set (265), and ~480 counts lines, not calls (542).
- Method calls don't resolve: Jedi 5 of 30, pyright 2 of 30, 0 of 12 each in `django/`. Neither is ever wrong, so keep the name matches they can't bind.

## Can we use it? The numbers

| What it tells you | Need at least | We got | |
|---|---|---|---|
| Tree-sitter over all of Django (Q1) | minutes, not hours | **0.78 s**, 2,374 files, 0 parse errors | ✅ |
| Scanner finds the edit lines | recall ≥ 0.95 | **265 of 265**, 0 false finds (265 of 267 counting 2 lines outside `.py`) | ✅ |
| Resolvers on aliased `_()` (Q2) | 95% | **533 of 542**, Jedi and pyright alike | ✅ |
| Hand-check time for the answer key (Q3) | done by week 4 | **9.3 min** for 30, ~82 min for all 265 | ✅ |
| Edit lines that are junk (Q4) | few | **0 of 265** | ✅ |
| Plain text search as the count | equals the edit set | 284 lines: all 265, plus 19 non-uses. 0 of 485 alias-call lines | 🟡 |
| Resolvers on method calls | recall ≥ 0.95 | Jedi **5 of 30**, pyright **2 of 30** | 🔴 |
| Resolver answers that are right | precision ≥ 0.98 | Jedi 5 of 5, pyright 2 of 2 | ✅ |

## Flags

- The migration was in **2017, not 2019**. `c651331b34` was authored 2017-01-26. 2019 is when Django 3.0 deprecated the old names.
- 03-data's 279 (110 imports, 169 calls) is `git grep` minus the 5 definition lines. All four numbers match (see [Grep check](#grep-check)). The commit changed 285 `.py` lines; 265 are uses (113 import, 151 call, 1 alias), the other 20 are renamed tests, rewrapped lines, comments and docstrings.
- Aliases are made two ways: `import ugettext_lazy as _` (533 calls) and `_ = ugettext_lazy` (9 calls, `tests/mail/tests.py:929`). Both resolvers stop at the `=` line for those 9.
- The old names are second names for the new ones (`ugettext = gettext`). A resolver that follows `_` all the way back could report `gettext` and hide the call. Neither did, but scoring must require the old name.
- Python lives outside `.py`: `tests/i18n/commands/code.sample` has 2 lines the commit changed. We keep 265 as the key and report 265 of 267.
- `is_ajax()` has only 2 uses in Django, too few to benchmark. We added `is_authenticated()` / `is_anonymous()`: 28 uses.
- Method uses aren't only calls: `user.is_anonymous = lambda: True` had to change too. A call-only scanner misses it.
- Resolvers give up whenever the object arrives as a function parameter (`(parameter) request: Unknown`). Django had no type hints then; typed code is untested.
- Migrations can't be spotted by file name: 32 of 128 aren't named `0001_name.py`, and `tests/migrations/test_writer.py` is a test.

## Impact on [05-test](../../docs/05-test.md)

| Test | Change |
|---|---|
| Daily pipeline speed | Passed. Under 1 s to parse Django; each resolver adds ~1 s for 542 calls |
| Django answer key | Freeze at 265 edit lines from the `c651331b34` diff, not 279. Alias key: 542 calls on 485 lines. Report 265 of 267 overall |
| Burndown "diff = edit set" | Doesn't hold exactly: 20 of 285 changed lines aren't uses. Count uses, not diff lines |
| Burndown "grep = edit set" | Doesn't hold: grep finds 284, 265 are uses. Check against the commit instead |
| Resolver recall ≥ 0.95, aliases | Passed, 533 of 542. Add one rule: if the answer lands on `x = old_name`, follow `old_name` |
| Resolver recall ≥ 0.95, method calls | Fails, 5 and 2 of 30. Keep unbound name matches as `ast_unresolved` (30 of 30, 0 false) |
| `ast_resolved` precision ≥ 0.98 | Passed: 5 of 5 (Jedi), 2 of 2 (pyright) |
| Resolver choice | Jedi, narrowly. Tied on aliases, 5 vs 2 on method calls, all in tests |
| Candidate goldens | `tests/mail/tests.py:929–940` (assignment alias), old name aliases new name, `code.sample`, `user.is_anonymous = lambda: True`, `__all__` strings, `def test_ungettext_lazy`, continuation line of a parenthesised import (`django/contrib/admin/utils.py:18`) |
| Collect and extract scope | Keep tests, labelled. Skip vendored `django/utils/six.py` and non-code. Spot migrations by `class Migration(migrations.Migration)` |
| Week-4 hand-check freeze | Holds. 0.31 min per site |

## Next

1. Add the one-hop assignment rule and rerun Q2. Expect 542 of 542.
2. Update 03-data to 265 edit lines (+2 outside `.py`) and 542 alias calls. Confirm with Aniket that 279 / 110 / 169 is a text search.
3. For method calls, keep unbound name matches labelled unresolved. Retest on typed code or with django-stubs.
4. Find a method-call deprecation with 100+ uses, so the resolver choice rests on more than 30.
5. Not run: the optional push replay.

---

## Q1 · Speed

Every `.py` file parsed with tree-sitter, 3 runs, median reported ([q1_speed.json](results/q1_speed.json)).

| | |
|---|---|
| Files | 2,374, 0 with parse errors |
| Total / parse only | 0.78 s / 0.60 s (runs: 0.92, 0.78, 0.78) |
| Machine | Apple M4, 16 GB, macOS 26.5.1, Python 3.9.6 |

## The edit set

Tree-sitter finds every identifier named `ugettext`, `ugettext_lazy`, `ugettext_noop`, `ungettext` or `ungettext_lazy`, and sorts it by role. Comments and strings aren't identifiers, so they're skipped. One row per line: [edit_sites.csv](results/edit_sites.csv).

- 265 edit lines in 115 files: 113 import, 151 call, 1 alias. Plus 5 definitions in `django/utils/translation/__init__.py`, rightly not changed.
- Against the commit ([scanner_vs_commit.csv](results/scanner_vs_commit.csv)): 265 found and changed, 0 found but not changed, 20 changed but skipped (all non-uses).

## Q2 · Alias resolution

**Answer key.** Every `import <old name> as X` and `X = <old name>`, then every call `X(...)`: 542 calls in 96 files, on 485 lines. Alias is always `_`. 533 through import, 9 through `=`. Rechecked with Python's scope rules: 542 ok, 0 shadowed.

**Scoring.** Right means the tool names the old function or lands on its definition line. The new name counts as wrong. Jedi: `goto(follow_imports=True)`. pyright: `textDocument/definition` over `pyright-langserver --stdio`.

| | Jedi 0.19.2 | pyright 1.1.414 |
|---|---:|---:|
| Right | 533 of 542 | 533 of 542 |
| Wrong / no answer | 9 / 0 | 9 / 0 |
| `ugettext` / `ugettext_lazy` / `ugettext_noop` | 156/156, 348/357, 29/29 | same |
| Total time | 1.3 s | 0.6 s startup + 0.4 s |

- Both right on 533, neither on the same 9 ([alias_resolution.csv](results/alias_resolution.csv)).
- The 9 are all in `test_lazy_addresses`, lines 930–940. Both land on line 929, `_ = ugettext_lazy`: the right binding, one step short.

## Q3 · Hand check

30 of the 265 edit lines, picked with `random.seed(1)` and saved before checking. Timed one at a time; the checker saw only file and line ([hand_check_30.csv](results/hand_check_30.csv)).

- **30 of 30** real edit lines, and the commit changed all 30.
- 9.3 min total: 0.31 min mean, 13 s median, slowest 83 s. Projected 82 min for 265.
- Caveat: one checker, who knew the lines came from the scanner.

## Q4 · Junk

Every `.py` file gets the first bucket that fits ([junk_buckets.csv](results/junk_buckets.csv)):

| Bucket | Rule | Edit lines | All `.py` files |
|---|---|---:|---:|
| vendored | `vendor/`, or `django/utils/six.py` | 0 | 1 |
| generated | "generated" / "do not edit" in the first 5 lines | 0 | 0 |
| migrations | top-level `class Migration(migrations.Migration)` | 0 | 128 |
| `django/conf/locale/`, `docs/` | path | 0 | 145 |
| `tests/` | path | 78 | 1,424 |
| other | `django/` itself, `setup.py`, `scripts/` | 187 | 676 |

- **Keep tests, labelled.** The commit changed all 78; the old function can't go while tests call it.
- **Skip vendored and non-code.** `six.py` is someone else's package. Only 2,374 of 5,686 tracked files are Python (2,250 are `.po` / `.mo`).
- **Never edit release notes.** Two of them mention the old names. They're history.
- Outside `.py`, 15 files mention the old names: 14 docs and `code.sample`, whose 2 lines the scanner never sees.

## Grep check

`git grep -n -e ugettext -e ungettext -- '*.py'`, every hit labelled with tree-sitter ([grep_hits.csv](results/grep_hits.csv)).

| Label | Lines | Example |
|---|---:|---|
| real use | 265 | `from django.utils.translation import ugettext_lazy as _` |
| definition | 5 | `ugettext = gettext` |
| string | 5 | `'ugettext'` in `__all__`, `'--keyword=ugettext_noop'` |
| longer name | 5 | `def test_ungettext_lazy(self):` |
| docstring / comment | 2 / 2 | `# ungettext calls require a count parameter` |
| **Total** | **284 in 118 files** | |

- **Where 279 comes from.** Drop the 5 definitions: 279 lines in 118 files, 110 containing "import", 169 not. Counting by the word misses 3 imports on continuation lines; the parser counts 113.
- **Aliases are invisible.** 0 of the 485 alias-call lines. Fine for the edit set, useless for blast radius.
- **Re-exports: none.** No file imports an old name from another file.

## Method-call benchmark

Uses found with tree-sitter at each conversion's parent, checked out as git worktrees.

| | `is_ajax` | `is_authenticated` / `is_anonymous` |
|---|---:|---:|
| Snapshot | `5d654e1e71` | `c16b9dd8e0` |
| Uses (calls + replacing assignments) | 2 + 0 | 26 + 2 |
| Uses the commit changed | 2 of 2 | 28 of 28 |
| Jedi right / pyright right | 0 / 0 | 5 / 2 |

- Of the 30: 12 in `django/` (0 right for both), 18 in `tests/`. Wrong answers: 0. The rest is no answer ([method_resolution.csv](results/method_resolution.csv)).
- **Why** ([method_receivers.csv](results/method_receivers.csv)): every right answer came when the tool knew the receiver's type, and every miss when it didn't. A receiver that arrives as a parameter (`request` in a view, `u` in a lambda) is never resolved. Jedi's 3 extra follow assignments in tests, like `self.request.user`.

## Rerun

Needs a full clone of django/django at `4353640ea9` (default `~/MLOps/data/django`; pass another path as the first argument). Run from `week1/pair1/`, in order, with `pyright-langserver` on the path. The method scripts add two worktrees next to the clone.

```
pip install tree-sitter==0.23.2 tree-sitter-python==0.23.6 jedi==0.19.2 pyright==1.1.414
python scripts/q1_speed.py
python scripts/scan_sites.py && python scripts/compare_to_commit.py
python scripts/q2_find_alias_calls.py && python scripts/q2_check_alias_calls.py
python scripts/q2_jedi.py && python scripts/q2_pyright.py      # pyright merges both into alias_resolution.csv
python scripts/q3_pick.py && python scripts/q3_check.py        # q3_check is interactive and timed
python scripts/q4_junk_buckets.py && python scripts/grep_check.py
python scripts/method_sites.py && python scripts/method_resolve.py && python scripts/method_receivers.py
```

Only the files below are in git. The scripts also write intermediate files to `results/` (alias key, per-tool answers, summaries); those stay local.

| File | What |
|---|---|
| `scripts/q1_speed.py` → `results/q1_speed.json` | Q1 timing |
| `scripts/scan_sites.py` → `results/edit_sites.csv` | The 265 edit lines (the answer key) |
| `scripts/compare_to_commit.py` → `results/scanner_vs_commit.csv` | Edit set vs the lines `c651331b34` changed |
| `scripts/q2_*.py` → `results/alias_resolution.csv` | Q2 key, scope check, and both tools' answer per call |
| `scripts/q3_pick.py`, `q3_check.py` → `results/hand_check_30.csv` | Q3 sample and timed answers |
| `scripts/q4_junk_buckets.py` → `results/junk_buckets.csv` | Q4 bucket per edit line |
| `scripts/grep_check.py` → `results/grep_hits.csv` | Every grep hit, labelled |
| `scripts/method_*.py` → `results/method_resolution.csv`, `method_receivers.csv` | Method-call benchmark answers, and what each tool thinks the receiver is |
