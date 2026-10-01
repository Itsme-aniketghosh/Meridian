# Week 1 · Pair 2 review

Spark git + Apache Jira. Run 2026-09-30/10-01 on a fresh full clone (HEAD `f868de6914`) and a full Jira pull.
Scripts: [pair2_jira_pull.py](pair2_jira_pull.py) (download Jira), [pair2_build_dataset.py](pair2_build_dataset.py) (build the merged dataset).

## Verdict

**Strong. Go ahead.** This is our Jira half, and it's ready to build on.
- Commit → ticket links are near-complete and correct
- Bug history is large, and fragility predicts future bugs (11.7x lift)
- The changelog rebuilds any ticket's state at any date

**It doesn't prove** hidden cross-team blocks or stall reasons. Spark is one repo, and its tickets are mostly filed right before the fix. See [How optimistic](#how-optimistic-for-the-product).

## Can we use it? The numbers

| What it tells you | Need at least | We got | |
|---|---|---|---|
| Commits that name their ticket | 90% | **94%** | ✅ |
| Those links point to the right ticket (50 hand-checked) | 95% | **100%** (50/50) | ✅ |
| Commit keys that point to missing tickets | close to 0 | **3** in 45,312 commits | ✅ |
| Fixed bugs we can trace to a fix commit | 80% | **93%** | ✅ |
| Fixed bugs to learn from | 1,000s | **10,981** | ✅ |
| Tickets whose status we can rebuild at any past date | 100% | **100%** | ✅ |
| "Risky" files really break more often | clearly more than other files | **11.7x more** | ✅ |
| Duplicate labels that are real (not mass-closed) | most | **95%** | ✅ |
| Our owner guess beats the simple baseline | beats it | **20% vs 18%** | 🟡 barely |
| Tickets that sit open before work starts (needed for stalls) | a real share | **16%** wait 2+ weeks | 🔴 |
| Tickets with "blocks" links (needed for hidden blocks) | a real share | **2%** | 🔴 |

**How to read it:**
- **All ✅:** the data is solid for linking code to tickets, bug history and risk, ticket status over time, and duplicates. Use it for those
- **🟡 Ownership:** it works, but the signal is weak. Try owning by team (Jira component) instead of by person
- **🔴 Stalls and blocks:** this dataset can't show them. That isn't bad data; Spark just doesn't work that way. We need another source, or we show those two features on test fixtures only

**Short answer: yes, use it.** Just don't use it to prove stalls or cross-team blocks.

## How optimistic for the product

Scored against the 7 failures in [01-problem](../docs/01-problem.md):

| # | Failure | Spark proves it? | Evidence |
|---|---|---|---|
| 4 | Risky call sites look like the rest | ✅ **Yes** | 1.5% of files flagged risky get **17%** of next year's fixes. 74% of them are fixed again |
| 2 | Jira says done, code disagrees | ✅ Testable | Full changelog + links. 7% of Bug+Fixed tickets have no fix commit |
| 3 | Stale owners | 🟡 Barely | Our owner rule predicts the next author 20% of the time vs 18% for "last toucher". Passes, by 2 points |
| 5 | Hidden cross-team blocks | 🔴 No | One repo, 2% of tickets have block links. Only 37 collision candidates |
| 7 | Why it stalled | 🔴 No | 45% of tickets get their first commit within a day of filing. Spark tickets wrap ready PRs, so there's little "stalled" to find |
| 1, 6 | Late discovery, drift | ⚪ N/A | Needs a deprecation migration (Django, Pair 1) |

**Bottom line:**
- The **plumbing and the risk signal are proven.** Join, fragility, ticket state over time and duplicates all work on real data. This is a big chunk of the build order (steps 2–3)
- The **differentiators aren't proven yet.** Blocks (#5) and stalls (#7) are what "no tool covers". Neither Spark nor Pair 3's corpus shows them (Pair 3's repos don't call each other)
- That's the product's real risk, not the data quality. We need a dataset with long-open tickets and cross-team calls, or we demo #5 and #7 on fixtures and say so

## Merged dataset

`week1/data/pair2_spark.sqlite` (179 MB, not in git). Everything not pushed (Spark clone, raw Jira, results) lives in `week1/data/`, which is git-ignored. Rebuilds in **46 s** with `python3 pair2_build_dataset.py`. **Byte-identical on rebuild**, which meets our determinism rule.

| Table | Rows | One row per |
|---|---:|---|
| `commits` | 45,312 | first-parent commit (author, merged person, `[MINOR]` flag, size) |
| `commit_files` | 295,692 | file touched, with +/- lines |
| `commit_ticket` | 42,050 | commit → ticket link (subject first, body as fallback) |
| `tickets` | 59,492 | Jira ticket (type, **parent type**, component, **bulk-closed flag**) |
| `ticket_history` | 160,372 | status / resolution change, so "open at time t" is one query |
| `ticket_links` | 19,657 | Jira issue link |
| `people` / `people_emails` | 3,039 / 3,673 | merged human / alias table, hand-fixable |
| `commit_people` | 46,584 | author + `Co-authored-by` |
| view `bug_fix_commits` | 11,298 | commit linked to a Bug+Fixed ticket |

- Reproduces every number below (94.4% keyed incl. body, 853 tickets, 145 Bug+Fixed)
- Only 3 dead keys (keys not in Jira) in all of history
- People count includes co-author emails, so 3,039 here vs ~2,888 authors-only

## Friday numbers

| Number | Answer |
|---|---|
| % of Spark commits with a ticket ID | **94.0%** (last 1,000) |
| Bug + Fixed tickets | **145** of 853 linked tickets (17%) · **10,981** project-wide |
| Duplicate emails | **~490** (3,381 emails → ~2,888 people, 15%) |

## Useful for what

| Use case | Rating | Why |
|---|---|---|
| Ticket join (`commit_key`) | 🟢 Strong | 94% keyed, 0 dead keys, 50/50 hand-checked links correct |
| Duplicate detection | 🟢 Strong | 2,094 Duplicates with a link to the original, not bulk-closed |
| "Open at time t" (P3, P5) | 🟢 Strong | Full changelog on all 59,492 tickets, none truncated |
| SZZ / fragility (`bug_fixes`) | 🟢 Good | 10,981 Bug+Fixed. Only from 2015 on, see flags |
| Ticket checks (vague) | 🟡 OK | 141 real Incomplete + 513 Cannot Reproduce. Small but clean |
| Collisions (P3, chains) | 🟡 OK | 37 same-function pairs with both tickets open, since 2024. Needs a hand check |
| Ownership | 🟡 Weak signal | Beats "last toucher" only 20% vs 18%. Identity merge matters for history, not recent files |
| Cross-team blocking | 🔴 Weak | One repo. Only 2% of tickets have a block link. Components ≈ teams |
| Call-site recall | ⚪ N/A | That's Django (Pair 1) |

---

## The 5 questions

**1. Linkage: how many commits carry `SPARK-1234`?**
- **94.0%** of the last 1,000 (94.4% counting the body). 96–97% per year since 2021
- **0% before 2014** (pre-Apache era). Anything time-based starts in 2015
- The unkeyed 6% are almost all `[MINOR]`. Some are real fixes ("Validate array counts…", "Bound nesting depth…") that SZZ will never see

**2. How many are Bug + Fixed?**
- 1,000 commits → 853 tickets → **145 Bug+Fixed (17%)**, touched by 153 commits
- Ticket type is what cuts the count, not resolution: 99% of linked tickets are "Fixed"

| Type | Share of linked tickets |
|---|---|
| Sub-task | 45% |
| Improvement | 28% |
| **Bug** | **17%** |
| Other | 10% |

- Sub-tasks hide their nature. Use the parent's type (+5 bug sub-tasks here)
- Project-wide: **10,981** Bug+Fixed, 640 in the last 365 days

**3. Duplicate people**
- 3,381 author emails → **~2,888 people**. 356 people have 2–9 emails each
- **Those people wrote 75% of commits**, so ownership is badly off without merging
- No `.mailmap`. Generic `root@ip-…ec2.internal` addresses exist, so never merge on name "root"
- Last 1,000 alone: 152 emails, 8 duplicates. It's mostly a history problem

**4. Bots**
- **~0.** Spark merges through a script that keeps the human as author. No dependabot or github-actions
- 2 commits authored by an AI agent ("Claude", Holden Karau's bot). Treat as its human
- Watch instead: committer ≠ author (the merger is the committer). **Use author.** 13% of recent commits have `Co-authored-by`

**5. Pulling Jira**

| | |
|---|---|
| Auth | None, anonymous REST |
| Rate limits | None seen. 0 errors over 595 requests |
| Full pull with changelog | **35 min**, 59,492 tickets, 1.8 GB raw / 118 MB gzipped |
| Without changelog | ~9 min (1,000 per page, 8.5 s each) |

---

## Extra checks from [05-test](../docs/05-test.md) week-1 list

| Check | Result | Decides |
|---|---|---|
| Bulk-closed share of Duplicates | **5%** (136 of 2,828) | Duplicate labels usable ✅ |
| Bulk-closed share of Incomplete | **96%** (3,218 of 3,359) → 141 real | Confirms 03-data's 145 |
| Jira "open at time t" | Changelog complete for every ticket (max 1,228 entries) | P3, P5 testable ✅ |
| Collision pairs (fixes since 2024, same function, ≤ 30 days) | 142 pairs, **37 with both tickets open at once** | Chains testable, small N |
| Ticket link precision (50 hand-checked) | **50/50 correct ticket**. ~4 are follow-ups, not the main fix | Passes ≥ 0.95 bar |

## What to do now

0. **Find a dataset for blocks and stalls** (#5, #7), or agree they're fixtures-only for the demo. Biggest open risk, shared with Pair 3
1. **Alias table:** done (`people_emails`). Hand-review the biggest groups. Try owner by **component** (team-level). Person-level barely beats baseline
2. **SZZ:** 2015+ only. Sub-tasks take their parent's type
3. **Vague labels:** 141 Incomplete + 513 Cannot Reproduce. Drop anything with the `bulk-closed` label or in a mass-close batch
4. **Duplicate labels:** the 2,094 linked, non-bulk pairs
5. **Hand-check 50 collision pairs.** Drop generic names (`apply`, `__hash__`) first
6. **Decide:** Jira components (38, 98% tagged) as the "team" for the Spark demo
7. Update 03-data: 59,492 tickets, 6,046 open, 94% keyed on 1,000 commits (it says 95% on 100)

## Flags

- `[MINOR]` commits skip the ticket, and some are real fixes. Fragility undercounts
- Follow-up commits on a Bug ticket count as fixes too. One bug can become several "fixes"
- Jira links are thin: 21% of tickets have any link, 2% a block link. Not chain truth (agrees with dead-ideas)
- 1,955 tickets mass-closed in one hour (2019-05-21) and 740 more (2021-05-25). Exclude both batches from all labels
- SQL is 38% of tickets. A "team" split by component is lopsided
- `SQLConf.scala` is one giant config object. Function-level matching there is meaningless, so it's excluded

## Caveats

- Identity merge is a heuristic (name + email local part). Not hand-verified
- Fragility and ownership previews: one time split (cut 2025-10-01 / 2026-04-01), source files only, tests excluded
- "Owner = next author" is a hard target in open source (many one-off contributors). An internal company repo should score higher
- Collision functions come from git hunk headers, not an AST. Treat as candidates
- Link check is 50 samples from the last 1,000 commits only
