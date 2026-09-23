# Self-improvement

Based on Dream-RSI (Zheng et al., [arXiv:2609.14858](https://arxiv.org/html/2609.14858v1), Sept 2026).

Being precise about what we took: that paper is recursive self-improvement of
exploration policies by replaying past discovery trees. It has no auditor agents.
The exploration half is theirs. Applying replay to the auditor catalog is ours.

## The insight

- A finished run is a tree of decisions with recorded outcomes, so you can replay it
- A different policy walks the same frozen tree, picks different branches, gets
  scored. No new expensive calls
- We already have that substrate. The map is append-only and dated. Every
  migration is a recorded tree
- We didn't design the two clocks for this. But `effective_date` + `known_at` is
  exactly what you need to replay a decision without leaking the future into it

## The loop

```
   ┌──> STAGE 1  run it for real ─────────────────────────────┐
   │    policy explores the graph, every choice logged into a  │
   │    discovery tree with outcomes attached                  │
   │                        ▼                                  │
   │    STAGE 2  freeze it                                     │
   │    finished trees join the replay pool. never edited      │
   │                        ▼                                  │
   │    STAGE 3  dream                                         │
   │    score many candidate policies against the frozen pool  │
   │    an LLM reads where it lost and rewrites the policy     │
   │                        │                                  │
   └────────────────────────┘  best policy goes back online
```

## Exploration policy

- Policy sees a tree, picks a batch from eligible nodes, capped by free workers
- A node is a candidate: a call site to expand, a module to walk into, a symbol
  that might be an unfiled migration target
- Score = quality found − agent calls spent + bonus for work that parallelized.
  The cost term matters more for us than the paper, because calls are the bill
- The policy is code, not a prompt. A policy-development agent reads the replay
  trajectories and scores, finds where calls were wasted, rewrites it

## Auditors improve the same way

- Every stall that broke is a frozen world: evidence at the time, theories
  proposed, what actually unblocked it
- A revised catalog re-ranks against every past stall at almost no cost. The
  evidence is recorded, nothing needs re-querying
- Loop: propose, log as dated predictions, record the verdict, replay the revised
  catalog against all past stalls, keep the version that ranks truth higher
- The LLM writes new theories by reading the ones that lost
- A year in, the catalog isn't eight things somebody thought of in week seven

## Promotion rules

Replay makes it cheap to fool yourself, so the gates matter more than the loop.

- Train on earlier worlds, test on later. Never a random split
- Never promote on a single world. The paper sweeps its cost parameter across all
  history to check the tradeoff genuinely shifted. A policy that wins on one
  migration doesn't ship
- Report what got promoted and why. Fewer calls but finds less, we say so

## Why this is the moat

Drop the old argument. "A competitor starting in 2028 can't recover 2026" is false
for the raw data: git is a time series you can re-parse, Jira exposes changelogs.

What actually can't be backfilled:

- Outcome labels. Was the diff applied clean, edited, ignored. Which theory was
  right. It never existed as data until we wrote it down
- `known_at`. When we observed something, not when it became true. A competitor
  re-parsing history gets perfect hindsight, which is useless for training a
  policy that decides under partial information

The compounding is in the policies, not the rows. Data is substrate. The improved
policy is the asset.

## Limits

- Replay only covers decisions inside the recorded tree. A policy that would have
  explored somewhere we never looked can't be scored. Same limit as the paper
- Early on the pool is tiny and this does nothing. Two or three migrations is not
  a signal. Until it's real, the policy is hand-written and we say so
- Auditor verdicts are noisier than code outcomes. "Applied clean" is unambiguous.
  "The stall broke because Platform landed their PR" is a judgement, sometimes
  ours. Log who made the call. Human-confirmed verdicts count for more
