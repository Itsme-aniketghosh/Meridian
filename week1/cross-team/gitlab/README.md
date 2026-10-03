# Cross-team · GitLab (gitlab-org/gitlab)

Step 1 of [NEXT-STEPS](../NEXT-STEPS.md), on a source neither research report picked. Queried 2026-10-02 from gitlab.com's public API (GraphQL anonymous, REST with a `read_api` token) and a blobless clone of the repo. All read-only. Every number below is printed by a script in this folder. The "real wait?" calls are a hand read of each issue's full history.

## Verdict

- **Is it cross-team? The teams are. The blocks mostly aren't.**
  - GitLab is one repo shared by about 100 teams (`group::` labels).
  - 579 human-filed issues were blocked at some point, and the links point the way Meridian's `blocked_by` does.
  - But only **5 of 57** hand-read blocks waited on another team. The rest waited on an issue in their own team.
- **#5 (blocks): short of the bar, but the closest of our sources.**
  - 5 real cross-team waits in 57 is about 9%.
  - Applied to all 579, that suggests about 50 in six months, roughly 20 to 100 given N = 5.
  - Mozilla found 3–4, and Debian and OpenStack found 0.
  - A filtered hand read of the 579 could reach 30.
- **#7 (stalls): usable. This is the strongest source we have.**
  - 39 of 57 were really stuck, and 50 of 57 have a written reason.
  - Blocked spells run a median 26 days, and 97 lasted 90+ days.
  - Blocked issues miss their milestone 36% of the time, vs 16% for the rest.
- **Filter first.** 54% of issues in the window are bot-filed flaky-test reports, and one person bulk-closed 5,143 of them in 4 hours. Every rate below is for the 14,668 human-filed issues.

## What it is

- **GitLab** builds its product in one large repo, `gitlab-org/gitlab` (Ruby on Rails + Vue).
- **Teams** are `group::` labels, about 100 of them (Source Code, Code Review, Geo, …), each with its own engineers and managers.
- **Issues** live in the same project. A team marks an issue stalled with the scoped label `workflow::blocked` (or, since 2025, Status **Blocked**). It links the blocker with "blocked by #N".
- **In Meridian terms:**
  - repo = monorepo
  - `group::` = team
  - issue = ticket
  - "blocked by" = `blocked_by`
- So this tests team boundaries inside a monorepo, like Mozilla, not chains between repos.

## What we did

1. **Pulled every issue created 2025-01-01 to 2025-07-01:** 32,166 issues with labels and blocking links, plus each issue's author (anonymous GraphQL, about 12 min).
2. **Pulled the label history of every issue** (REST with a token, about 27 min). That gives each `workflow::blocked` spell its start and end.
3. **Dropped the flaky-test bot.** 17,502 issues come from one bot account and are all "[Test] spec/…" reports. 14,668 are human-filed.
4. **Compared three ways to spot "blocked"** on a seeded 1,000 sample:
   - a block link
   - the Status field
   - the label
5. **Hand-read 57 blocked issues:**
   - 17 from the 1,000 sample, plus 40 seeded (`random.seed(2)`) from all 579.
   - Each was read in full: description, comments, and label, milestone and state history.
   - Coded with the NEXT-STEPS reasons (`upstream`, `owner`, `risk`, `capacity`, `other`, `unknown`).
   - Cross-team = the thing waited on belongs to a different `group::`, or a comment names another team.
6. **Also checked the other five failures** (ownership, risky files, closed-vs-done, deprecations, adds-while-removing) on the tickets and the git history. See [Beyond blocks and stalls](#beyond-blocks-and-stalls).

## The numbers

| What it tells you | Need | We got | |
|---|---|---|---|
| Human-filed issues | 100s | **14,668** of 32,166 (the rest is one test bot) | ✅ |
| Issues ever `workflow::blocked` | 30+ | **579** (3.9%) | ✅ |
| Block links on issues now | a real share | **1,099** of 32,166 (3.4%). Spark: 2% | 🟡 |
| Labels added by hand | most | **603 of 618** adds by a person. **0 of 57** sampled by a bot. One bulk day (15 issues) | ✅ |
| Links point the way Meridian's do | yes | **Yes.** The waiting issue says "blocked by" its blocker | ✅ |
| Blocked issues really stuck (hand read) | most | **39 of 57** y, 6 partly, 5 n, 7 unclear | ✅ |
| Reason written down | most | **50 of 57** | ✅ |
| Real waits on another team | 30+ | **5 of 57** (9%). About 50 across the 579, unchecked | 🔴 / 🟡 |
| Blocked-by links that cross `group::` | a real share | **11 of 67** | 🔴 |
| Blocked spell length | long enough to matter | median **26 days**, p75 76, p90 181. **97** at 90+ days (N = 462 closed spells) | ✅ |
| Blocked → missed milestone | higher than the rest | **36%** (208 of 579) vs 16% (2,306 of 14,089) | ✅ |
| Wait from filing to first `in dev` | not same-day (Spark: 45% within a day) | median **15 days**, 52% wait 2+ weeks (N = 3,473) | ✅ |
| Spell dates match the real wait | most | **34 of 57**. The label is often left on | 🟡 |

**How to read it:**
- **✅ The stall signal is real, labelled by people, and points the right way.** The reasons are in the comments.
- **🔴 Cross-team waits are rare in any one sample,** but the population is big enough that a targeted read might reach 30.

## Three ways to spot "blocked"

Seeded 1,000 sample ([methods.py](methods.py)):

| Method | Token? | Issues |
|---|---|---:|
| Block link ever ("marked this issue as blocked by") | No | 59 |
| Status set to **Blocked** | No | 7 |
| `workflow::blocked` label | Yes | 16 |

- **Status and label are one signal.** 6 of the 7 overlap.
- **Link and label are different things.** Only 9 issues have both: a link names the blocker, and the label marks the stall.
- **Use the label or Status for #7 and the link for #5.** Any one alone misses most of the 67.

## The 57 hand-read issues

[hand_check_17.csv](hand_check_17.csv), [hand_check_40.csv](hand_check_40.csv)

| Reason | Issues | Detail |
|---|---:|---|
| upstream | 41 | 35 waiting on another issue or MR in the same team · **5 on another team** · 1 deprioritized |
| unknown | 7 | No reason anywhere |
| other | 6 | Planned schedule or release wait (4), not a block (1), deprioritized (1) |
| owner | 2 | Product decision |
| risk | 1 | Flag rollout broke customer workflows |
| capacity | 0 | Nobody said "no time" |

**The 5 cross-team waits:**

| Issue | Team | Waited on | Evidence |
|---|---|---|---|
| [552111](https://gitlab.com/gitlab-org/gitlab/-/issues/552111) | runner core | Database team | Index-limit exception, "Addressed in database-team/team-tasks#522". 3 days |
| [540976](https://gitlab.com/gitlab-org/gitlab/-/issues/540976) | secrets manager application | Distribution (Runway) | "blocked by … distribution/team-tasks#1755", unblocked when promoted to an epic |
| [525085](https://gitlab.com/gitlab-org/gitlab/-/issues/525085) | policy management | devops::verify | "we need to clarify with … ~devops::verify approach". 1 month |
| [537059](https://gitlab.com/gitlab-org/gitlab/-/issues/537059) | secrets manager application | secrets manager OpenBao | Waited on OpenBao metadata CAS (#538174). Moved to in dev "since it's no longer blocked". 54 days |
| [515448](https://gitlab.com/gitlab-org/gitlab/-/issues/515448) | optimize | Design system (design.gitlab.com) | "wait until design.gitlab.com#1555 is complete". Still blocked |

**Patterns:**
- **The label goes stale.**
  - 15 of 57 spells have the wrong dates.
  - 66 closed issues still carry `workflow::blocked`.
  - 91 open issues have been "blocked" a median of 510 days, mostly abandoned.
  - End a spell at the earliest of: label removed, Status changed, blocker closed.
- **"Blocked" is sometimes "deprioritized"** (moved to Backlog and unassigned the same day), or a planned wait.
- **Real blocks go unlabelled.** [513546](https://gitlab.com/gitlab-org/gitlab/-/issues/513546) had two real blocks, a QA test and #535230, and neither got the label.
- **Links get deleted when work is done.** 59 sampled issues ever had one, and only 32 still do. Count from the activity log, not the current links.

## How it compares

| | Debian | Mozilla | OpenStack | **GitLab** |
|---|---|---|---|---|
| Links point Meridian's way | 🔴 reversed | ✅ "blocks" list | — | ✅ |
| Links added one by one | 🔴 bulk, one person | ✅ | — | ✅ |
| Waits read by hand | keyword, 0 of 13 | ✅ 3 of 7 | keyword | ✅ **57 read** |
| Real cross-team waits | 0 | 3–4 | 0 | **5** (about 50 projected) |
| Stalls with a written reason | — | 1 | 0 | **50 of 57** |

## Beyond blocks and stalls

We checked the other failures from [01-problem](../../../docs/01-problem.md) too, the same way Pair 2 checked Spark:

| # | Failure | GitLab? | Evidence |
|---|---|---|---|
| 6 | Adds while others remove | ✅ Yes | RuboCop todo lists: **320 of 351** shrank, yet **51** got files added in 3+ separate months. 602 of 647 adding commits are small feature MRs |
| 4 | Risky files | ✅ With a catch | Files with 3+ bug fixes get **8.5x** their share of next year's fixes (21,238 files, 616 risky). Churn alone gets the same (25.7% vs 24.6%) |
| 2 | Ticket says done, code disagrees | 🟡 Partly | 36 closed before the fix merged, 25 open after it merged. 6 of 20 hand-read "done" bugs were never fixed (bot auto-close). The closing link covers 6.5%; related MRs bring it to 69% |
| 3 | Stale owners | 🟡 Partly | Owner rule 16.3% vs last toucher 15.6%. CODEOWNERS is approver groups. Team sections stale on 43% of files |
| 1 | Consumers find out late | 🟡 Tickets only | 378 curated deprecation records, median 4 milestones to removal, 2% slip. Ruby, so no resolver |

Other numbers:
- **Ticket key:** 76% of merged MRs name an issue (Spark 94%).
- **Commits:** 99% point to their MR.
- **Identities:** 6.9% are duplicates.
- **Bots:** 6.7% of commits.

## Caveats

- **Checked by Claude, not by a person.** The 57 stall rows and 20 closed-bug rows were judged by Claude reading each issue's full API history. Two cross-team calls were checked against the raw comments. A human should spot-check about 5 of each.
- **The 50 projection is rough.** It comes from 5 of 57, so the 95% range is wide (about 3–19%). Only a filtered hand read of the 579 can confirm it.
- **Team = `group::` label.** It ignores stages and sibling groups. A blocker with no group label counts as unknown, not cross-team.
- **Bots that look human.** The flaky-test bot files 54% of issues. gitlab-bot auto-closes 10,624. The Status field defaults to "Complete" on 98% of closed issues, so it means nothing.
- **API traps.**
  - Label history and REST `/links` need a token.
  - `merge_requests_count` counts closing MRs only.
  - `/closes_issues` is empty once an MR is merged.
  - Merge commits now cite `…/merge_requests/N`, not `!N`.
- **Code side is Ruby.** Pair 1's Python resolvers don't apply. Risky-file and ownership numbers come from git history and CODEOWNERS only.

## What to do with it

- **For #7 (stalls):** use it. The 57 rows are a gold-set start, after a human spot-check. Apply the spell-end rule before using durations.
- **For #5 (blocks):** run one more targeted pass before giving up on real data.
  1. Filter the 579 to issues whose blocker has a different `group::`, or whose comments name another team.
  2. Hand-read those.
  3. Keep a block only if it passes the NEXT-STEPS checks (order, plus a written wait). The code-edge check isn't possible here (Ruby).
  4. If that reaches 30, GitLab answers #5. If not, #5 is fixtures only.
- **For #6:** the RuboCop todo lists are the only real adds-while-removing data we've found. They need a random-team baseline.
- **For #4:** add a churn baseline to 05-test. Bug history doesn't beat churn here, or in Home Assistant.

## Reproduce

Python 3.9+. Run from this folder:
- Token steps need a GitLab `read_api` token in `~/.gitlab_token`.
- Outputs go to `.jsonl` here and to `data/`, both git-ignored. Run `mkdir -p data/tix data/code` first.

```
# Issues and authors (anonymous)
python3 pull.py 2025-01-01T00:00:00Z 2025-07-01T00:00:00Z && python3 authors.py && python3 analyze.py

# Blocked spells for every issue (token, ~27 min)
python3 labels_all.py && HUMAN=1 python3 analyze_all.py

# The 1,000 sample and the three methods
python3 notes.py && python3 labels.py && python3 analyze_notes.py && python3 analyze_labels.py && python3 methods.py

# Picking and reading the 40 (token)
python3 pick_40.py && python3 fetch_issues.py sample_blocked_40.txt issues40.json
REC=issues40.json python3 timeline.py <iid> ...

# Beyond blocks and stalls
python3 tix_pull_closing.py && python3 tix_pull_rest_issues.py && python3 tix_pull_related.py 5
python3 tix_analyze_done.py && python3 tix_mr_ticketkey.py && python3 tix_analyze_mr.py
python3 tix_deprecations.py && python3 tix_analyze_deprecations.py && python3 tix_labels_table.py
sh code_run_all.sh      # clones gitlab-org/gitlab (blobless, 1.2 GB) into data/code, ~35 min fresh
```

| Script | Prints | Writes (git-ignored) |
|---|---|---|
| [pull.py](pull.py), [authors.py](authors.py), [analyze.py](analyze.py) | Issue counts, team and workflow labels | `issues.jsonl`, `authors.jsonl` |
| [labels_all.py](labels_all.py), [analyze_all.py](analyze_all.py) | Blocked spells, wait to start, milestone slips, cross-team links | `labels_all.jsonl`, `spells_all.json` |
| [notes.py](notes.py), [labels.py](labels.py), [methods.py](methods.py) | The 1,000 sample and the three methods | `notes.jsonl`, `labels.jsonl` |
| [pick_40.py](pick_40.py), [fetch_issues.py](fetch_issues.py), [fetch17.py](fetch17.py), [timeline.py](timeline.py) | Issues to read, and a readable timeline per issue | `issues40.json`, `issues17.json` |
| `tix_*.py` | Closed-vs-done, MR → issue, deprecations, labels table | `data/tix/` |
| `code_*.py`, `code_run_all.sh` | Git basics, CODEOWNERS, ownership, fragility, RuboCop todo | `data/code/` |

Kept in git: [hand_check_17.csv](hand_check_17.csv) and [hand_check_40.csv](hand_check_40.csv) (the 57 read), [tix_closed_no_mr_20.csv](tix_closed_no_mr_20.csv), [labels_table.md](labels_table.md), and the seeded lists [sample_1000.txt](sample_1000.txt) and [sample_blocked_40.txt](sample_blocked_40.txt).
