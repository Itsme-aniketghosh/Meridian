# Product

One map, three views.

## Staff engineer: cross-team view

```
verify_token        184 open tickets     212 call sites remaining in code

  Hidden chains     11    Jira links 3 of them
  Collisions         4    two open tickets editing the same function
  Duplicates         2    same call sites, two tickets
  Untracked work    28    call sites no ticket covers
  Vague             19    failed a ticket check  ──>  suggested rewrites

  link coverage 71%   ·   19 tickets unmapped   ·   3 teams not connected
```

For each cross-team ticket, a suggested solution:

- **What connects the teams:** `shared/client.py:88`, Platform-owned, still on the old path
- **Order:** PLAT-2291 first, then Checkout's 6 dependent call sites
- **Who:** the owner of each piece, by commit share
- **Change:** a diff where it matches prior migrations, otherwise a flag
- **Risk:** bug-fix count, with ticket IDs
- **Done when:** the listed call sites show `removed` in the map
- **Workarounds** (e.g. an adapter in the shared client) are labelled hypotheses

Internally this is the "super engineer". The screen never says so.

**Legacy radar:** legacy code worth migrating that nobody has filed yet, such as
deprecated symbols with live callers or old high-fan-in files. Accept or reject each
one. Those clicks, plus "what unblocked this?" when a stall ends, train the auditor
policies (04, section 9).

## API owner

```
verify_token          31 of 52 done          est. complete Nov 14

  Payments        9 left     moving       3 risky
  Fulfillment     7 left     blocked      waiting on Checkout
  Checkout        5 left     STALLED      19 days  ──> 3 possible reasons
  Search          0 left     done

  coverage 85%   ·   3 new call sites since Sept 1   ·   6 unowned
```

What's new here: who is blocking whom, and ranked possible reasons for a stall, each
with evidence.

## API consumer

> **Checkout, 9 call sites remaining.**
> - 3 in `cart/pricing.py`, 4 bug fixes this year. Pair with a reviewer
> - 2 blocked: `order/submit.py` gets its client from `shared/client.py`, which
>   Platform hasn't migrated
> - 4 one-line swaps, diffs below, matching Platform's `a3f21c9` and `7b40e18`
> - You are blocking Fulfillment

## Developer: on every push

```
Meridian · 3 notes · nothing blocks this merge

  P1  cart/totals.py:88     new call to AuthClient.verify_token (being removed)
  P3  shared/client.py:40   CHK-311 (Checkout, open) edits this function too
  P5  CHK-298               last call site removed. Ticket can move to Done
```

## Ticket tools

- **Ticket manager:** flags vague tickets, shows which check failed, and suggests a
  rewrite using only facts from the map. It asks about anything the map can't know
- **Ticket maker:** drafts tickets for work the map has and Jira doesn't. One per team
  per wave, with depends-on links filled in
- **Intake:** Slack, support tools, and GitHub issues are grouped, mapped to code by
  stack trace or error string, and turned into draft tickets

> **Before:** CHK-311: Fix auth stuff in checkout *(no description)*
>
> **Suggested:** CHK-311: Migrate `cart/` off `AuthClient.verify_token`
> - Call sites: `cart/pricing.py:214`, `:231`, `cart/totals.py:57`
> - Owner: checkout (r.mehta, 62% commit share)
> - Depends on PLAT-2291
> - Done when: all three show `removed`
> - Question: two calls sit inside `except AuthError`. Keep the same handling?

## Features

| | |
|---|---|
| Impact map | Every call site, file and line, across all repos |
| Ownership | From git history, not CODEOWNERS |
| Fragility | Bug fixes on the file, with ticket IDs |
| Order | Blocking and risky call sites first |
| Burndown | Counted from code, not tickets |
| Blast radius | What else a change reaches |
| Stalls | Detected, with ranked possible reasons |
| Ticket sync | Jira's status vs the code's |
| Ticket chains | Dependencies in code that Jira doesn't record |
| Ticket manager and maker | Flags, rewrites, drafts |
| Drift alarm | New call sites after the freeze |
| Push check | Drift, collisions, new blocks, ticket moves, on every push |
| Suggested diff | Only where it matches your own prior migrations |
| Coverage | What we found and what we missed, on every screen |

## Wording rule

- Name what the data counts: "4 bug fixes" unless incident data is connected
- Every fragility figure ships with its ticket IDs

## Who else does parts of this

| | Others | Ours |
|---|---|---|
| Ownership from commits | Sourcegraph | Per call site, and used for routing |
| Burndown | Sourcegraph Batch Changes, counting merged PRs | Counts call sites in code |
| Fragility | 20 years of defect-prediction research | Shown at the call site |
| Ticket rewriting | Jira's AI, Linear | Adds code facts, not prose |
| Duplicate detection | Linear, Jira plugins | Compares call sites, not text |
| Feedback clustering | Productboard, Enterpret, Unwrap | Maps to code and owners |
| Hidden cross-team dependencies | Not checked yet | Lead with it if nobody does it |

Recheck this before any pitch.

## Success metrics

- Diffs: applied clean, applied with edits, or ignored
- Drafted tickets: filed unchanged, filed with edits, or ignored
- Burndown rate before and after we show up, for the same migration
- Consuming teams come back a second time
- Stall reasons and suggested order checked against what actually happened, with N
- Suggestions ignored and burndown flat = we built a dashboard. Know by week 8
- No customer yet: outcomes come from replayed history. Every result says so

## Build order

1. Extract + Join on Django → the 279 number
2. Push check P1, P7, P8, plus the replay equivalence test
3. Spark ticket links → P3, P5, ticket checks
4. Graph, chains, P4, only after the cross-repo week-1 check passes
5. LLM layer, gate, diffs
6. Auditor learning

## Known gaps

| Gap | Effect | Decided by |
|---|---|---|
| Cross-repo corpus may not exist | Blocks, chains, and P4 tested on fixtures only | Week-1: 20 packages |
| Resolver may not bind aliased `_()` | Blast radius and P1 miss about 480 calls | Week-1: pyright vs Jedi |
| Weak ticket → code mapping | P3, P5, and chains go quiet. Spark: 5% of tickets linked | Mapping precision (05) |
| Too few Elo matches | Learning doesn't move in 10 weeks | Week-1: matches per month |
