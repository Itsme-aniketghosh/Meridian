# GAPS 24, 13 and 17 (Nov 12)

## Gap 24: bug-fix history for the package repos

**Why it was empty.** `file_bug_fixes` counted a commit as a fix only if `commit_ticket` linked it to a Jira Bug ticket. Only Spark has a tracker in the block (42,191 `commit_ticket` rows, all `apache/spark`), so the 21 other repos had no fixes. And the window was the 365 days before the run's snapshot (2026-10-01), while every package is pinned at a 2017–2022 commit. Even with fixes, that window would have been empty.

**Fix** (`meridian/szz.py`, `[szz] fix_message` in `meridian.toml` and `meridian.next.toml`):
- A repo with no ticket tracker in the block reads its fixes from the commit subject: `Fixed #27258 -- …` (Django's convention), `fix …`, `closes #45`. It's still first-parent, non-merge, from `fix_since` on, and never a revert. The keys are the subject's `#N` references.
- A repo pinned at a commit older than the window is counted over the 365 days up to its own pinned commit, the last year it has.
- **Only `file_bug_fixes` uses it.** `bug_inducing`, the model's labels, keeps the ticket rule, because a subject word is too weak for labels.

**On the team block's own files** (preview: the same code over `out/c5118b019d642a9b/`):
- **Spark:** 1,492 rows, byte-identical to before. Its pin is recent, so its window is unchanged.
- **Packages:** every package now has rows: wagtail 138 files, allauth 78, cookiecutter 45, DRF 44, … haystack, tastypie and two-factor 0, since none of their fixes fell in their last year. **Django: 1,326 files.**
- **Coverage:** 115 of the 1,237 package call sites are in a file with fixes in its repo's last year. Before, it was 0.

**Tests:** `tests/test_szz.py` covers the subject rule (Django's `Fixed #N`, `closes #N`, reverts out, a tracked repo untouched) and a repo's own window.

**The block with gap 24: `bb6b5f4e32a4bee7` (built Nov 13).** It's `meridian.toml` as the team ran it (same pinned commits, same snapshot 2026-10-01T01:23:03Z, same ticket pulls) with main at `2b0606d`. Built under its own data root and not published: `published/latest.json` is still `c5118`. 19 minutes; validate: 0 errors, 2 warnings, 1,790,126 rows in 30 tables.

Against `c5118`, table by table:
- **Same bytes:** 21 of the 25 tables, `bug_inducing` among them, so the model's labels are unchanged.
- **`file_bug_fixes`:** 1,492 → 3,248 rows. Spark's 1,492 rows are identical. Django has 1,326, wagtail 138, allauth 78, cookiecutter 45, DRF 44, and so on.
- **`call_site`:** the same 1,526 sites. `bug_fixes` is now filled in for the packages and Django: 367 sites are above 0, where before all were 0. That's validate's second warning (24% of `bug_fixes` outside 0..0); the first (289 Spark tickets dated year 0010) was already in `c5118`.
- **`run`:** the new run_id and source hashes.
- **`commit_file`:** 849,643 rows against 849,648. In three 2010–2012 commits (django-cms, Django docs, an oscar merge), git now reads a moved file as one rename row, where `c5118` has a delete and an add. `history.py` didn't change, and this Mac's git reads both checkouts the same way today, so it's PLAN S8's machine difference (git rename detection), not gap 24. Those commits are years before any bug-fix window, so `file_bug_fixes` doesn't move.

### Rebuilding it (Chaitali, for `meridian.context`)

The block holds real names, so it isn't in git. Rebuild it from the caches the team zip gave you (`reproduce.py --zip`), with main at `2b0606d` or later. About 20–30 minutes, no network:

```bash
cd Data-Pipeline
git pull
OLD=<your data root, the folder whose snapshots/_cache holds repos, jira, gitlab, scan, resolve, szz>
export MERIDIAN_DATA=~/meridian-block3
mkdir -p $MERIDIAN_DATA/snapshots $MERIDIAN_DATA/out
cp -cR $OLD/snapshots/_cache $MERIDIAN_DATA/snapshots/      # macOS (instant); elsewhere cp -r
python -m meridian.collect --run-id new --config meridian.toml    # prints run_id=bb6b5f4e32a4bee7
for p in history scan resolve owners join graph validate; do
  python -m meridian.$p --run-id bb6b5f4e32a4bee7 --config meridian.toml
done
python -m meridian.context --run-id bb6b5f4e32a4bee7 --db $MERIDIAN_DATA/out/bb6b5f4e32a4bee7/meridian.sqlite
```

- **The run_id is the check:** collect should print `bb6b5f4e32a4bee7`. If it prints another, `Data-Pipeline` code or `meridian.toml` changed after `2b0606d`. Use the id it prints, and say so.
- **Byte check (md5 of `out/bb6b5f4e32a4bee7/…`):** `file_bug_fixes.jsonl` `a98e5d40d71d09d6642a211e606c91da`, `call_site.jsonl` `bf428a2fa11a19c9f0ad453574d17f27`, `bug_inducing.jsonl` `682abebd1cea5ef95b74009d82c91b9d`. `commit_file.jsonl` (`8a6dc499246b3671fa97127a7fda9c9a` here) can differ by git's rename detection (above), which doesn't change the first three.
- `validate` raises a warning alert with 0 errors; it isn't a failure.
- `publish` isn't run: the team's live block stays `c5118` unless Aniket says otherwise.

## Gap 13: a cleaner SZZ

What gap 13 asks to skip is mostly already skipped. `szz.py` blames with `git diff -w` and `git blame -w` (whitespace ignored), and skips blank lines, comment-only lines and test files. What it didn't skip is **moved and copied code**: a commit that only moved lines gets blamed as where the bug came from. The variant follows them back with `git blame -M -C` (`szz.MOVES`, `moves=True`). It's off in the pipeline: measured only, and the champion doesn't change.

**Spark's labels again with the variant** (all 9,757 fix commits, 5.6 minutes): of today's 22,007 fix → inducing pairs, 20,784 stay, 1,223 go (the commit only moved the line) and 2,460 are new (the line's real author, found past the move).

**Val PR-AUC, the champion forest's settings** (`candidate.json`: 200 trees, depth 8, leaf 5, seed 1), fit on train and scored on val:

| Labels | train positives | val positives | val PR-AUC |
|---|---:|---:|---:|
| today's SZZ (reproduces `candidate.json`) | 5,038 | 486 | 0.4058 |
| following moves and copies | 5,023 | 470 | **0.4002** |

**Result: no gain.** It's −0.006, and both the labels and the answer key move together, so the two numbers aren't strictly comparable. Following moves changes which commit gets blamed for about 1 in 10 pairs, but not how many commits are positive, or how predictable they are from the features. The champion and the pipeline stay as they are.

**Defects4J pick rate (against 44%): not measured.** It needs the Defects4J project repositories, which aren't on this machine. Week 1 ran it from a Docker image built from a Defects4J clone (`week1/pair3/defects4j/runall.sh`). Once they're here it's one command each, today's rule and the variant: `python -m meridian.szz_check --run-id … --d4j <dir>` and the same with `--moves`.

**Tests:** `tests/test_szz.py::Moves`: a line written in one commit, moved in a second and fixed in a third is blamed on the second today and on the first with the variant.

## Gap 17: graph walkers

**Why.** A stalled team's context (`meridian.context`) holds only its own files. A cause somewhere else, like the team that blocks it, never reaches the writer. When the orchestrator's tools fail, it needs something that looks further.

**What** (`meridian/walkers.py`):
- `walk` starts at the stalled team and runs KNOWLEDGE-BASE.md's eight `stall_*` queries as of the stall's moment (the auditor's `point`). It follows `blocked_by` to each blocking team, up to 2 hops, and returns what it finds as a Contract 5 facts object for the writer. The writer's fact gate still checks every text.
- `failed` decides "the tool failed" by code: it raised or hung, it returned nothing usable, or the writer's text failed the gate twice. `fallback(tool, …)` runs the tool, gives up on it after `tool_seconds` without waiting for it, and calls the walkers when it failed.
- **Guardrails:**
  - the block is opened read-only;
  - each query is capped at 200 rows and 2 s;
  - 24 query lookups and 3 teams per stall;
  - only named KNOWLEDGE-BASE.md queries, never SQL written on the fly;
  - every result is cached by (run_id, moment, repo, symbol, query), so a rerun makes 0 queries;
  - no person is a fact (`stall_orphaned_owner`'s person id is dropped).

**Done when (GAPS 17), `tests/test_walkers.py`:** on the fixture, fulfillment is stalled by `S_shared`, which platform owns. With the context builder made to fail, the walkers walk fulfillment → platform and find platform's test gap on `shared.client`, which is outside fulfillment's files. A rerun with the same cache makes 0 queries and gives the same facts. The tests also cover failure by code, a hung tool, every cap, the read-only block and no person in the facts.

**On the real block:** six stalls in the package repos took about 0.5 s each, hit no caps, and reran with 0 queries. None of the six has a cross-team blocker, so each walk stayed on its own team.

**Left for Aniket (gap 15):** wiring `fallback` into the orchestrator, with the context builder as the tool.
