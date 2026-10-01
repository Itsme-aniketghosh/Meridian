# Cross-team blocks and stalls: what to do next

The gap: no week-1 dataset can test feature #5 (hidden cross-team blocks) or #7 (why work stalled). Spark has no stalls, and our 20 Django packages never call each other. We ran two deep-research reports to find public data: [Claude](claude-deep-research.md) and [ChatGPT](chatgpt-deep-research-report.md).

## TL;DR

- **Both reports agree:** no public dataset does this out of the box. Build a small, hand-checked set from real dependency edges and real tickets, and never invent the blocks.
- **They disagree on where to start.** Claude says Debian's Python 2 removal (about 3,477 bugs with "blocked by" links). ChatGPT says OpenStack's Oslo migration (about 23 repos) and never looked at Debian.
- **Don't pick yet.** Spend about an hour running three public queries (below) to settle it with numbers. Then build the gold set: 30+ blocks and 30+ stalls, the bar in [05-test](../../docs/05-test.md).

## Where the reports agree

- NumPy 2.0 has real import chains across independent repos, e.g. librosa blocked via numba, and geoxarray via rasterio. It's the best fit for Meridian's "find it in the code" claim. But there's no central tracker, so labels have to be made by hand.
- Mozilla Bugzilla is the best source for stall reasons, but it's mostly one repo. It tests team boundaries, not repo boundaries.
- Kubernetes `extensions/v1beta1` (#43214) gives about 10–20 gold cases with clean ordering across repos (kops, cluster-proportional-autoscaler).
- Apache Hadoop/HBase/Hive has strong individual cases (HIVE-15393, HBASE-4233), but they're scattered, not one migration.
- BUMP and PyMigBench have no tickets and no cross-repo chains. BUMP is still useful for real build breakages to put inside fixtures.
- A tracker link isn't proof of a block. "Depends on" often just means the work was split up. Only count a block when the code or comments prove the wait.

## Where they disagree

| | Claude | ChatGPT |
|---|---|---|
| #1 for blocks | Debian py2removal | OpenStack Oslo-incubator removal |
| OpenStack | Ranked 7th, but it looked at a different goal (drop py27) | Ranked 1st: library release → requirements → consumer change is a real, ordered chain |
| Debian | Ranked 1st | Not considered |
| Risk it names | Debian's block links were bulk-added from the package graph, so they may be circular | OpenStack has few planning tickets; most work is only in Gerrit |

Neither report verified its own scale numbers. Both say "compute from the API."

## Step 1 · settle it with numbers (about 1 hour, read-only public APIs)

| Query | Answers | Source |
|---|---|---|
| Debian UDD: py2removal bugs joined to `bugs_blockedby` | How many bugs have a block? Who added the links, and when? Median and slowest time to close? How many `py2keep`? | `postgresql://udd-mirror:udd-mirror@udd-mirror.debian.net/udd` |
| OpenStack Gerrit: topic `goal-remove-incubated-oslo-code` | How many changes, across how many repos? Created → merged times? How many cite a library release? | `review.opendev.org` REST |
| Mozilla Bugzilla: bug 922464 and its 43 dependencies | Open → resolved times. How many comments say "wait for", "once X lands", or "blocked"? | `bugzilla.mozilla.org` REST |

Go with whichever source gives the most blocks that pass the check in step 2, per hour of work. Most likely: Debian or OpenStack for blocks, Mozilla for stalls.

## Step 2 · build the gold set

**Blocks (target 30+, pass bar: precision ≥ 0.90).** Sample 60 candidate pairs with a fixed seed. Keep a pair only if all three hold:
1. **Code edge:** the downstream repo really imports or calls the upstream one. Check the source, not just the packaging metadata.
2. **Order:** the downstream fix landed after the upstream fix or release.
3. **Wait:** a comment, review, or version pin shows someone actually waited.

Check 3 is what stops this from being circular. Without it we'd be grading Meridian against its own method.

**Stalls (target 30+).** Take tickets open 90+ days and code each reason as one of `upstream`, `owner`, `risk`, `capacity`, `other`, `unknown`. Only use `capacity` if someone said so. Inactivity alone isn't evidence. Add 10+ conda-forge "awaiting parents" cases as false blocks, so Meridian has to tell them apart.

**One row per case** (merged from both reports):

```
migration_id, consumer_repo, consumer_team, upstream_repo, upstream_team,
code_edge, deprecated_symbol_or_version, deprecation_at, removal_at,
ticket_created_at, first_code_activity_at, resolved_at, blocked_from, unblocked_at,
reason, evidence_type, evidence_url, confidence, synthetic
```

Use `resolved_at`, never the close time. HBASE-4233 took 4 days of work, then sat 4 years before being bulk-closed. Pair 2 found the same mass-close trap in Spark.

## Step 3 · put it in Meridian's shape

- Map repo → repo, maintainer team → owning team, bug or issue → Jira ticket, keeping the real dates.
- Hide the block links from the ticket view, so Meridian has to rediscover them from code.
- Synthetic is allowed only for the ticket workflow around a real code edge (e.g. a "wrong owner" reassignment), and those rows must be marked `synthetic=true`. Never synthesize the block itself.

## Decisions needed

1. **Who owns this?** It's cross-repo, so Pair 3 by default.
2. **What to demo:** if step 1 shows fewer than 30 real blocks anywhere, #5 and #7 go in the demo on fixtures only, and we say so on screen.
3. **Timebox:** about 1 hour for step 1, then 1–2 days for step 2.
