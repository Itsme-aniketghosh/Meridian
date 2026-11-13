# Data, labels and the product's data side (Utkarsh): draft

Draft of my section for `Model-Pipeline/report/REPORT.md`, due Nov 12. **[TODO]** marks a number that's still open. Every other number comes from a script or test named next to it, and from `decisions.md`, `run2.md` and `diffs.md` in this folder.

## 1. The block

Everything is built from the team run `c5118b019d642a9b` (snapshot 2026-10-01T01:23:03Z, status `complete`, 22 repos). It was rebuilt from the team zip with `reproduce.py` Option 2. All 24 outputs match their golden md5s (`MERIDIAN_RUN_ID=c5118b019d642a9b python -m unittest tests.test_golden`), and `dvc status` reports the data and pipelines up to date. The block holds real names, so it never goes in git; the feature table holds none and does.

## 2. The feature table

`python -m model.data --config model.toml` reads `published/latest.json`, checks that it names the same run as `model.toml`, and writes `Model-Pipeline/data/features.parquet` and `features.schema.json`.

- **Rows:** 41,163 Spark first-parent commits authored from 2015 on (no merges among them), sorted by `authored_at`, then `sha`.
- **Columns:** the Kamei et al. (2013) just-in-time set (`la`, `ld`, `nf`, `nd`, `ns`, `entropy`, `ndev`, `age`, `nuc`, `exp`, `sexp`), plus `prior_bug_fixes`, the ticket fields and four slice columns (Contract 1).
- **No future:** every history feature uses only earlier first-parent commits, and file history follows renames. `file_bug_fixes` is counted at the snapshot, so it's never read. `prior_bug_fixes` is counted as of each commit, with SZZ's own fix rule, read from `meridian.toml` so the two can't drift.
- **No people:** authors are merged persons (decision 3), and no email, name or person id reaches the file. `data.check()` refuses any value containing `@`, and a test checks for name columns.
- **Determinism:** the same block and config give the same bytes. md5 `9c0ab22048fff931ac89e27c20112c56` (2.16 MB, zstd, pyarrow 22.0.0 pinned). Two rebuilds give the same md5.
- **Tests:** `tests/test_data.py`, 8 tests on a made-up history: window edges, `prior_bug_fixes` never seeing the future, merged emails, privacy, splits, md5.

| split | rows | positives |
|---|---|---|
| train (2015–2022) | 26,050 | 5,038 (19.3%) |
| val (2023) | 4,113 | 486 (11.8%) |
| test (2024) | 3,762 | 283 (7.5%) |
| none (2025 on) | 7,238 | 424 (follow-up incomplete) |

## 3. Labels and the label window

A commit is positive if a `bug_inducing` row names it, and that row's fix was authored 0 to `window_days` later.

**The window (decision 1).** The rule was the shortest window that catches 80% of blames and still leaves a full 2024. **No option met it.**
- Over all 22,007 SZZ pairs, 365 days catches 54% of blames and 730 days catches 72%; 80% takes about 988 days. On fully observed inducing commits (2015–2018, N = 11,808) it's 59% and 73%.
- A full 2024 needs a window of at most 638 days, which rules out 730.
- We used 365, by the packet's fallback rule, and Aniket confirmed it on Nov 9.

**The share slides by year.** The positive share falls from 30.8% (2015) to 7.5% (2024) under every window. It follows the number of fix commits per year (1,309 with blame in 2015, 340 in 2022), not the window. A model can learn "old means buggy" from this, which is why splits are by time and val and test are judged on their own years.

**Label checks, by an AI model, not a person.** For each pair it read the fix's ticket, both commits, and the lines blamed on the inducing commit next to the fix's replacement. **9 of 20 random positives are real (45%; 95% interval 26–66%), against a bar of 16: the label-precision bar fails.** It matches SZZ's 44% on the Defects4J labels: most positives name a commit that didn't bring the bug in (refactors, reverts, incidental lines). For decision 2, 2015 got 4 of 10 right and 2022 7 of 10. By the written rule (at most 2 of 10 wrong) the start year moves to 2016, but 2022 fails the same bar, the gap isn't significant on 10 each (p = 0.37), and it lands after the freeze. 2015 stays unless Aniket moves it.

**v1 vs c5118.** Not done: the v1 block wasn't on this machine, and c5118 was available from day 1.

## 4. Decisions

| # | Decision | Pick | Deciding number |
|---|---|---|---|
| 1 | Label window | 365 days | rule not met (54% / 72% / 80% at about 988 days); fallback |
| 2 | Start year | 2015, for now | AI check: 6 of 10 from 2015 wrong (rule says 2016); 2022 3 of 10; p = 0.37; Aniket's call |
| 3 | Author identity | merged person | 65.8% of train commits come from authors with 2+ emails; raw emails would mislabel about 11% of newcomers |
| 4 | `[MINOR]` commits | keep, with `is_minor` | 3.5% of train, 3.9% positive |
| 5 | Module slices | second level inside `sql/` | top-level `sql` is 41.8% of test, over the 40% bar |
| 6 | Time column | `authored_at` | author-to-merge gap 0 days for all 41,163 commits |
| 7 | Binary files | keep, 0 lines | 373 commits (0.9%), 18.5% positive |
| 8 | Table in git | full table | 2.16 MB |
| 9 | Run 2 scope | Django + packages | the full run took 21 minutes |
| 10 | Codemod or LLM | codemod for Django | 254 of 267 = 95.1% |
| 11 | `url()` replacement | `path` when the pattern allows, else `re_path` | 49.5% on the packages; no single style or rule gets close to 90% |

## 5. Tracking tables

schema.sql v1.3 adds `suggestion`, `outcome` and `push_alert`, taken from KNOWLEDGE-BASE.md's tested DDL, with `agent`, `model`, `route` and `policy_id` added for Contract 5. It also adds `policy` and `match` for the auditor; `match.mode` separates live matches from replays. The writer now checks that every `REFERENCES` parent exists, and that no row is known after the block's snapshot. A block without these tables (every pipeline run) is valid. `tests/test_tracking.py` has 12 tests, and KNOWLEDGE-BASE.md's `outcome_*` queries still return what the doc says.

## 6. The second real run

Run `5bc7deda64fcfa0a` uses `meridian.next.toml`: Django at its conversion commit, each package at its migration commit, and `--prev-run c5118…`. It took 21 minutes, under its own data root, and was never published. validate found 0 errors.
- **Django:** 265 of 267 lines removed. The other 2 are imports that the conversion commit rewrites but keeps for `test_legacy_aliases`, whose 4 calls come back as new sites, as appendix C §2 predicted.
- **Packages:** 1,205 of 1,236 lines removed. The 1,160 we expected counted lines the commits edited. autocomplete-light's commit rewrites only its imports, to `re_path as url`, so 45 calls stop resolving to the deprecated name without being edited. Removal goes by resolution, not by edit.
- **Owner numbers:** `owner_done` gives 1,491 of 1,530. `owner_eta` is NULL: it counts removals dated in the last 28 days, and these are dated 2017–2022. **[TODO: Aniket's call on the query.]**
- **Test:** `tests/test_run2.py` checks every number above against the answer keys.

## 7. The diff drafter

The codemod drafts one line per site line: rename rules, the owner's import order kept, and every draft must apply, change only its line, parse, and resolve to the new symbol in real code. Jedi's bundled Django stubs don't count, because with them any name resolves.
- **Django:** 254 of 267 lines match the real after-line at `c651331b34` (95.1%), which clears 05-test's 90%.
- **Packages:** 58.6% on the 12 finished repos at the parent commit. Owners chose their own `url()` style, and nothing at the parent shows which. Reading the owner's style from their first migrated files (decision 13), and drafting a call over several lines as one statement, joined when it fits the repo's line limit (decision 14), gives **73.2%** on the files the drafter wasn't shown, against 58.1% for the default style on the same lines. Of the 244 lines still missed, only 14 are the owner doing more than migrating. The rest are owners choosing line by line (`path` here, `re_path` there; joined here, kept there), which nothing at the parent shows. The 90% bar isn't met. In the product the drafter doesn't guess a `url()` style (decision 15): it waits for the owner's first migrated file, copies its style, learns from the owner's edits of earlier diffs, and never re-offers a diff they rejected. Learning from edits is built and tested on a fixture, with no real outcomes yet.
- **The LLM path** follows 04 §6. It only tries a line the codemod refused, with 2 or more same-shape examples, never inside `except`. Models go coding small → mid → large. Each answer goes through Vishwa's cache and the fact gate, then the same apply, parse and resolve checks, and is logged with `log_llm_call`. The gate checks only what a draft adds, since a regex reads as numbers and paths (decision 12). On the team run the codemod refuses just 20 of the 1,236 package lines, so the LLM path can add at most 1.6 points. The packages' gap is the choice of style, not lines the codemod can't write. The Nov 9 hand-off test (`test_handoff_diffs.py`) runs it on the fixture with a warm cache and 0 API calls. **[TODO: a real LLM run on the 20 lines, or "not run: no key".]**

## 7b. Late gaps (GAPS 24, 13 and 17, Nov 12)

- **Bug-fix history for the packages (gap 24).** `file_bug_fixes` was Spark-only, because only Spark has a ticket tracker and the window was the year before the snapshot, while the packages are pinned in 2017–2022. Repos with no tracker now read fixes from the commit subject (`Fixed #N`, `fix …`), for this table only, counted over each repo's own last year. Spark's rows are byte-identical; every package and Django now have rows, and 115 of 1,237 package call sites sit in a file with fixes. They appear in the next block built.
- **A cleaner SZZ (gap 13).** Whitespace, blank, comment and test lines were already skipped. The variant also follows moved and copied lines (`git blame -M -C`). It changes about 1 in 10 fix → inducing pairs, and val PR-AUC with the champion's forest goes from 0.4058 to 0.4002: no gain, so nothing changes. The Defects4J pick rate wasn't measured: the Defects4J repositories aren't on this machine.

- **Graph walkers (gap 17).** When the orchestrator's tools fail (by code: an error, a hang, nothing usable, or two fact-gate misses), walkers query the graph themselves. They run the eight stall queries from the stalled team and follow `blocked_by` to the teams that block it. They're read-only, capped in time, rows and queries, and cached, so a rerun makes 0 queries. On the fixture they find a cause in the upstream team that the stalled team's own context can't show. On the real block a walk takes about 0.5 s.

## 8. What didn't work, and why it's reported

- Decision 1's rule couldn't be met by any option. That's reported as a result, not hidden by moving the bar.
- The package answer key changed after run 2, from 1,160 edited lines to 1,205 resolved lines, for the reason above, and pass-bars.md says so.
- `owner_eta` doesn't give a number on historical commits.
- `age` is slightly negative on 8 rows (lowest −1.94 days, none positive). History is walked in landing order, but the gap is measured in author dates, and a commit can land after one authored later. No future data gets in. Models clamp it at 0. **[TODO: clamped in `data.py` with the next rebuild, or say it stayed model-side.]**
- The packages' diff bar (90%) isn't met: 73.2% with the owner's style and whole statements. The misses are mostly owners choosing differently line by line, not restructuring (14 of 244). Decision 14 drafts whole statements, which changes PASS-BARS §2.5's one-line rule and needs Aniket's OK.
