# Week 1 · Pair 3 review

Third-party Django packages (`url()` → `path()`) and Defects4J. Run 2026-09-30 on fresh clones. Scripts: [pair3_scan.py](pair3_scan.py) (counts), [pair3_verify.py](pair3_verify.py) (line check).

## Verdict

Packages found: **19 / 20**. The data is usable, but it's thinner than that number suggests. Most repos have only a handful of call sites, and none of them call each other, so it can't test cross-team blocking. Defects4J doesn't hold the answer key we wanted it for.

## What this means for testing ([05-test](../docs/05-test.md))

| Test | Impact |
|---|---|
| Week-1 cross-repo check | Passed (19 ≥ 12). Build the corpus |
| Resolver recall | Report per repo, with N. Pooled recall is really a 4-repo number |
| Candidate goldens | Now there are real cases: re-export (guardian `compat.py`), alias (`re_path as url`), and a docstring false positive |
| Burndown "grep = edit set" check | Doesn't hold here. Grep misses guardian and includes docstrings. Use the lines the migration diff removed as the independent count instead (97% match on 8 repos) |
| Ground truth | Needs a reachable / example / dead label. Leftovers in `examples/` and dead urlconfs are the repo's misses, not ours |
| Blocking: "30 `blocked_by` edges on the cross-repo corpus" | Can't come from this corpus, because the repos don't call each other. Blocks, chains, and P4 stay fixtures-only unless we find dependent pairs |
| SZZ vs Defects4J | Defects4J only tests finding the bug-fix commit. The 50 hand-labelled bug-introducing commits are the whole cause-side test. The Dataset Assignments brief has this wrong, but 05-test already has it right |
| Collect | Clone step needs `core.longpaths=true` and must detect the default branch. Add a fixture for each |

## What to do now

1. Swap storages, crispy, taggit, and modeltranslation for autocomplete-light, dj-rest-auth, silk, and tastypie.
2. Freeze ground truth at each migration commit's parent. Label every line reachable / example / dead.
3. Add the three new goldens (re-export, alias, docstring) to candidate extraction.
4. This week, look for dependent pairs (dj-rest-auth → allauth, djoser → DRF). If there are none, mark blocking as fixtures-only in 05-test and say so on screen.
5. Fix the Defects4J line in the brief. Then either budget the 50 hand-labelled bug-introducing commits or pull the Fonte labels.
6. Add `core.longpaths` and default-branch detection to the clone step.

## Flags

- Defects4J has no bug-inducing commit. "Buggy" is just the fix's parent (61/61 checked). It can't grade our SZZ guesses.
- 4 repos hold 80% of all call sites (oscar, wagtail, DRF, cms). Only 7 of 20 have 20 or more.
- The 20 repos don't depend on each other. This tests multi-repo scanning, not blocking chains.
- The brief's `git log -S "conf.urls import url"` misses `import include, url`. That's likely why three repos "found nothing".
- django-storages never used `url()`. Swap it out.
- Alias trap: autocomplete-light does `re_path as url`, so the imports look migrated but 76 `url(` calls remain.
- Commit messages lie ("Minor cleanups", branch `translate-russian`). Detect from diffs, not messages.
- 3 of 29 clones fail on Windows without `core.longpaths=true`.

---

## Q&A

**Q: How many of the 20 had a findable migration commit?**
19. Only storages had nothing.
- 13 did it in one clean commit
- 5 spread it over several commits or years
- 1 (modeltranslation) deleted dead code instead of migrating it

**Q: Which commit, per repo?**

| Repo | Sites | Commit | Date | Files | Note |
|---|---:|---|---|---:|---|
| django-oscar | 250 | `10dce286b` | 2020-07-24 | 28 | Inside merge `84e1412f4` |
| wagtail | 241 | `4076b9ef5e` | 2020-02-17 | 44 | Plus `2cea9bd441` on a branch |
| django-rest-framework | 197 | `410575da` | 2020-09-08 | 30 | Plus `e215db20`. Two commits, not one |
| django-cms | 175 | `fb0d4f235` | 2021-11-08 | 306 | Mixed backport commit |
| django-allauth | 50 | `c4d7b410` | 2019-12-09 | 15 | |
| django-two-factor-auth | 37 | `fe57c40` | 2020-08-03 | 12 | Titled "Minor cleanups" |
| django-debug-toolbar | 28 | `47d6b5e9` | 2020-05-23 | 5 | Inside a merge |
| channels | 17 | `a12800e` | 2020-10-05 | 2 | |
| cookiecutter-django | 16 | `6d4be405` | 2018-05-14 | 4 | |
| django-extensions | 13 | `b32f98e8`, `964ff7d5` | 2020–22 | 3 | 4 left at HEAD are in a docstring |
| django-haystack | 13 | `6fc7e8b`, `d391a95` | 2021 | 28 | |
| graphene-django | 13 | `19ef9a0`, `5d5d7f1` | 2018–22 | 12 | Unfinished: 3 left in `examples/` |
| django-filter | 7 | `52eece7` | 2020-07-21 | 2 | |
| django-guardian | 7 | `5a97f63` | 2019-09-08 | 6 | Really 21 (see line check) |
| django-import-export | 7 | `f6864ff`, `e855fcf` | 2020 | 5 | |
| django-tenants | 6 | `6735fca`, `46fc62d` | 2017–22 | 18 | |
| django-modeltranslation | 3 | `d3e2396` | 2022-07-12 | 9 | Deletion, not migration |
| django-taggit | 3 | `b748f81` | 2021-09-26 | 1 | |
| django-crispy-forms | 2 | `a42d534` | 2020-06-19 | 1 | |
| django-storages | 0 | none | | | Dead end |

"Sites" = import lines + `url(` call lines at the peak, the same unit as Django's 279. The total is about 1,085.

**Q: Were the brief's notes right?**
- "DRF has one clean commit": close. It's 182 of 197 in one commit, with another 13 two weeks earlier.
- "Wagtail has two": yes.
- "Three found nothing": it was the search command, not the repos. `-S` returns 0 on extensions, modeltranslation, and tenants. Use `git log -G 'django.conf.urls' -- '*.py'`.

**Q: Did we check the counts by hand?**
Yes, two checks across 8 repos (DRF, wagtail, oscar, allauth, two-factor, debug-toolbar, channels, guardian):
- Precision: I sampled 30 of the 809 counted lines and read each one. All 30 are real `url(...)` calls, and the migration commit edited all 30. The sample leans toward the big repos and contained no import lines.
- Recall: I compared the scanner's count with the `url` lines each migration diff removed. They match exactly in 6 of 8 repos. Overall the scanner found 809 of the 834 lines the diffs removed (97%).
  - DRF, 10 extra: code examples in docstrings. Edited, but not call sites.
  - Guardian, 14 extra: a real miss. `guardian/compat.py` re-exports `url` and the rest of the code imports it from there. A regex can't follow that hop; a resolver can.
- Time: the script took 5 seconds and reading the 30 lines took about 10 minutes.

**Q: Replacements for the weak ones?**
I scanned 8 more, and all 8 had a findable migration. Best swaps for storages, crispy (2 sites), taggit (3), and modeltranslation:

| Repo | Sites | Commit | Note |
|---|---:|---|---|
| django-autocomplete-light | 67 | `1f500ad4` | Alias shim, keep as a hard case |
| dj-rest-auth | 61 | `ea14056` | Titled "Bumps all-auth + fixes tests" |
| django-silk | 35 | `b8331d0` | url → re_path → path |
| django-tastypie | 34 | `6d06e29` | Clean |

Also found: oauth-toolkit (32), djoser (24), simple-history (17, now at `django-commons/`), and rosetta (13, with 3 left in a dead file).

**Q: When did they migrate?**
Django deprecated `url()` in 3.1 (2020-08-04) and removed it in 4.0 (2021-12-07).
- Before the deprecation: 9 repos
- Between deprecation and removal: 6
- After removal, or still unfinished: 4

That's a real spread, which is useful for the stall story.

**Q: Does Defects4J set up cleanly?**
Not on this laptop.
- The clone (200 MB) needs `core.longpaths=true` on Windows.
- Full setup needs Java 11, svn, and cpanm (none installed). `init.sh` then pulls another 632 MB.
- Docker is installed and the repo ships a Dockerfile, so that's the route. Not tried yet.
- The metadata below needs only the clone.

**Q: What does Defects4J give per bug?**
- `active-bugs.csv`: `bug.id`, `revision.id.buggy`, `revision.id.fixed`, `report.id`, `report.url`
- `deprecated-bugs.csv`: the same plus `deprecated.version` and `deprecated.reason`
- Per-bug files: `patches/<id>.src.patch` and `.test.patch` (minimized fix), `trigger_tests/<id>` (failing tests with stack traces), `modified_classes`, `loaded_classes`, `relevant_tests`
- Numbers:
  - 854 bugs, of which 337 link to Apache Jira, 280 to GitHub, and 18 to nothing
  - 85% of fixes touch one file, with a median of 6 changed lines
  - Chart uses SVN revision numbers, not git SHAs

**Q: Can it check our "which commit caused the bug" code?**
No. It stores the fix and the commit before the fix, nothing about the cause. It can still check:
- ticket → fix-commit links
- the files a fix touched (the fragility input)

For the cause side we need outside labels. Fonte (Sohn et al., ICSE 2023) published some for Defects4J; I haven't downloaded it or checked how many. The dataset is also Java, so this check needs tree-sitter-java.

**Q: What else did the brief miss?**
- Merges hide diffs in first-parent history (oscar, debug-toolbar, haystack, djoser, silk). Use `git diff M^1 M`.
- django-cms migrated on its 3.x branch and backported to 4.x, so the default branch alone misleads.
- Junk to label:
  - `examples/` and demo projects
  - dead urlconfs not wired to ROOT_URLCONF (rosetta)
  - docstrings
  - cookiecutter's `{{...}}` template paths
- "Still at HEAD" isn't the same as "used". Ground truth needs a reachable / dead flag.
- Default branches vary (`main`, `master`, `develop`). Don't hardcode one.

**Q: How expensive is this?**
- Cloning all 20 (about 875 MB) took 30 s.
- Scanning full history takes under a minute per small repo and a few minutes each for the big ones.

## Caveats

- The counts are regex, not AST, and only cover first-parent history of the default branch.
- Indirect imports through a compat module are missed, as with guardian.
