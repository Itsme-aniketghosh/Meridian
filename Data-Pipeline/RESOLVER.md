# Resolver · step 1

Rochan's part of the [pipeline plan](PIPELINE-PLAN.md): what each candidate really is, and who calls it. Team run **70dd1dabfed544b8**, 2026-10-23 (after dynamic-config). The earlier run 973531f7fd4cfd5e is noted where its numbers differ.

```
python -m meridian.resolve --run-id <run_id> --config meridian.toml      # from Data-Pipeline/
```

| Reads | Writes |
|---|---|
| `snapshots/<run_id>/` (code), `out/<run_id>/candidate.jsonl` (Rachna) | `out/<run_id>/resolution.jsonl`, `out/<run_id>/call_edge.jsonl` |

## Verdict

| Task | Bar | Got | |
|---|---|---|---|
| D4 · Jedi + one-hop rule (`_ = old_name`) | 542 of 542 | **542 of 542** (533 import alias, 9 assign alias) | ✅ |
| D5 · Resolve every candidate, scored on the old name | precision ≥ 0.98 | Django 294 of 294 · packages **1,236 of 1,236**, recall 1,236 of 1,236 (was 1,059 on 16 packages) | ✅ |
| D5 · Hand-check 30 alias calls | 30 of 30, or misses written down | **30 of 30** agree (24.4 min, RH) | ✅ |
| D6 · `call_edge`, resolved only, depth ≤ 3 | edge fixtures pass | **2,288** edges (1,186 Django, 1,102 packages), fixtures exact. Was 2,054: dynamic-config's scope and `__init__` rules, plus 4 packages | ✅ |
| Same run, same bytes | 0 diffs | 0 diffs over repeated runs and any `PYTHONHASHSEED` | ✅ |

Run time on 70dd1dabfed544b8: about 74 s, peak 0.8 GB (Django + 20 packages). On 973531f7fd4cfd5e: about 76 s, peak 1.2 GB (16 packages).

## How it decides

- **Candidates:** Jedi `goto` (follow imports). `ast_resolved` only if it lands on the candidate's own old name, never the new name it aliases. `dropped` if it lands on something else, `ast_unresolved` if no answer (kept). `via = reexport` when the name comes through another module (guardian's `compat.py`: 18 rows).
- **Alias calls:** `_("Hello")` rows the scanner can't see. One-hop rule: when Jedi stops at `_ = ugettext_lazy`, follow the right side once more.
- **Call edges:** each confirmed call of a tracked symbol (depth 1), then callers of the functions holding one, up to `[resolver] call_edge_depth`. Module and class bodies get edges but don't propagate. Imports never count.

## Flags

- **Decision: the packages borrow Django from the snapshot, not one env per repo.** meridian.toml says `env = "per_repo_lockfile"`: install each repo's pinned tools into its own environment. That can't work here, so the packages resolve `django.conf.urls` against the `django/django` checkout in the same snapshot instead:
  - None of the 16 packages has a lockfile. They only ask for `django>=2.2`, and installing that today gets Django 5, which no longer has `url()` (removed in 4.0). Every real use would come out unresolved.
  - Installing from the internet changes with the day, so two runs could differ. The snapshot copy is the same on every machine, with nothing to install.
  - It works: 1,059 of 1,059. The limit: that checkout is Django from 2017, not each package's exact version. Fine for `url()`, which exists in both. A company's private libraries would still need real per-repo environments.
- **Determinism fixes.** A fresh `jedi.Script` per question (one Script per file flipped 1 row in 3,451 between runs), and parso never evicts during a run (it evicted by file mtime, and Jedi failed with `KeyError` on 30 names). And a submodule is searched for only in its package's folders: Jedi also asked the builtin importer, so on Windows `django/contrib/postgres/fields/array.py` and `forms/array.py` came back as the builtin `array` ("not importable", the UserWarnings) and nothing in them was reachable. Builtin names differ by OS, so the edges did too. No other file in the snapshot is named like a builtin, so Linux and Mac output doesn't change. On 70dd1dabfed544b8 it changes no row either (same `call_edge.jsonl` before and after, 2,293 edges): nothing calls into those two files' functions holding a tracked call.
- **Same bytes on every OS: Jedi's folder walk has one order.** To guess what a parameter holds, Jedi reads the calls of its function in the first file of a project walk that has one, and `os.walk` lists a folder in the filesystem's order: NTFS by name, APFS by a hash of the name. On 70dd1dabfed544b8 Windows got 2,293 edges against the Mac's 2,288 (django's `AdminField.label_tag` landed on `BoundField.label_tag`, plus its 4 callers in `tests/admin_utils/tests.py`) and 2 allauth `dropped` rows with another `resolved_to` (`allauth.account.utils.get_login_redirect_url.url` for `allauth/account/utils.py:39`, same at line 395). Jedi's walk now lists every folder in APFS's order (case-insensitive, macOS's default) on every OS. Any fixed order would do; this one keeps the Mac's output, the 2,288 above, unchanged. Checked on 70dd1dabfed544b8's django and allauth: Windows now gives the Mac's rows byte for byte; plain name order gives the old Windows rows, so the walk order was the only difference. Only searching the function's own file would also remove the OS, but it drops `ChangeList.get_results` → `Paginator.page`, which needs the call in `admin/options.py`.
- **Commits miss real uses.** 26 confirmed `url()` lines were never changed by their package's migration commit (unfinished migrations: graphene-django, tests, examples). The score counts them by a text rule, independent of Jedi.
- **Look-alikes rejected:** `django.template.defaulttags.url` (another Django `url`), `from .url import ...` (a module).
- **`blocked_by` may need depth 5.** A → F → up to 3 calls → B is 5 levels from the symbol; depth 3 covers blast radius. It's a setting.
- **Candidates come from `meridian.scan`.** The stand-in (`tests/standin_candidates.py`) is deleted: the tests build candidates with `meridian.scan.scan_repo`. Columns in characters or bytes both work.

## Changes (dynamic-config)

Not yet rerun on team data. The edge count will move: the scope fix removes edges such as every caller of Django's `get_text_list` (its default `ugettext_lazy('or')` runs in the module), and the `__init__` rule adds class-call edges.

| Change | Before | Now |
|---|---|---|
| Files | own `rglob("*.py")`: matched `Views.PY` on Windows, returned a folder named `pkg.py` (crash) | `meridian.scan.files`, `.py` only: one walk, case as in git, unreadable folder halts |
| Scope of an edge's caller | every enclosing def/class, so a default value counted as inside the function | `meridian.scan.scopes`: only bodies count. Same code as the scanner |
| Callers of `__init__` | `F()` never matched `F.__init__` | a call to the class counts when Jedi lands on the class def |
| Jedi crashes | `imported_from`, `one_hop` unguarded: one crash stopped the run. Crashes while finding callers gave no edges, silently | every `goto` is guarded (logged, no answer). The per-repo log line counts failures while finding callers |
| Jedi `sys_path` | the active virtualenv's (site-packages, the working directory) before the snapshot | explicit: the repo, its snapshot dependencies, the standard library. Nothing installed counts |
| Memory | every file's tree kept, parso cache shared across repos | parso cache emptied per repo, whole-repo walks keep no trees |
| jsonl | `splitlines()`: a candidate with U+2028 in `text` crashed the load | `meridian.common.read_jsonl` / `write_jsonl` |
| `resolution.receiver_from` | — | for a call `recv.name(...)` whose `recv`'s only binding in its scope is `recv = F(...)` (not a parameter, not bound twice), and Jedi lands `F` on exactly one def: `'repo:path:scope'` of that def. Otherwise NULL |

Fixtures added in `tests/test_resolve.py`: `Views.PY` and `pkg.py/`, a U+2028 candidate through the one command, `get_text_list`'s default (no caller edge), `make() -> F.__init__`, `receiver_from` (`c = get_client(); c.verify_token()` → `fix/repo:shared/client.py:get_client`, and NULL when bound twice, a parameter, another scope, or a class), Jedi crashes counted, `sys_path`, parso and tree memory, `score_packages` deaf to `diff.noprefix`, the hand check saving UTF-8.

## Next

1. Rerun on Rachna's `candidate.jsonl` once it's on main, and on each new `run_id`.
2. Experiment and confirm depth for `blocked_by`, the setting itself is variable and can be changed later(currently set to 3). This decision is much later and is of little relevance now, Forward edges aren't needed by any feature; they can be added later as their own setting.
3. Utkarsh: `requirements.txt` is the lockfile for the image; the job needs ~1.2 GB.

## Rerun

```
pip install -r requirements.txt                                                  # Python 3.11+
python -m meridian.scan --run-id <run_id> --config meridian.toml                 # Rachna's candidate.jsonl
python -m meridian.resolve --run-id <run_id> --config meridian.toml
python -m tests.score_packages --run-id <run_id> --config meridian.toml          # package precision and recall
python -m tests.hand_check_aliases --run-id <run_id> --config meridian.toml --initials XX   # interactive
MERIDIAN_RUN_ID=<run_id> python -m unittest tests.test_resolve -v                # 32 tests, Django + package checks
```

| File | What |
|---|---|
| `meridian/resolve.py` | The part |
| `tests/test_resolve.py` | Fixtures, the one command, Django answer keys, package score |
| `tests/score_packages.py` | Package precision and recall against their migration commits |
| `tests/hand_check_aliases.py`, `labels/alias_hand_check_30.csv` | Hand check (seed 1) and its answers |
| `requirements.txt` | Pinned jedi, parso, tree-sitter |
