# Step L: the second real run

Run `5bc7deda64fcfa0a`, from [meridian.next.toml](../../../Data-Pipeline/meridian.next.toml), with `--prev-run c5118b019d642a9b`:
- snapshot_at is 2026-10-08T00:00:00Z.
- It took 21 minutes end to end (collect through validate) on a laptop. The scan was 19 of those minutes.
- validate found 0 errors and 8 warnings: drift in `call_site`, `candidate`, `resolution` and `call_edge` (expected, since most sites are gone), 18% missing `resolved_to`, and the 289 Spark tickets dated 0010–0012 that every run warns about.
- **Never published.** It's an answer-key run, not the live map.

## How it was run

Everything ran under its own data root (`MERIDIAN_DATA`), so the team run's `snapshots/`, `out/` and `published/` were never written to:
- **Data root:** `~/meridian-run2/`.
- **Caches:** `snapshots/_cache/{repos,jira,gitlab,scan,resolve,szz}` are full copies of the main ones.
- **Run 1's output:** `out/c5118b019d642a9b` is a link to the main one, which join only reads.
- **Ticket data:** snapshot_at falls before the first ticket pull (2026-10-13), so collect reads the same pulls and never queries Jira or GitLab.
- **Alerts:** none are configured, so they're only logged.

```bash
export MERIDIAN_DATA=~/meridian-run2
python -m meridian.collect --run-id new --config meridian.next.toml        # run_id=5bc7deda64fcfa0a
for p in history scan resolve owners; do python -m meridian.$p --run-id 5bc7deda64fcfa0a --config meridian.next.toml; done
python -m meridian.join --run-id 5bc7deda64fcfa0a --config meridian.next.toml --prev-run c5118b019d642a9b
python -m meridian.graph --run-id 5bc7deda64fcfa0a --config meridian.next.toml
python -m meridian.validate --run-id 5bc7deda64fcfa0a --config meridian.next.toml --prev-run c5118b019d642a9b
```

Test: `MERIDIAN_RUN2=$MERIDIAN_DATA/out/5bc7deda64fcfa0a python -m unittest tests.test_run2 -v` (3 tests, OK; skipped without the data).

## Results against the packet's checks

| Check | Expected | Got |
|---|---|---|
| Django call-site lines removed | all 267 | **265 removed, plus 2 rewritten but still importing the legacy names.** Every one of the 267 lines is edited |
| `test_legacy_aliases` | 4 new on purpose | **4 new** (tests/i18n/tests.py:60, 61, 62, 64) |
| Package lines removed | 1,160 of 1,236 | **1,205 of 1,236.** The extra 45 are all in autocomplete-light (see below) |
| Removed rows' `known_at` | run 2's snapshot | all 1,491 at 2026-10-08T00:00:00Z |
| `owner_done` | real numbers | **1,491 of 1,530** overall. Django 285 of 293, oscar 247 of 247, autocomplete-light 65 of 67 |
| `owner_eta` | real numbers | **NULL for every repo.** See below |

Join summary: run 1 had 1,526 present sites. 35 are matched again (4 of them changed), 1,491 are removed and 4 are new, leaving 39 present.

### Removed lines per repo

| repo | run 1 lines | removed | left |
|---|---|---|---|
| django/django | 267 | 265 | 2 (the `test_legacy_aliases` imports) |
| django-oscar | 247 | 247 | 0 |
| wagtail | 240 | 240 | 0 |
| django-rest-framework | 182 | 182 | 0 |
| django-cms | 160 | 160 | 0 |
| autocomplete-light | 67 | 65 | 2 |
| dj-rest-auth | 51 | 51 | 0 |
| django-allauth | 50 | 50 | 0 |
| two-factor-auth | 37 | 37 | 0 |
| tastypie | 31 | 31 | 0 |
| debug-toolbar | 29 | 28 | 1 |
| django-silk | 28 | 26 | 2 |
| django-guardian | 25 | 25 | 0 |
| channels | 17 | 17 | 0 |
| cookiecutter-django | 16 | 16 | 0 |
| graphene-django | 13 | 3 | 10 |
| django-haystack | 13 | 10 | 3 |
| django-extensions | 10 | 2 | 8 |
| django-filter | 7 | 7 | 0 |
| import-export | 7 | 3 | 4 |
| django-tenants | 6 | 5 | 1 |

The appendix's per-repo edited-line counts match wherever it gives them: oscar 247, wagtail 240, DRF 182, cms 160, graphene 3, extensions 2, import-export 3. The one exception is autocomplete-light.

### Why 1,205 and not 1,160

autocomplete-light's migration commit `1f500ad4` changes 21 `.py` files. Each change rewrites the import, from `from django.conf.urls import url` to `from django.urls import re_path as url`. The 45 `url(...)` call lines aren't touched, but they now resolve to `re_path`, which isn't the tracked symbol, so join marks them removed. The appendix's 1,160 counted lines the commits *edited* (20 in autocomplete-light). The run counts sites that *no longer resolve* to the deprecated name (65). By resolution, the run is right.

This matters for decision 11: an aliased import (`re_path as url`) is a third option alongside "always `re_path`" and "`path` when there's no regex". The codemod has to recognize it.

### Why `owner_eta` is NULL

`owner_eta` divides by the removals whose `effective_date` falls in the 28 days before `asof.at`. A removed row's `effective_date` is the removing commit's date: 2017-01-26 for Django and 2017–2022 for the packages. Both runs pin historical commits but have 2026 snapshots, so no removal falls inside the window and the query returns NULL.

That's correct behavior for this run, but it isn't the "real numbers" the packet asks for. There are two ways to get them, and both need Aniket's OK because they change the KB query:
- count removals by `known_at` between the two runs (removed since the last run);
- or run with `asof.at` at each migration commit's date.

### Decision 9 (second run's scope)

Test: run Django first, and add the packages if the run takes under an hour. The full run, with Django, all 20 packages and Spark, took 21 minutes. **Pick: Django plus packages.** That gives 1,205 removed package lines across 20 teams, which is what makes burndown per team meaningful.
