# Data, labels and the product's data side (Utkarsh)

My section for `Model-Pipeline/report/REPORT.md`. Every number comes from a script, test or file named next to it, or from `decisions.md`, `run2.md`, `diffs.md` and `gaps.md` in this folder.

**Which block each number comes from.** All three are builds of the same snapshot (2026-10-01T01:23:03Z) with the same pinned commits and ticket pulls (GAPS.md, "Which block").
- **`c5118b019d642a9b`, the team run:** the feature table, the labels, decisions 1–11 and 13–14, and the diff drafter's results.
- **`bb6b5f4e32a4bee7`:** the team config with gap 24's code. It covers gap 24's coverage numbers and the product runs (Aniket's section).
- **`5bc7deda64fcfa0a`, run 2:** its numbers in §6. Re-made on Nov 14 as `7b745b4430bbac75` with the same results.

## 1. The block

Everything starts from the team run `c5118b019d642a9b` (status `complete`, 22 repos). It was rebuilt from the team zip with `reproduce.py` Option 2. All 24 outputs match their golden md5s (`MERIDIAN_RUN_ID=c5118b019d642a9b python -m unittest tests.test_golden`), and `dvc status` reports the data and pipelines up to date. The block holds real names, so it never goes in git. The feature table holds none, so it does.

## 2. The feature table

`python -m model.data --config model.toml` reads `published/latest.json`, checks that it names the same run as `model.toml`, and writes `Model-Pipeline/data/features.parquet` and `features.schema.json`. That's guideline §2.1 and §8.2: data loaded from the pipeline, versioned. Every model run is tagged with `block_run_id` and `features_md5`.

- **Rows:** 41,163 Spark first-parent commits authored from 2015 on, none of them merges, sorted by `authored_at`, then `sha`.
- **Columns:**
  - the Kamei et al. (2013) just-in-time set: `la`, `ld`, `nf`, `nd`, `ns`, `entropy`, `ndev`, `age`, `nuc`, `exp`, `sexp`;
  - `prior_bug_fixes`;
  - the ticket fields;
  - four slice columns (Contract 1).
- **No future:**
  - Every history feature uses only earlier first-parent commits, and file history follows renames.
  - `file_bug_fixes` is counted at the snapshot, so the table never reads it.
  - `prior_bug_fixes` is counted as of each commit, with SZZ's own fix rule read from `meridian.toml`, so the two can't drift.
- **No people:** authors are merged persons (decision 3), and no email, name or person id reaches the file. `data.check()` refuses any value containing `@`, and a test checks for name columns.
- **Determinism:** the same block and config give the same bytes. md5 `9c0ab22048fff931ac89e27c20112c56` (2.16 MB, zstd, pyarrow 22.0.0 pinned). Two rebuilds gave the same md5, and the 2024 run checked it again before scoring (`reports/2024/RUN-LOG.md`).
- **Tests:** `tests/test_data.py`, 8 tests on a made-up history: window edges, `prior_bug_fixes` never seeing the future, merged emails, privacy, splits and the md5.

| split | rows | positives |
|---|---|---|
| train (2015–2022) | 26,050 | 5,038 (19.3%) |
| val (2023) | 4,113 | 486 (11.8%) |
| test (2024) | 3,762 | 283 (7.5%) |
| none (2025 on) | 7,238 | 424 (follow-up incomplete) |

## 3. Labels and the label window

A commit is positive if a `bug_inducing` row names it, and that row's fix was authored 0 to `window_days` later.

**The window (decision 1).** The rule was to take the shortest window that catches 80% of blames and still leaves a full 2024. **No option met it.**
- **All 22,007 SZZ pairs:** 365 days catches 54% of blames and 730 days catches 72%. 80% takes about 988 days.
- **Fully observed inducing commits** (2015–2018, N = 11,808): 59% and 73%.
- **Ruled out:** a full 2024 needs a window of at most 638 days, which excludes 730.
- **Picked:** 365, by the packet's fallback rule. Aniket confirmed it on Nov 9.

**The share slides by year.** The positive share falls from 30.8% (2015) to 7.5% (2024) under every window. It follows the number of fix commits per year (1,309 with blame in 2015, 340 in 2022), not the window. A model can learn "old means buggy" from this. That's why the splits are by time, and val and test are each judged on their own year.

**Label checks, done by an AI model, not a person** (PR #94, `decisions.md`). For each pair, the model read:
- the fix's ticket;
- both commits;
- the lines blamed on the inducing commit, next to the fix's replacement.

The results:
- **The label-precision bar fails:** 9 of 20 random positives are real (45%; 95% interval 26–66%), against a bar of 16.
- **It matches SZZ's known rate,** 44% on the Defects4J labels. Most false positives name a commit that didn't bring the bug in: refactors, reverts, incidental lines.
- **By year (decision 2):** 2015 had 4 of 10 right and 2022 had 7 of 10. The written rule (at most 2 of 10 wrong) would move the start year to 2016. But 2022 fails the same bar, the gap isn't significant on 10 each (p = 0.37), and an unchecked AI read doesn't meet a rule written for a person's. Aniket kept 2015.

**v1 vs c5118.** Not compared: the v1 block wasn't on this machine, and c5118 was available from day 1. REPORT.md §2 of the data pipeline lists what changed from v1.

## 4. Decisions

| # | Decision | Pick | Deciding number |
|---|---|---|---|
| 1 | Label window | 365 days | rule not met (54% / 72%; 80% needs about 988 days); fallback |
| 2 | Start year | 2015 | AI check: 2015 had 6 of 10 wrong (the rule says 2016), 2022 had 3 of 10; p = 0.37; Aniket's call |
| 3 | Author identity | merged person | 65.8% of train commits come from authors with 2+ emails; raw emails would mislabel about 11% of newcomers |
| 4 | `[MINOR]` commits | keep, with `is_minor` | 3.5% of train, 3.9% positive |
| 5 | Module slices | second level inside `sql/` | top-level `sql` is 41.8% of test, over the 40% bar |
| 6 | Time column | `authored_at` | author-to-merge gap 0 days for all 41,163 commits |
| 7 | Binary files | keep, 0 lines | 373 commits (0.9%), 18.5% positive |
| 8 | Table in git | full table | 2.16 MB |
| 9 | Run 2 scope | Django + packages | the full run took 21 minutes |
| 10 | Codemod or LLM | codemod for Django | 254 of 267 = 95.1% |
| 11 | `url()` style with nothing to go on | `path` when the pattern allows, else `re_path` | 612 of 1,236 = 49.5% on the packages; no single style gets close to 90%, and the best per-repo style picked in hindsight stops near 68% |
| 12 | Fact gate on a line of code | gate only the tokens a draft adds, as numbers; no new comments | gated whole, even an unchanged line fails (`\d{4}` reads as 4); gated this way, a `path()` conversion passes and an invented number, person or email is stopped |
| 13 | Style source | the owner's first migrated files (3 votes or 3 files) | held-out files on the 12 finished repos: 464 → 600 of 895 (51.8% → 67.0%); the rule needed +10 points |
| 14 | Draft unit | the whole statement, joined when it fits the repo's line limit | 600 → 655 of 895 (67.0% → 73.2%); costs cms (143 → 125) and two-factor (14 → 5) |
| 15 | No owner lines yet | wait (`style_unknown`); copy the owner's style; learn from edits; never re-offer a rejected diff | a wrong diff costs the owner more than none; measured as 13–14's held-out numbers; learning from edits tested on a fixture only |

## 5. Tracking tables

schema.sql v1.3 adds three tables from KNOWLEDGE-BASE.md's tested DDL: `suggestion`, `outcome` and `push_alert`.
- **Contract 5 columns:** `agent`, `model`, `route` and `policy_id`.
- **The auditor's tables:** `policy` and `match`. `match.mode` separates live matches from replays.
- **Writer checks:** every `REFERENCES` parent exists, and no row is known after the block's snapshot.
- **Optional:** a block without these tables is still valid, as every pipeline run is.
- **Tests:** `tests/test_tracking.py` has 12, and KNOWLEDGE-BASE.md's `outcome_*` queries still return what the doc says.

## 6. The second real run

Run `5bc7deda64fcfa0a` uses `meridian.next.toml`: Django at its conversion commit, each package at its migration commit, and `--prev-run c5118…`. It took 21 minutes under its own data root and was never published. validate found 0 errors.

- **Django:** 265 of 267 lines removed. The other 2 are imports that the conversion commit rewrites but keeps for `test_legacy_aliases`, whose 4 calls come back as new sites, as appendix C §2 predicted.
- **Packages:** 1,205 of 1,236 lines removed. The 1,160 we expected counted the lines the commits edited. autocomplete-light's commit rewrites only its imports, to `re_path as url`, so 45 calls stop resolving to the deprecated name without being edited. Removal goes by resolution, not by edit.
- **Owner numbers:** `owner_done` gives 1,491 of 1,530. `owner_eta` is NULL. It projects from removals in the last 28 days before the snapshot, and these removals are dated 2017–2022. On historical commits it can't give a number; on a live repo it reads the last four weeks as designed. The query is unchanged.
- **Test:** `tests/test_run2.py` checks every number above against the answer keys.
- **Rerun on Nov 14** (`reports/final/RUN-LOG.md` step 3, main `844a721`):
  - **New run_id:** `7b745b4430bbac75`, because gap 24 added `fix_message` to `meridian.next.toml` and the run_id hashes the config.
  - **Validate:** 0 errors, 9 warnings: the same 8 as before, plus the expected rise in `file_bug_fixes`.
  - **Tests:** `test_run2` passed 4 of 4.
  - **4 more `call_site` rows:** they're present sites whose `bug_fixes` changed from 0 to 3, so join records them again. No removal count moved.

## 7. The diff drafter

**How it works.** The codemod drafts one statement per site:
- rename rules, keeping the owner's import order;
- every draft must apply, change nothing outside its own statement, parse, and resolve to the new symbol in real code. Jedi's bundled Django stubs don't count, because with them any name resolves.

**Results** (`diffs.md`, `tests/test_diffs.py`):
- **Django: 254 of 267 lines match** the real after-line at `c651331b34` (95.1%), which clears 05-test's 90%.
- **Packages: 73.2% (655 of 895). The 90% bar isn't met, and GAPS 3 is frozen at this number** (Nov 13).
  - **The test:** the 895 lines come from the 12 finished repos, in the files the drafter wasn't shown.
  - **Without the owner's lines:** at the parent commit, with no owner lines to read, it's 58.6% (623 of 1,063). The best single style is 49.5% over all 20 repos (decision 11).
  - **What closes most of the gap:** reading the owner's style from their first migrated files (decision 13) and drafting multi-line calls as one statement (decision 14).
  - **Of the 244 lines still missed,** only 14 (5.7%) are the owner doing more than migrating. The other 230 are owners choosing line by line, which nothing at the parent commit shows: `path` here and `re_path` there, joined here and kept there, a strict converter here and a loose one there.
- **One rule changed after the drafts ran:** decision 14 changes PASS-BARS §2.5's rule from "a draft that changes any other line is refused" to "any other statement". Aniket signed it off on Nov 15, and PASS-BARS says it came after the results. `--one-line` keeps the old rule and gives the old numbers.

**In the product, the drafter doesn't guess a `url()` style** (decision 15). It waits for the owner's first migrated file, copies its style, learns from the owner's edits of earlier diffs, and never re-offers a diff they rejected. Learning from edits is built and tested on a fixture (`OwnerFirst`), but there are no real outcomes to measure it on yet.

**The LLM path** follows 04 §6:
- **When it runs:** only on a line the codemod refused, with 2 or more same-shape examples, and never inside `except`. A line whose facts name a person is never sent.
- **The ladder:** models go coding small → mid → large.
- **The checks:** each answer goes through Vishwa's cache and the fact gate (decision 12), then the codemod's own apply, parse and resolve checks.
- **Logging:** every answer goes to `log_llm_call`.
- **Tests:** the Nov 9 hand-off test (`tests/test_handoff_diffs.py`) runs it on the fixture with a warm cache and 0 API calls.

**The model comparison (GAPS 2, Nov 13, `gaps.md`, `diffs_compare.json`).**
- **Which lines:** of the 1,236 package lines, the codemod refuses 20, so the LLM path can add at most 1.6 points. Its own rules leave 12 to try: 11 in django-guardian and 1 in debug-toolbar.
- **The rule, written before the run:** the cheapest model within 2 points of the best pass rate wins, and ties go to the open-weights model.

| model | tried | drafted | matched |
|---|---:|---:|---:|
| Gemini 3.8 Flash | 12 | 0 | 0 |
| Gemini 3.1 Pro | 12 | 0 | 0 |
| Gemma 4 | 12 | 0 | 0 |
| Llama 4 Maverick, Scout | unreachable (HTTP 404: not enabled in the project's Model Garden) | | |

**Why every model drafted 0.** The lines can't pass as single drafts:
- **django-guardian:** it imports `url` from `guardian.compat`, so a rewritten line doesn't resolve until that import changes too. The real migration changed both in one commit. Applied alone, the line would break the file, and the resolve check is right to refuse it.
- **Gemma:** it also lists names under `used`, which the strict reader refuses.

**Diagnostic, not the result.** With the resolve check skipped for the LLM's lines only, the same cached answers match:
- Flash 6 of 12;
- Pro 6 of 12 (7 with a lenient reader);
- Gemma 6 of 12 with the lenient reader.

**Pick:** Gemma 4, by the tie rule, but the pick says nothing: no model gets a line through. The drafter stays codemod-only for the packages, and `LADDER` is unchanged. The run cost about $0.003. `python -m meridian.diffs_compare --run-id c5118b019d642a9b --models gemini-flash,gemini-pro,gemma-4` reruns it from the committed cache with 0 calls.

## 8. Late gaps (GAPS 24, 13 and 17)

**Bug-fix history for the packages (gap 24, PR #92).**
- **The problem:** `file_bug_fixes` was Spark-only. Only Spark has a ticket tracker, and the window was the year before the snapshot, while the packages are pinned in 2017–2022.
- **The fix:** repos with no tracker now read fixes from the commit subject (`Fixed #N`, `fix …`), counted over each repo's own last year. This feeds only that table. The model's labels keep the ticket rule.
- **On block `bb6b5f4e32a4bee7`** (built Nov 13, 0 errors):
  - Spark's rows are byte-identical, and `bug_inducing` is unchanged;
  - `file_bug_fixes` grows from 1,492 to 3,248 rows;
  - 367 of 1,526 migration call sites now carry a bug-fix count, where before none did. 115 of them are package sites.
- **The rebuild:** Rochan rebuilt the block on Nov 14 and got the same run_id and md5s.

**A cleaner SZZ (gap 13).**
- **What it already skipped:** whitespace, blank, comment and test lines.
- **What the variant adds:** it follows moved and copied lines (`git blame -M -C`). Of 22,007 fix → inducing pairs, 1,223 go and 2,460 are new.
- **The result:** val PR-AUC with the champion's forest goes from 0.4058 to 0.4002. That's no gain, so the labels stay as they are.
- **Not measured:** the Defects4J pick rate, because the Defects4J repositories weren't on this machine.

**Graph walkers (gap 17, PRs #92 and #95).**
- **When they run:** when the orchestrator's tools fail. Code decides "failed": an error, a hang, nothing usable, or two fact-gate misses.
- **What they do:** query the graph themselves. They run the eight stall queries from the stalled team and follow `blocked_by` to the teams that block it.
- **Limits:** read-only, capped in time, rows and queries, and cached, so a rerun makes 0 queries.
- **On the fixture:** they find a cause in the upstream team that the stalled team's own context can't show.
- **On the real block:** six package stalls took about 0.5 s each, hit no caps, and stayed on their own team, since none has a cross-team blocker.

## 9. What didn't work, and why it's reported

- **Decision 1's rule couldn't be met by any option.** It's reported as a result, not hidden by moving the bar.
- **Label precision is 45% (9 of 20), against a bar of 80%.** The labels are SZZ's. The model learns from noisy positives, and its PR-AUC is measured against the same noise.
- **The package answer key changed after run 2,** from 1,160 edited lines to 1,205 resolved lines, for the reason in §6. pass-bars.md says so.
- **`owner_eta` gives no number on historical commits** (§6).
- **`age` is slightly negative on 8 rows** (train 6, val 1, test 1; lowest −1.94 days; none positive).
  - **Why:** history is walked in landing order, but the gap is measured in author dates, and a commit can land after one authored later (rebases, cherry-picks). No future data gets in.
  - **Handling:** the models read it as 0 (`train.py` `feature_matrix`, Rachna's decision of Nov 6).
  - **Left as is:** clamping in `data.py` waits for the next table rebuild, since changing it now would change the frozen table's md5 (`9c0ab220…`) and every number scored on it.
- **The packages' diff bar (90%) isn't met:** 73.2%, frozen. The misses are owners choosing differently line by line, not owners restructuring (14 of 244).
- **The LLM path drafts nothing on the real packages.** Its only lines need an import change in the same commit.
