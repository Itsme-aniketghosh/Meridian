# Cross-team · blocks and stalls

The gap was that no week-1 dataset could test feature #5 (hidden cross-team blocks) or #7 (why work stalled). Two deep-research reports ([Claude](research/claude-deep-research.md), [ChatGPT](research/chatgpt-deep-research-report.md)) suggested sources. We checked four of them.

## TL;DR

- **#5 blocks: GitLab reaches the bar, twice over.** Two passes on H1 2025 used different starting points and each confirmed 30. Vishwa's started from issues labelled blocked; ours from "blocked by" links. None of Vishwa's 8 published examples is in our 30, so there are at least 38 distinct waiting issues.
- **#7 stalls: GitLab is usable.** Of 57 blocked issues read, 50 have a written reason.
- **Both reports' top picks failed.** Debian's links are reversed and bulk-added. OpenStack had nothing to wait for.
- **Big caveat:** a model read the evidence, not a person. A human spot-check comes before anything is called gold.

## What counts as a block

A cross-team block counts only if all 3 hold:
1. **Code edge:** the waiting team's code really uses the other team's code. Check the source, not just package metadata. (GitLab is Ruby, so it's skipped there.)
2. **Order:** the waiting work finished after the blocker did.
3. **Wait:** a comment, review, or version pin shows someone actually waited. Check 3 stops us grading Meridian against its own dependency graph.

A stall gets a reason only from a written statement: `upstream`, `owner`, `risk`, `capacity`, `other`, or `unknown`. Inactivity alone isn't a reason.

## What each source gave

| Source | Checked by | Real cross-team waits (#5) | Stalls with a written reason (#7) | Use it for |
|---|---|---|---|---|
| [Debian](debian/) | Chaitali | 0 of 13. Links point the wrong way, one person bulk-added them, 97 are cycles | Slow bugs, no reasons | Real dependency edges only |
| [OpenStack](openstack/) | Chaitali | 0 of 59. Libraries had shipped 1.5–3.5 years before consumers moved | None (median 4 days) | Nothing |
| [Mozilla](mozilla/) | Chaitali | 3–4, all in the meta bug's "blocks" list | 1 | The lesson: read "blocks", not "depends on" |
| [GitLab](gitlab/) | Vishwa | 5 of 57 in a random read | **50 of 57** | #7 |
| [GitLab, targeted read](gitlab/README.md) | Vishwa | **30** (25 from 126 candidates + 5 from the random read) | — | **#5** |
| GitLab, link pass (`gitlab/xt_*.py`) | this pass | **30** (22 written waits + 8 stated prerequisites) | — | **#5** |

## The GitLab link pass

**Question:** does GitLab hold 30+ real cross-team blocks? This pass starts from every "blocked by" link. Vishwa's [targeted read](gitlab/README.md) starts from blocked labels. 17 of our 30 were in Vishwa's 126 candidates, and 13 weren't, because of the different starting points.

1. **Population.** All 32,178 issues created Jan–Jun 2025. Dropped the flaky-test bot's 17,502, leaving 14,653 filed by people. Every one's activity log was read, because links get deleted once work is done.
2. **Links.** 1,649 "blocked by" links on 1,200 issues. All were added by people. 106 point to other projects.
3. **Cross-team.** The blocker has a different `group::` label, or sits in another project: 179 links.
4. **Order.** The blocker was open when the link was added, and the blocked issue didn't close before it: 132 links on 111 issues.
5. **Written evidence.** We pulled each blocked issue's comments and description, and read every snippet that mentions the blocker, waiting, or the other team.

| Verdict per waiting issue | Issues |
|---|---:|
| **Wait**: a comment says it's blocked by or waiting on the blocker | **22** |
| **Planned**: the description makes the blocker a prerequisite, and the order held | **8** |
| Partial: a real dependency, but the wait isn't stated | 21 |
| Unclear | 9 |
| No: "not a blocker", dependency swapped, or wrong link | 18 |
| No written evidence | 33 |

- 30 wait + planned, from 18 waiting teams and 18 blocking teams, 26 pairs. Vishwa's 4 others with no formal link make 34.
- Blocked span (link added → blocker closed, N = 31): median **74 days**, p90 246, max 414.
- Examples:
  - #537634 vulnerability management: "work has now started, following the resolution of the blocker #543762" (security foundations).
  - #547122 dap: "we need ai-assist#906 completed before we can start" (code creation).
  - #552105 pipeline execution: "The migration is now completed! Marking this issue as unblocked" (ci platform).

**Caveats**
- **A model judged the snippets** (`checked_by` says so). Only "wait" rows quote the blocker directly.
- **Monorepo:** team = `group::` label. This tests team boundaries, not chains between repos.
- **Ruby:** our Python resolvers can't run the code-edge check, so the evidence is the ticket and its comments, not imports.
- **Current labels:** group labels are as they are today. Some teams were renamed or deprecated since.
- 63 links point to blockers we can't see (private projects), so they weren't checked.

## Rerun the link pass

Python 3.9+, no token needed. Run in a scratch folder (the outputs are big), with the `xt_*.py` files copied in:

```
python xt_pull_issues.py                                                # 32k issues, ~8 min
# write human_iids.txt (iids not by the "[Test]" project bot), then:
python xt_pull_notes.py human_iids.txt activity.jsonl ONLY_ACTIVITY      # ~30–60 min; split across files to parallelise
python xt_edges.py && python xt_enrich.py && python xt_evidence.py      # edges → cross-team + order → comment snippets
```

Verdicts go to `gitlab/xt_cross_team_blocks.csv`: one row per link, with an evidence quote and a verdict. It stays local under the folder's "CSVs aren't pushed" rule, as do Vishwa's confirmed 30.

What to do next is in [../NEXT-STEPS.md](../NEXT-STEPS.md).
