# Resolver · step 1

The resolve part decides what each candidate really is, and who calls it. Numbers are from v1, run 70dd1dabfed544b8 (2026-10-23, after the dynamic-config changes). The live team run, c5118b019d642a9b, has the same resolve rows. Where the earlier run 973531f7fd4cfd5e differs, it says so.

```
python -m meridian.resolve --run-id <run_id> --config meridian.toml      # from Data-Pipeline/
```

| Reads | Writes |
|---|---|
| `snapshots/<run_id>/` (code), `out/<run_id>/candidate.jsonl` (from scan) | `out/<run_id>/resolution.jsonl`, `out/<run_id>/call_edge.jsonl` |

## Verdict

| Task | Bar | Got | |
|---|---|---|---|
| D4 · Jedi + one-hop rule (`_ = old_name`) | 542 of 542 | 542 of 542 (533 import alias, 9 assign alias) | ✅ |
| D5 · Resolve every candidate, scored on the old name | precision ≥ 0.98 | Django 294 of 294 · packages 1,236 of 1,236, recall 1,236 of 1,236 (was 1,059 on 16 packages) | ✅ |
| D5 · Hand-check 30 alias calls | 30 of 30, or misses written down | 30 of 30 agree (24.4 min) | ✅ |
| D6 · `call_edge`, resolved only, depth ≤ 3 | edge fixtures pass | 2,288 edges (1,186 Django, 1,102 packages), fixtures exact. Was 2,054: dynamic-config's scope and `__init__` rules, plus 4 packages | ✅ |
| Same run, same bytes | 0 diffs | 0 diffs over repeated runs and any `PYTHONHASHSEED` | ✅ |

Run time: about 74 s, peak 0.8 GB (Django + 20 packages). On 973531f7fd4cfd5e: about 76 s, peak 1.2 GB (16 packages).

## How it decides

- Candidates: Jedi `goto` (follow imports). `ast_resolved` only if it lands on the candidate's own old name, never the new name it aliases. `dropped` if it lands on something else, `ast_unresolved` if there's no answer (kept). `via = reexport` when the name comes through another module (guardian's `compat.py`: 18 rows).
- Alias calls: `_("Hello")` rows the scanner can't see. One-hop rule: when Jedi stops at `_ = ugettext_lazy`, follow the right side once more.
- Call edges: each confirmed call of a tracked symbol (depth 1), then callers of the functions holding one, up to `[resolver] call_edge_depth`. Module and class bodies get edges but don't propagate. Imports never count. A call to a class counts as a call of its `__init__` when Jedi lands on the class def. `introduced_sha` = the commit that last changed the caller's line (join's blame, `--first-parent -w`, at the run's SHA).
- Scope of an edge's caller: `meridian.scan.scopes`, the same code as the scanner. Only bodies count, so a default value like `def get_text_list(list_, last_word=ugettext_lazy('or'))` gives no caller edge.
- Files: `meridian.scan.files`, `.py` only, case as in git. An unreadable folder halts.
- `receiver_from`: for a call `recv.name(...)` whose `recv` has one binding in its scope, `recv = F(...)` (not a parameter, not bound twice), and Jedi lands `F` on exactly one function def (a class gives NULL): `'repo:path:scope'` of that def, e.g. `c = get_client(); c.verify_token()` → `fix/repo:shared/client.py:get_client`. Otherwise NULL, never a guess.
- Jedi's `sys_path` is explicit: the repo, its snapshot dependencies, the standard library. Nothing installed counts. Every `goto` is guarded: a crash is logged and gives no answer, and the per-repo log line counts failures while finding callers.
- Memory: the parso cache is emptied per repo, and whole-repo walks keep no trees.

## Flags

- Decision: the packages borrow Django from the snapshot, not one env per repo. `[resolver] env = "snapshot"`: the packages resolve `django.conf.urls` against the snapshot's `django/django` checkout. None of them has a lockfile, and their `django>=2.2` installs Django 5 today, which has no `url()` (removed in 4.0). An install also changes with the day, and the snapshot copy doesn't. It works: 1,236 of 1,236. The limit: it's Django from 2017, not each package's exact version. That's fine for `url()`, but a company's private libraries would need real per-repo environments.
- Determinism fixes: a fresh `jedi.Script` per question (one Script per file flipped 1 row in 3,451 between runs). parso never evicts during a run (it evicted by file mtime, and Jedi failed with `KeyError` on 30 names). A submodule is searched for only in its package's folders: through the builtin importer, Windows read `django/contrib/postgres/fields/array.py` and `forms/array.py` as the builtin `array`. No other snapshot file is named like a builtin, and this fix changes no row (2,293 edges before and after).
- Same bytes on every OS: Jedi's folder walk has one order. To guess what a parameter holds, Jedi reads its function's calls in the first file of a project walk that has one, and `os.walk` lists folders in the filesystem's order (NTFS by name, APFS by a hash of the name). So Windows got 2,293 edges and the Mac 2,288: Django's `AdminField.label_tag` landed on `BoundField.label_tag` (plus its 4 callers in `tests/admin_utils/tests.py`), and 2 allauth `dropped` rows got another `resolved_to` (`allauth/account/utils.py:39` and `:395`). Jedi's walk now uses APFS's order (case-insensitive) on every OS, which keeps the Mac's 2,288. On django and allauth, Windows now gives the Mac's rows byte for byte. Searching only the function's own file would also remove the OS difference, but it loses `ChangeList.get_results` → `Paginator.page`, which needs the call in `admin/options.py`.
- Commits miss real uses. 26 confirmed `url()` lines were never changed by their package's migration commit (unfinished migrations: graphene-django, tests, examples). The score counts them by a text rule, independent of Jedi.
- Look-alikes rejected: `django.template.defaulttags.url` (another Django `url`), `from .url import ...` (a module).
- `blocked_by` may need depth 5. A → F → up to 3 calls → B is 5 levels from the symbol; depth 3 covers blast radius. `call_edge_depth` is a setting (3 today), not final; anything but a whole number of at least 1 halts (0 used to act as 1, and `"3"` crashed). No feature needs forward edges yet.
- Red-team probes (`RealWorldCode` tests): Python 2 syntax, non-UTF-8 bytes, CRLF, the packages' `try:`/`except ImportError:` compat import, star imports and module aliases all resolve. Callers are found through decorators, `async def`, `self.` methods and nested functions. A re-export loop (`a` imports from `b`, `b` from `a`) makes Jedi recurse: on macOS and Linux its rows are kept as `ast_unresolved` and the error is logged. On Windows the 1 MB main-thread stack overflows first and the process dies with no traceback, so that test (`ReexportLoop`) is skipped there. No snapshot repo has such a loop (0 Jedi failures in its resolve log). Two known limits, each pinned by a test:
  - A second-hop alias (`a = ugettext; b = a; b()`) gets no row, since the one-hop rule follows one assignment. 0 cases in the snapshot's 6,553 `.py` files.
  - A property is read, not called, so its readers get no caller edge. 2 of the 421 functions holding a confirmed use are properties (Django's `BaseFormSet.management_form`, read 9 times, and tastypie's `Api.urls`), so their readers are missing from `call_edge`.
- Every real edge is checked (`TeamRunEdges`, with `MERIDIAN_RUN_ID`), by plain text and the rows rather than Jedi: no edge on an import line, every use edge on an `ast_resolved` row, every caller line calls its callee by name (`super().__init__(...)` for an `__init__`), and every edge within `call_edge_depth` of a use. All 2,288 pass.
- Candidates come from `meridian.scan`. The stand-in (`tests/standin_candidates.py`) is deleted: the tests build candidates with `meridian.scan.scan_repo`. Columns in characters or bytes both work.

## Rerun

```
pip install -r requirements.txt                                                  # Python 3.11+
python -m meridian.scan --run-id <run_id> --config meridian.toml                 # candidate.jsonl
python -m meridian.resolve --run-id <run_id> --config meridian.toml
python -m tests.score_packages --run-id <run_id> --config meridian.toml          # package precision and recall
python -m tests.hand_check_aliases --run-id <run_id> --config meridian.toml --initials XX   # interactive
MERIDIAN_RUN_ID=<run_id> python -m unittest tests.test_resolve -v                # 46 tests, Django + package checks
```

`requirements.txt` is the image's lockfile. Size the job for the 1.2 GB peak seen on 973531f7fd4cfd5e.

| File | What |
|---|---|
| `meridian/resolve.py` | The part |
| `tests/test_resolve.py` | Fixtures, the one command, Django answer keys, package score |
| `tests/score_packages.py` | Package precision and recall against their migration commits |
| `tests/hand_check_aliases.py`, `labels/alias_hand_check_30.csv` | Hand check (seed 1) and its answers |
| `requirements.txt` | Pinned jedi, parso, tree-sitter |
