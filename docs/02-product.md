# Product

One map, four views.

## Staff engineer

```
verify_token        184 open tickets     212 call sites remaining in code

  Hidden chains     11    Jira links 3 of them
  Collisions         4    two open tickets editing the same function
  Duplicates         2    same call sites, two tickets
  Untracked work    28    call sites no ticket covers
  Vague             19    failed a ticket check  ──>  suggested rewrites

  link coverage 71%   ·   19 tickets unmapped   ·   3 teams not connected
```

Per cross-team ticket: what connects the teams, order, owners, diff or flag, risk,
done condition. Workarounds are labelled hypotheses.

- Internally the "super engineer". Never on screen
- Legacy radar: unfiled legacy code (deprecated symbols with callers, old
  high-fan-in files). Accept/reject and "what unblocked this?" train the auditor (04 §9).
  The fan-in and twin signals were cut in phase 2: they need a whole-repo call graph

## API owner

```
verify_token          31 of 52 done          est. complete Nov 14

  Payments        9 left     moving       3 risky
  Fulfillment     7 left     blocked      waiting on Checkout
  Checkout        5 left     STALLED      19 days  ──> 3 possible reasons
  Search          0 left     done
```

## API consumer

> **Checkout, 9 call sites remaining.**
> - 3 in `cart/pricing.py`, 4 bug fixes this year
> - 2 blocked by `shared/client.py`, which Platform hasn't migrated
> - 4 one-line swaps, diffs below
> - You are blocking Fulfillment

## Developer, on every push

```
Meridian · 3 notes · nothing blocks this merge

  P1  cart/totals.py:88     new call to AuthClient.verify_token (being removed)
  P3  shared/client.py:40   CHK-311 (Checkout, open) edits this function too
  P5  CHK-298               last call site removed. Ticket can move to Done
```

## Ticket tools

- Manager: flags vague tickets, suggests rewrites from map facts, asks for the rest
- Maker: drafts tickets for untracked work, one per team per wave, with links
- Intake: Slack, support, GitHub issues → grouped by fingerprint → draft tickets

> **Before:** CHK-311: Fix auth stuff in checkout *(no description)*
>
> **After:** CHK-311: Migrate `cart/` off `AuthClient.verify_token`
> - `cart/pricing.py:214`, `:231`, `cart/totals.py:57` · owner checkout · depends on PLAT-2291
> - Done when all three show `removed`
> - Question: two calls sit inside `except AuthError`. Keep the same handling?

## Rules

- "Bug fixes", never "incidents". Fragility always ships with ticket IDs
- Competitors (Sourcegraph, OpenRewrite, Jira AI, Linear) don't do hidden cross-team
  dependencies. Recheck before any pitch

## Success

- Diffs and drafts used, edited, or ignored. Burndown before vs after us
- Ignored and flat by week 8 = we built a dashboard
- No customer yet: outcomes are from replay. Say so

## Build order

1. Extract + Join on Django → the 265
2. Push check P1, P7, P8 + replay equivalence
3. Spark ticket links → P3, P5, ticket checks
4. Graph, chains, P4, after the cross-repo check
5. LLM, gate, diffs
6. Auditor learning

Status (Nov 15): the push check runs P1–P9 and replayed the 21 migration commits: 7 P9
alerts on 19 checked pushes, median 0 per push. LLM, gate and diffs are built: Django
95.1%, packages 73.2% against a 90% bar. Auditor learning is built, with 0 live matches. On real data no ticket
maps to a call site, so the block and chain tables are empty; the `acme/shop` fixture runs
them end to end.

## Known gaps

Phase 2's open gaps and what happens to each: [GAPS.md](../Model-Pipeline/GAPS.md), "After phase 2".

| Gap | Effect | Decided by |
|---|---|---|
| Cross-team blocks need a human check | GitLab gold set: 262 pairs read by a model; a random 50 were drafted by a model and reviewed by a person, and the original model verdict agrees on 18 | Spot-check before 05-test uses them |
| No chains across repos | Chains, P4 on fixtures only, said on screen | None of the 4 sources has them |
| Resolver misses aliased `_()` | Closed: the one-hop rule finds 542 of 542 | Week-1: both got 533 of 542 |
| Weak ticket → code mapping | P3, P5, chains go quiet (Spark: 5% linked) | Mapping precision |
| Few Elo matches | 0 live matches: auditor policies and walk-v1 unscored, promotion needs 30 | Live use, phase 3 |
| Package diffs under the bar | 73.2% vs 90%; 230 of 244 misses are owners choosing line by line | Learning from owners' edits, phase 3 |
| Walker texts fall back | 19 of 20 name a team their facts don't hold, so they ship as templates | Team names into the walk's facts (gap 27) |
| Gate rejects one right form | 14 right line lists without their path are rejected (`6921a` is checked since Nov 15, gap 26) | Gate fix with planted-error tests (gap 23) |
| No policy writer | Cut: the auditor's only challenger is the hand-written v1 | Phase 3, after live matches (gap 22) |
