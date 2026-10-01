# Pair 2 · week 1

Spark git + Apache Jira. Snapshot **2026-10-01 01:23 UTC**: Spark at `f868de6914` (its commit time), and Jira cut back to the same moment (59,491 tickets). Every number below is printed by a script in this folder.

## Verdict

- **Strong. Go ahead.** This is our Jira half, and it's ready to build on.
- Commit → ticket links are near-complete and correct. Bug history is large, and fragility predicts future bugs (9.7x).
- It can't prove hidden cross-team blocks or stall reasons. Spark is one repo, and its tickets are mostly filed right before the fix.

## Can we use it? The numbers

| What it tells you | Need at least | We got | |
|---|---|---|---|
| Commits that name their ticket | 90% | **94%** | ✅ |
| Those links point to the right ticket ([50 hand-checked](merged/links_hand_check_50.csv)) | 95% | **50/50**, consistent with ≥ 95% (lower bound 93%) | ✅ |
| Commit keys that point to missing tickets | close to 0 | **3** in 45,312 commits | ✅ |
| Fixed bugs we can trace to a fix commit | 80% | **93%** (N=9,440, since 2015) | ✅ |
| Fixed bugs to learn from | 1,000s | **10,980** | ✅ |
| Tickets whose status we can rebuild at any past date | 100% | **100%** | ✅ |
| "Risky" files really break more often | clearly more than other files | **9.7x more** (N=2,462 files, 54 risky) | ✅ |
| Duplicate labels that are real (not mass-closed) | most | **95%** | ✅ |
| Our owner guess beats the simple baseline | beats it | **20% vs 18%** (N=935 files) | 🟡 barely |
| Tickets that sit open before work starts (needed for stalls) | a real share | **16%** wait 2+ weeks (N=8,836) | 🔴 |
| Tickets with "blocks" links (needed for hidden blocks) | a real share | **2%** | 🔴 |

**How to read it:**
- **All ✅:** the data is solid for linking code to tickets, bug history and risk, ticket status over time, and duplicates. Use it for those
- **🟡 Ownership:** it works, but the signal is weak. Try owning by team (Jira component) instead of by person
- **🔴 Stalls and blocks:** this dataset can't show them. That isn't bad data; Spark just doesn't work that way. We need another source, or we show those two features on test fixtures only

## Product fit

Against the 7 failures in [01-problem](../../docs/01-problem.md):

| # | Failure | Spark proves it? | Evidence |
|---|---|---|---|
| 4 | Risky call sites look like the rest | ✅ Yes | 2.2% of files flagged risky get 21% of next year's fixes. 40 of 54 break again |
| 2 | Jira says done, code disagrees | ✅ Testable | Full changelog + links. 7% of Bug+Fixed tickets have no fix commit |
| 3 | Stale owners | 🟡 Barely | Owner rule 20% vs "last toucher" 18% at predicting the next author |
| 5 | Hidden cross-team blocks | 🔴 No | One repo. 2% of tickets have block links. 37 collision candidates |
| 7 | Why it stalled | 🔴 No | 45% of tickets get their first commit within a day of filing |
| 1, 6 | Late discovery, drift | ⚪ N/A | Needs a deprecation migration (Django, Pair 1) |

The plumbing and the risk signal are proven. The differentiators (#5, #7) aren't, here or in Pair 3's corpus. That's the product's real risk, not the data.

## Flags

- `[MINOR]` commits skip the ticket, and some are real fixes. Fragility undercounts.
- Follow-up commits on a Bug ticket count as fixes too. One bug can become several "fixes".
- Committer ≠ author: Spark's merge script makes the merger the committer. Always use author.
- 330 people have 2+ emails each and wrote 65% of commits. No `.mailmap`. Never merge on generic names like `root`.
- Mass closes: 1,955 tickets in one hour (2019-05-21) and 740 more (2021-05-25). Exclude both from all labels.
- 0% of commits before 2014 carry a key. Anything time-based starts in 2015.
- SQL is 38% of tickets, so a "team" split by component is lopsided.

## Impact on [05-test](../../docs/05-test.md)

| Test | Change |
|---|---|
| Ticket links, hand-check 50 | 50/50, consistent with ≥ 0.95 but can't prove it (lower bound 0.93). Check 100 to prove it. 4 are follow-ups |
| Ownership vs last toucher | Passes, by 2 points. Try component-level owners |
| SZZ, fragility | 2015+ only. Sub-tasks take their parent's type |
| Ticket checks | 141 real Incomplete + 513 Cannot Reproduce. Drop bulk-closed |
| Duplicates | 2,094 linked, non-bulk Duplicates as labels |
| Week-1: bulk-closed share of Duplicates | 5%. Labels usable |
| Week-1: Jira "open at time t" | Changelog complete on every ticket. P3, P5 testable |
| Week-1: collision pairs in Spark | 142 same-function pairs, 37 with both tickets open. Small N |
| Blocking, chains | Not from Jira links (2% have one). Fixtures, unless we find another dataset |

## Next

1. Find a dataset for blocks and stalls (#5, #7), or agree they're fixtures-only. Shared with Pair 3.
2. Hand-review the biggest identity groups in `people_emails`.
3. Hand-check 50 collision pairs. Drop generic names (`apply`, `__hash__`) first.
4. Decide: Jira components (38, 98% tagged) as the "team" for the Spark demo.
5. Update 03-data: 59,491 tickets, 6,046 open, 94% keyed on 1,000 commits (it says 95% on 100).
6. Hand-check 50 more links, to prove link precision ≥ 0.95.

---

## Spark git

**Ticket keys (Q1)**
- 94.0% of the last 1,000 commits (94.4% counting the body). 96–97% per year since 2021.
- The unkeyed 6% are almost all `[MINOR]`.
- Hand check: 50 seeded random links, all 50 point to the right ticket. Verdicts: [links_hand_check_50.csv](merged/links_hand_check_50.csv), checked by [links_check.py](merged/links_check.py).

**People (Q3)**
- 3,381 author emails → 2,926 people (455 duplicates, 13.5%). Merged by name + email local part, not hand-verified.
- Recent commits barely suffer (last 1,000: 8 duplicates). It's mostly a history problem.

**Bots (Q4)**
- ~0. The merge script keeps the human as author. No dependabot or github-actions.
- 2 commits by an AI agent (a committer's bot). Treat as its human.
- 63% of recent commits have a committer ≠ author.
- 11% of recent commits carry `Co-authored-by`, but most name AI coding tools or the author's own other email. 39 (4%) credit a different co-author.

**Collisions** ([collisions.py](spark/collisions.py))
- Bug+Fixed commits since 2024 (1,226) that edit the same function within 30 days: 142 pairs, 37 with both tickets open at once.
- Functions come from git hunk headers, not an AST. `SQLConf.scala` is excluded (one giant config object).

## Apache Jira

**Bug + Fixed (Q2)**
- 1,000 commits → 853 tickets → 144 Bug+Fixed (17%), touched by 152 commits. 10,980 project-wide.
- One more (SPARK-59831) has its fix commit in the snapshot, but Jira resolved it 2.5 minutes later, so at the snapshot it's still open.
- Type does the filtering, not resolution (99% of linked tickets are "Fixed"). 45% are Sub-tasks, 28% Improvements.

**Pull (Q5)** ([pull.py](jira/pull.py))

| | |
|---|---|
| Auth | None, anonymous REST |
| Rate limits | None seen. 0 errors over 595 requests |
| Full pull with changelog | 35 min, 1.8 GB raw / 118 MB gzipped |
| Without changelog | ~9 min (1,000 per page) |

**Labels and links** ([friday.py](merged/friday.py), at the snapshot)

| | Total | Bulk-closed | Usable |
|---|---:|---:|---:|
| Incomplete | 3,359 | 96% | 141 |
| Duplicate | 2,828 | 5% | 2,094 with a link to the original |
| Cannot Reproduce | 534 | 4% | 513 |

- 21% of tickets have any link, 2% a block link.
- Changelog: complete on every ticket (up to 1,228 entries), none truncated ([analyze.py](jira/analyze.py)).

## Merged dataset

[build.py](merged/build.py) joins both into one SQLite file, `merged/data/spark_jira.sqlite` (179 MB, not in git). It takes 45 s, and a rebuild is byte-identical.

**Pinned in time.** Spark is read at `f868de6914`. Jira is pulled live, so build.py cuts it to that commit's time (`SNAPSHOT_AT`): later tickets and history are dropped, and status and resolution roll back to their value then. Type, component, labels, links and summary stay as of the pull.

| Table | Rows | One row per |
|---|---:|---|
| `commits` | 45,312 | first-parent commit (author, merged person, `[MINOR]` flag, size) |
| `commit_files` | 295,692 | file touched, with +/- lines |
| `commit_ticket` | 42,050 | commit → ticket link (subject first, body as fallback) |
| `tickets` | 59,491 | Jira ticket (type, parent type, component, bulk-closed flag) |
| `ticket_history` | 160,370 | status / resolution change, so "open at time t" is one query |
| `ticket_links` | 19,657 | Jira issue link |
| `people` / `people_emails` | 3,039 / 3,673 | merged human / alias table, hand-fixable |
| `commit_people` | 46,584 | author + `Co-authored-by` |
| view `bug_fix_commits` | 11,297 | commit linked to a Bug+Fixed ticket |

- [friday.py](merged/friday.py) prints the Friday numbers (Q1–Q4) and the Jira label counts.
- [preview.py](merged/preview.py) runs the product rules on it (coverage, fragility, ownership, ticket lifecycle). One time split: cut 2025-10-01 for fragility, 2026-04-01 for ownership. Fragility counts only files that existed before its cutoff.

## Rerun

```
# Spark: full clone (not blobless) into spark/repos/apache_spark. Scripts read commit f868de6914
git clone --no-checkout https://github.com/apache/spark spark/repos/apache_spark

# Jira: ~35 min, resumes if interrupted
python3 jira/pull.py && python3 jira/analyze.py

# Merged dataset, then everything that reads it
python3 merged/build.py
python3 merged/friday.py && python3 merged/preview.py && python3 merged/links_check.py
python3 spark/collisions.py   # writes .git/info/attributes into the Spark clone
```
