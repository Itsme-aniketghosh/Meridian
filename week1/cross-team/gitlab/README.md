# Cross-team · GitLab (gitlab-org/gitlab)

**The question:** can GitLab's own public data be used to test Meridian? This is step 1 of [NEXT-STEPS](../NEXT-STEPS.md), on a source neither research report picked.

**The data:**
- Pulled on 2026-10-02 from gitlab.com: the public website API, plus a copy of the code history.
- We only read data; nothing was changed.
- Every number below comes from a script in this folder.
- Every "was it really waiting?" answer comes from reading the issue's full history.

## The short answer

- **Yes, GitLab is worth using.** It's the only dataset we found for two of Meridian's 7 problems:
  - **#5, teams blocking each other:** 30 real cases. No other dataset got past 4.
  - **#6, adding new uses of old code while others remove them:** clear examples, over 2 years.
- **It's also the best for #7, why work got stuck:** 579 stuck issues, and people usually wrote down why.
- **It's weaker for #2, #3 and #4** (closed-but-not-done tickets, stale owners, risky files). Other datasets do those better.
- **It can't test #1**, finding out late about removed code. GitLab is written in Ruby, and our code tools only read Python.
- **One warning:** more than half the issues (54%) are written by a robot that reports failing tests. We removed them. All numbers below are for the **14,668 issues written by people**.

## What GitLab is

- **GitLab** is a company that makes software for teams to store code and track work.
- It builds its own product in **one big shared code base**: `gitlab-org/gitlab`.
- **About 100 teams** work in that one code base. Each team has a label, like `group::source code` or `group::database`.
- **Each piece of work is an "issue"** (a ticket).
- **When a team gets stuck**, it puts the label `workflow::blocked` on the issue. It often adds a link: "blocked by #1234".
- **For Meridian:** this is close to the real situation Meridian is built for: many teams, one shared code base, and tickets.

## What we did

1. **Downloaded every issue** created January to June 2025: 32,166 issues.
2. **Removed the robot's issues.** 17,502 came from one test-reporting bot, which left 14,668 written by people.
3. **Downloaded the full label history** of every issue. That shows exactly when each issue was marked "blocked" and when it was un-marked.
4. **Read 57 stuck issues by hand,** picked at random, to see if they were really stuck and why.
5. **Searched for teams waiting on other teams.**
   - A script picked the 126 most likely issues.
   - We read every one.
   - We kept only the ones that passed all 3 of the team's tests.
6. **Checked the other Meridian problems too:** owners, risky files, closed tickets, deprecations, and adding old code.

## The results

| Question | What we found | Good enough? |
|---|---|---|
| How many issues were ever marked "blocked"? | **579** (about 4 in 100) | ✅ |
| Were they really stuck? (57 read by hand) | **39 yes**, 6 partly, 5 no, 7 unclear | ✅ |
| Did someone write down why? | **50 of 57** | ✅ |
| How long were they stuck? | Half were stuck **26 days or more**. 97 were stuck over 3 months | ✅ |
| Do stuck issues finish late? | **Yes.** 36% of stuck issues missed their deadline, against 16% of the rest | ✅ |
| How long until work even starts? | Half waited **15 days or more** before anyone started | ✅ |
| Were any stuck because of **another team**? | **30 confirmed** (need 30) | ✅ just |
| Were the "blocked" labels added by people, not robots? | **Yes.** 0 of 57 by a robot | ✅ |
| Are the "blocked" dates accurate? | **Only 34 of 57.** People often forget to remove the label | 🟡 |

## Why issues got stuck (57 read by hand)

| Reason | How many | Example |
|---|---:|---|
| **Waiting on other work** ("upstream") | 41 | Waiting for another issue to finish first. 5 of these were another team's work |
| **No reason written** | 7 | Nobody explained |
| **Planned wait** | 6 | "Wait until the next release" |
| **Waiting for a decision** ("owner") | 2 | A product manager had to decide |
| **Too risky** | 1 | A new feature broke customers, so it was paused |
| **No time** ("capacity") | 0 | Nobody said "we're too busy" |

**What we learned:**
- **People forget to remove the "blocked" label.** 66 finished issues still say "blocked". So don't trust the label alone. Use whichever comes first: the label being removed, the status changing, or the blocking issue finishing.
- **"Blocked" sometimes really means "we stopped caring".** The issue was moved to the backlog and nobody was assigned.
- **Some real blocks never got the label.** So the label misses some.
- **People delete "blocked by" links when work is done.** We used the issue's history, which keeps the deleted ones.

## Teams blocking other teams (problem #5)

**The problem:** only about 1 stuck issue in 11 is caused by another team. Reading at random, we'd need to read about 350 issues to find 30.

**What we did instead:**

```
579 stuck issues
   │
   ▼  Step 1: a script picks the likely ones. It keeps an issue if:
   │   • the issue blocking it belongs to a different team, or is in another team's project, or
   │   • a comment says "waiting" AND names another team
   ▼
126 likely issues
   │
   ▼  Step 2: read every one, and keep it only if ALL 3 are true:
   │   1. the work it waited on belongs to a DIFFERENT team
   │   2. it really stayed stuck until that team finished
   │   3. someone WROTE that they were waiting
   ▼
25 confirmed  +  5 from the earlier random read  =  30
```

- **Is the script any good?** It found 4 of the 5 cases we already knew about.
- **Results of reading the 116 new ones:**
  - **25** confirmed
  - **43** maybe (another team, but weak proof)
  - **45** turned out to be the same team
  - **3** weren't really stuck

**Who were teams waiting on? (all 30)**

| Waiting on | How many | Example of what someone wrote |
|---|---:|---|
| The database team | 6 | "blocked until we get the OK to perform the necessary database migrations" ([543818](https://gitlab.com/gitlab-org/gitlab/-/issues/543818)) |
| Another product team | 8 | Waiting for code review, package registry, editor extensions, and others to finish their part ([526771](https://gitlab.com/gitlab-org/gitlab/-/issues/526771)) |
| The infrastructure team (servers, monitoring) | 4 | "input and collaboration with the Observability team is required" ([520343](https://gitlab.com/gitlab-org/gitlab/-/issues/520343)) |
| The Git storage and runner teams | 4 | "we'll need [gitaly#6917] to be done for this" ([550474](https://gitlab.com/gitlab-org/gitlab/-/issues/550474)) |
| The design system team | 3 | "wait until design.gitlab.com#1555 is complete" ([515448](https://gitlab.com/gitlab-org/gitlab/-/issues/515448)) |
| The AI team | 1 | [521252](https://gitlab.com/gitlab-org/gitlab/-/issues/521252) |
| A non-engineering team (Product, Legal, Quality) | 3 | Waiting for approval ([517640](https://gitlab.com/gitlab-org/gitlab/-/issues/517640)) |
| An outside company (Amazon) | 1 | [531380](https://gitlab.com/gitlab-org/gitlab/-/issues/531380) |

- **26 of the 30** are one team waiting on another team's **shared code or system**. That's exactly Meridian's problem #5.
- Half waited **21 days or more**.

**Why the script picked wrong ones** (so the next person knows):
- The "other team" was really the **same person** who split their work into two issues.
- The "other team" was a **sister team with the same people**, or an **old name** of the same team.
- The comment only **mentioned** another team. It didn't say they were waiting on them.
- The team **found a workaround** instead of waiting.

## How GitLab compares with the other datasets

| | Debian | Mozilla | OpenStack | Home Assistant | **GitLab** |
|---|---|---|---|---|---|
| "Blocked" links point the right way | ❌ backwards | ✅ | — | ❌ not used | ✅ |
| Links added one by one by people | ❌ one person added all at once | ✅ | — | — | ✅ |
| Waits checked by reading | ❌ word search, 0 of 13 | ✅ 3 of 7 | ❌ word search | ✅ 40 | ✅ **57 + 116** |
| **Real waits on another team** | 0 | 3–4 | 0 | 0 | **30** |
| **Stuck work with a written reason** | — | 1 | 0 | 32 of 40 | **50 of 57** |

## The other Meridian problems

| # | Problem (in plain words) | Does GitLab show it? | What we found |
|---|---|---|---|
| 6 | **New uses of old code get added while other teams remove them** | ✅ **Yes** | GitLab keeps a list of files that still use each banned coding pattern. **320 of 351** lists got shorter (teams cleaned up), but **51** lists got new files added in 3+ different months (others added more). One list: 1,112 files removed and 208 added over 27 months |
| 4 | **Some files break much more often than others** | ✅ Yes, but… | Files with 3+ bug fixes in a year got **8.5 times** their share of next year's bug fixes. **But:** simply picking the most-edited files works just as well. So bug history doesn't add anything here |
| 2 | **A ticket says "done", but the code isn't** | 🟡 A little | 36 issues closed **before** the fix was in. 25 still open **after** the fix was in. 6 of 20 "done" bugs were never fixed: a robot closed them for being old |
| 3 | **The listed owner of the code is out of date** | 🟡 A little | GitLab lists big groups as owners, not people. Guessing "who changes this next" was about as good as "whoever changed it last" (16.3% vs 15.6%). On 43% of files, none of the listed team owners had touched the file in a year |
| 1 | **Teams find out too late that code they use is being removed** | 🔴 Not really | GitLab has a clean list of 378 planned removals with dates. But the code is Ruby, so our tools can't find who uses it |

**Other facts:**
- **76%** of code changes name their issue (Spark: 94%).
- **99%** of commits link to their code change.
- **About 7%** of people use two different emails.
- **About 7%** of commits are made by robots.

## Things to be careful about

- **These were read by Claude, not a person.** The 57 stuck issues, the 116 cross-team checks and the 20 closed bugs were judged by Claude reading each issue's history. We double-checked a few quotes against the real comments, and they were all correct. **A person should still check about 5 of each.**
- **30 is exactly the minimum.**
  - A strict reviewer might remove a few.
  - 4 of the 30 aren't engineering teams.
  - The 43 "maybe" cases could add more after a person reads them.
- **These are blocks people noticed.** Meridian wants to find blocks people **don't** know about. Every one of the 30 was written down by someone, so it shows the problem is real and costly. It doesn't test whether Meridian can find hidden ones from the code. That part still needs made-up test cases.
- **"Team" means the `group::` label.** A team without a label counts as unknown.
- **Robots look like people sometimes:**
  - A test robot wrote 54% of the issues.
  - Another robot closed 10,624 issues.
  - The "Status" field says "Complete" on 98% of closed issues no matter what, so it's useless.
- **Some data needs a login token** (label history and links). Most doesn't.

## What to do with it

- **#5, teams blocking each other:** use the 30 confirmed cases as the team's real examples, after a person checks about 5. Read the 43 "maybe" cases to grow the set.
- **#7, why work got stuck:** use the 57 hand-read issues as real examples. Fix the forgotten-label dates first.
- **#6, adding old code:** use GitLab's banned-pattern lists. Next, check how often the adds come from a *different* team than the removes, compared with chance.
- **#4, risky files:** tell the team that "most-edited files" works as well as "most-bug-fixed files". Add that comparison to the test plan (05-test).

## How to run it again

Python 3.9+. Run from this folder:
- Steps marked **(token)** need a GitLab read-only token saved in `~/.gitlab_token`.
- Downloads go into this folder and into `data/`, which git ignores. Run `mkdir -p data/tix data/code` first.

```
# 1. Download issues and who wrote them (no token)
python3 pull.py 2025-01-01T00:00:00Z 2025-07-01T00:00:00Z && python3 authors.py && python3 analyze.py

# 2. Label history for every issue (token, ~27 min), then the stuck-issue numbers
python3 labels_all.py && HUMAN=1 python3 analyze_all.py

# 3. The 1,000-issue sample and the 3 ways to spot "blocked"
python3 notes.py && python3 labels.py && python3 analyze_notes.py && python3 analyze_labels.py && python3 methods.py

# 4. Pick and read the 40 random stuck issues (token)
python3 pick_40.py && python3 fetch_issues.py sample_blocked_40.txt issues40.json
REC=issues40.json python3 timeline.py <issue number> ...

# 5. Teams blocking teams: download comments (token), filter, make reading batches
python3 cross_pull.py && python3 cross_filter.py && python3 cross_dump.py cross_toread.txt 15

# 6. The other problems
python3 tix_pull_closing.py && python3 tix_pull_rest_issues.py && python3 tix_pull_related.py 5
python3 tix_analyze_done.py && python3 tix_mr_ticketkey.py && python3 tix_analyze_mr.py
python3 tix_deprecations.py && python3 tix_analyze_deprecations.py && python3 tix_labels_table.py
sh code_run_all.sh      # copies GitLab's code history (1.2 GB) into data/code, ~35 min the first time
```

| Files | What they do |
|---|---|
| `pull.py`, `authors.py`, `analyze.py` | Download the issues and count labels |
| `labels_all.py`, `analyze_all.py` | Label history; how long issues were stuck |
| `notes.py`, `labels.py`, `methods.py`, `analyze_notes.py`, `analyze_labels.py` | The 1,000 sample and the 3 ways to spot "blocked" |
| `pick_40.py`, `fetch_issues.py`, `fetch17.py`, `timeline.py` | Pick issues and print their history for reading |
| `cross_pull.py`, `cross_filter.py`, `cross_dump.py` | Find and prepare likely team-blocks-team issues |
| `tix_*.py` | Closed-but-not-done, code changes naming issues, planned removals |
| `code_*.py`, `code_run_all.sh` | Owners, risky files, and adding old code (from the code history) |

**Result sheets (CSV), kept on Vishwa's machine only and not in git:**
- `hand_check_17.csv`, `hand_check_40.csv`: the 57 stuck issues read by hand
- `cross_hand_check_116.csv`: the team-blocks-team reading, with the 25 confirmed
- `tix_closed_no_mr_20.csv`: 20 closed bugs read by hand

Ask Vishwa for them.

**In git:**
- `labels_table.md`: ticket counts
- `sample_1000.txt`, `sample_blocked_40.txt`, `cross_toread.txt`: the issue lists, so anyone gets the same samples
