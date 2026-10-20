# Scanner · step 1

Rachna's part of the [pipeline plan](PIPELINE-PLAN.md): every place a tracked old name appears in the code. Run 973531f7fd4cfd5e, 2026-10-20.

```
python -m meridian.scan --run-id <run_id> --config meridian.toml      # from DATA PIPELINE/
```

| Reads | Writes |
|---|---|
| `snapshots/<run_id>/` (code), `meridian.toml` (symbols, `[scanner]`) | `out/<run_id>/candidate.jsonl` (Rochan reads it), `out/<run_id>/parse_failure.jsonl` |

## Verdict

| Task | Bar | Got | |
|---|---|---|---|
| D3 · Scanner: rules per symbol from config, roles, junk labels, Python outside `.py`, parse failures counted | goldens pass | **17 of 17** tests | ✅ |
| D4 · Candidates on Django | 265 of 265, 0 false | **265 of 265, 0 false** (vs `week1/pair1/results/edit_sites.csv`), plus 2 of 2 in `code.sample` | ✅ |
| D4 · Candidates on packages → Rochan | ≥ 97% | resolver on these rows: **1,059 of 1,059**, precision 1.000 | ✅ |
| D4 · The 4 replacement refs | pinned | autocomplete-light, dj-rest-auth, silk, tastypie pinned (below) | ✅ |
| D4 · Freeze the Django 265 key | hand-checked, committed | **265 of 265** confirmed in 41.6 min (30 in week 1, 235 in 32.3 min). One first-pass slip, `widgets.py:361`, fixed on review | ✅ |
| D6 · Goldens, every case | all pass | 11 golden tests (cases below) | ✅ |
| Same run, same bytes | 0 diffs | `cmp` identical on a second run | ✅ |

On 973531f7fd4cfd5e: **2,908 candidate rows** (294 Django, 2,614 packages), the same keys as `tests/standin_candidates.py`. 8 parse failures, holding 0 tracked names. Rochan's resolver on these rows reproduces [RESOLVER.md](RESOLVER.md) exactly: 294 of 294, 542 of 542, 1,059 of 1,059, 2,054 call edges.

## How it decides

- **Files:** every `.py`, plus `[scanner] embedded_python` (`code.sample`) as `file_kind = 'embedded'`. `skip_paths` and `.git` are never read.
- **Candidates:** tree-sitter finds every identifier whose text is a tracked old name. Comments and strings are never identifiers, so they never become rows.
- **Role:** `import` (anywhere in an import, including a parenthesised import's continuation lines), `call` (`ugettext(...)`, `translation.ugettext(...)`), `alias` (right side of `=`), `definition` (def/class name, left side of `=`), `attribute` (not called), else `reference`.
- **col** counts characters, like Jedi. **scope** is the dotted enclosing def/class names.
- **Junk** is a label, never a filter: `test` (a `tests/` segment anywhere in the path, or `test_*.py`), `migration` (the marker), `example` (`examples/`).
- **Parse failures:** a file with an ERROR or MISSING node is excluded and becomes a `parse_failure` row. The log says how many tracked names those files held.

## Goldens

`tests/test_scan.py` builds a fixture repo with one file per case and checks the exact rows:

| Case | Expected |
|---|---|
| Direct import and call, attribute call, attribute not called | `import`, `call`, `call`, `attribute` |
| Import alias `ugettext_lazy as _` | the import line only. `_()` calls are the resolver's |
| Assignment alias `T = ugettext_lazy` | `alias` |
| Parenthesised import continuation `    ugettext,` | `import`, col 4 |
| Re-export through `compat.py` | rows in both files |
| `def test_ungettext_lazy`, comments, strings, `__all__` | no rows |
| `getattr(translation, 'ugettext')` | no row: the expected miss in 05-test |
| `user.is_anonymous = lambda: True` | `attribute` |
| `ugettext = gettext` | `definition` |
| Keyword-argument and parameter names | `reference`, counted |
| `code.sample` | `embedded` |
| `app/tests/`, `examples/`, a migration | `test`, `example`, `migration` |
| A broken file | one `parse_failure` row, no candidates |
| `docs/` | skipped |
| `é` before the name | col in characters (13, not 14 bytes) |

## The 4 replacement refs

Each is the parent of the package's `url()` migration commit, found with `week1/pair3/django-packages/scan.py` (largest drop in `url()` uses):

| Package | Migration commit | Date | Sites before → after | `ref` |
|---|---|---|---|---|
| django-autocomplete-light | `1f500ad4` "Django 4.0 test" | 2021-11-23 | 67 → 2 | `1f500ad4^1` |
| dj-rest-auth | `ea14056` "Bumps all-auth + fixes tests" | 2021-12-20 | 51 → 0 | `ea14056^1` |
| django-silk | `8d9b48b` (merge of #445) | 2021-01-29 | 28 → 2 | `8d9b48b^1` |
| django-tastypie | `6d06e29` "Feature/Django 4.0" | 2022-01-03 | 33 → 0 | `6d06e29^1` |

dj-rest-auth and silk had more uses earlier (peaks 61 and 35) than just before their migration. Like the other 16, we pin the migration's parent, so their keys are 51 and 28.

## Flags

- **The run changes.** Pinning the 4 refs gives a new `run_id`, as planned. Also, `5498457` (Owners) already changed `meridian.toml`, so latest main gives `0cfa364396e0f024` for the old inputs. To reproduce 973531f7fd4cfd5e: `git show 5498457^:"DATA PIPELINE/meridian.toml" > /tmp/old.toml` and pass `--config /tmp/old.toml`.
- **Keyword-argument and parameter names:** 315 in the packages (133 in DRF), 0 in Django. They can't be uses of `url()`, but they're kept as `reference` (as in the stand-in) so the resolver keeps matching. One switch, `KEEP_NAME_ONLY` in `scan.py`, if we agree to drop them.
- **`*.sample` is broader than Python.** It also catches wagtail's `nginx.conf.sample` and `uwsgi.conf.sample`. They fail to parse cleanly and are counted, so no harm, but the pattern could be narrowed.
- **Parse failures, all expected:** 5 cookiecutter templates (`{{ ... }}` paths), Django's deliberately non-UTF-8 `not_utf8.sample`, and the 2 wagtail `.conf.sample` files. 0 tracked names in any.
- **Junk differs from the stand-in on 82 rows:** `tests/` now matches anywhere in the path, and `example` is labelled.

## Next

1. Rerun on the new `run_id` once Vishwa posts it.
2. Agree with Rochan on keyword-argument and parameter names.

## Rerun

```
pip install -r requirements.txt                                                  # Python 3.11+
python -m meridian.scan --run-id <run_id> --config meridian.toml
python -m meridian.resolve --run-id <run_id> --config meridian.toml              # Rochan's part, on these rows
python -m tests.score_packages --run-id <run_id> --config meridian.toml
MERIDIAN_RUN_ID=<run_id> python -m unittest tests.test_scan -v                   # 17 tests, Django answer key included
python -m tests.hand_check_edit_sites --run-id <run_id> --config meridian.toml --initials XX   # interactive, resumable
```

| File | What |
|---|---|
| `meridian/scan.py` | The part |
| `tests/test_scan.py` | Goldens, the one command, Django answer key |
| `week1/pair3/django-packages/scan.py` | Finds each package's migration commit |
| `tests/hand_check_edit_sites.py`, `labels/django_edit_sites_265.csv` | Hand check and the frozen key (265 of 265) |
