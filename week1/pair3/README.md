# Pair 3 · week 1

Third-party Django packages (`url()` → `path()`) and Defects4J. Run 2026-09-30.

## Verdict

- 19 of 20 packages had a findable migration. That's enough to build the corpus, but it's thin, and it can't test blocking.
- Defects4J now has an answer key for "which commit caused the bug": 130 labelled bugs.

## Flags

- 4 repos hold 80% of the ~1,085 call sites. Only 7 of 20 have 20 or more.
- The repos don't call each other, so blocking and chains can't be tested here.
- The brief's `git log -S "conf.urls import url"` misses `import include, url`. Use `git log -G 'django.conf.urls' -- '*.py'`.
- Aliases exist here too: autocomplete-light imports `re_path as url`, and 76 `url(` calls remain.
- Commit messages lie ("Minor cleanups", branch `translate-russian`). Detect from diffs.
- Defects4J records the fix, not the cause. Fonte's labels fill that gap, but 68 of their 130 IDs don't exist in Defects4J v3, so I remapped them.

## Impact on [05-test](../../docs/05-test.md)

| Test | Change |
|---|---|
| Week-1 cross-repo check | Passed. Build the corpus |
| Resolver recall | Report per repo, with N |
| Candidate goldens | Add real cases: re-export (guardian), alias (autocomplete-light), docstring |
| Burndown "grep = edit set" | Doesn't hold here. Count the lines the migration diff removed instead |
| Blocking, 30 hand-labelled edges | Not possible from this corpus. Fixtures only, unless we find dependent repos |
| SZZ | Score against 130 Defects4J labels. Spend the 50 hand labels on Python repos |
| Collect | Clone with `core.longpaths=true`, and detect the default branch |

## Next

1. Swap storages, crispy, taggit, and modeltranslation for autocomplete-light, dj-rest-auth, silk, and tastypie.
2. Freeze ground truth at each migration commit's parent. Label each line reachable, example, or dead.
3. Look for dependent pairs (dj-rest-auth → allauth, djoser → DRF). If there are none, mark blocking as fixtures-only.
4. Pin Defects4J v3 and its repo bundle.

---

## Django packages

**Migration commits**

| Repo | Sites | Commit | Date | Note |
|---|---:|---|---|---|
| django-oscar | 250 | `10dce286b` | 2020-07 | Inside a merge |
| wagtail | 241 | `4076b9ef5e` | 2020-02 | Plus `2cea9bd441` on a branch |
| django-rest-framework | 197 | `410575da` | 2020-09 | Plus `e215db20` |
| django-cms | 175 | `fb0d4f235` | 2021-11 | 306-file backport |
| django-allauth | 50 | `c4d7b410` | 2019-12 | |
| django-two-factor-auth | 37 | `fe57c40` | 2020-08 | "Minor cleanups" |
| django-debug-toolbar | 28 | `47d6b5e9` | 2020-05 | |
| django-guardian | 21 | `5a97f63` | 2019-09 | Scanner saw 7 (compat re-export) |
| channels | 17 | `a12800e` | 2020-10 | |
| cookiecutter-django | 16 | `6d4be405` | 2018-05 | |
| django-extensions | 13 | `b32f98e8`, `964ff7d5` | 2020–22 | |
| django-haystack | 13 | `6fc7e8b`, `d391a95` | 2021 | |
| graphene-django | 13 | `19ef9a0`, `5d5d7f1` | 2018–22 | 3 left in `examples/` |
| django-filter | 7 | `52eece7` | 2020-07 | |
| django-import-export | 7 | `f6864ff`, `e855fcf` | 2020 | |
| django-tenants | 6 | `6735fca`, `46fc62d` | 2017–22 | |
| django-modeltranslation | 3 | `d3e2396` | 2022-07 | Deleted, not migrated |
| django-taggit | 3 | `b748f81` | 2021-09 | |
| django-crispy-forms | 2 | `a42d534` | 2020-06 | |
| django-storages | 0 | none | | Never used `url()` |

Replacements: autocomplete-light (67 sites), dj-rest-auth (61), silk (35), tastypie (34). Also found: oauth-toolkit, djoser, simple-history, rosetta.

**Hand check (8 repos)**
- I read 30 random counted lines. All 30 are real calls, and the migration commit edited all 30.
- The scanner found 809 of the 834 lines the migration diffs removed (97%). It misses guardian's compat re-export and skips docstring examples.

**Timing:** 9 repos migrated before the deprecation (Aug 2020), 6 before removal (Dec 2021), and 4 after. graphene-django still isn't done.

**Junk to label:** `examples/`, dead urlconfs (rosetta), docstrings, cookiecutter `{{...}}` paths. Also watch merges (use `git diff M^1 M`) and django-cms's two branches.

## Defects4J

- **Setup:** use the bundled Dockerfile (7 min build, 5.5 GB image). Natively on Windows it needs Java 11, svn, cpanm, and `core.longpaths`.
- **Per bug:** `bug.id`, buggy and fixed revisions, ticket ID and URL, the minimized patch, failing tests with stack traces, and modified classes.
- **No cause recorded.** "Buggy" is just the fix's parent (61/61).
- **Labels:** [defects4j_bic_labels.csv](defects4j/defects4j_bic_labels.csv) has 130 hand-checked bug-inducing commits from Fonte (An et al., ICSE 2023).
  - 62 IDs work as-is.
  - Lang and Math came from an older repo conversion. I remapped those 68 by author time + subject, and one by patch-id.
- **Bisection** ([bisect.sh](defects4j/bisect.sh)): run the bug's test back through history until it starts failing.
  - On 38 sampled bugs it answered 8, and all 8 matched Fonte. About 105 s each.
  - The other 30 gave no answer, never a wrong one, mostly because old code won't compile.
- **Ticket key in the fix commit:** 812 of 854 (95%), the same as Spark. Chart (SVN), Time, and Gson are the misses.

## Rerun

Django packages (about 1 GB of clones, under 10 minutes):
```
django-packages/clone_repos.sh
cd django-packages && python scan.py && python verify.py   # per-repo JSON lands in django-packages/results/
```

Defects4J (Docker; image is 5.5 GB):
```
git clone -c core.longpaths=true https://github.com/rjust/defects4j && docker build -t d4j defects4j
cd defects4j/                 # this folder
./runall.sh                   # bisects the 38 bugs in sample_38.txt, about 30 min
python score.py               # writes results/bisect_vs_fonte.csv
```

To rebuild the inputs:
- Labels: `make_labels.py` needs Fonte's `combined.csv`, the v3 repos (inside the image), and the v2 repo bundle. Usage is at the top of the script.
- Ticket check: inside the container, `bash ticket_key_check.sh > results/ticket_key_in_fix_commit.csv`.

| File | What |
|---|---|
| `django-packages/clone_repos.sh` | Clones the 20 assigned and 8 replacement repos |
| `django-packages/scan.py`, `verify.py` | Migration counts, and the 30-line hand check |
| `defects4j/defects4j_bic_labels.csv` | The 130 labels (the answer key) |
| `defects4j/make_labels.py` | Rebuilds that file |
| `defects4j/bisect.sh`, `runall.sh`, `score.py` | Bisection and scoring |
| `defects4j/sample_38.txt`, `repos.csv` | Bug sample, and project → GitHub repo |
| `defects4j/ticket_key_check.sh` | The 95% ticket-key check |
| `defects4j/results/` | Saved outputs |
