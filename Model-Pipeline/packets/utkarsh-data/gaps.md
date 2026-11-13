# GAPS 24 and 13 (Nov 12)

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

**Still to do:** the rows appear in the next block that's built. The published block `c5118` isn't rewritten. Then Chaitali reruns `meridian.context` on it.

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
