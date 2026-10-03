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
- **Legacy radar:** unfiled legacy code (deprecated symbols with callers, old
  high-fan-in files). Accept/reject and "what unblocked this?" train the auditor (04 §9)

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

- **Manager:** flags vague tickets, suggests rewrites from map facts, asks for the rest
- **Maker:** drafts tickets for untracked work, one per team per wave, with links
- **Intake:** Slack, support, GitHub issues → grouped by fingerprint → draft tickets

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

## Known gaps

| Gap | Effect | Decided by |
|---|---|---|
| No cross-repo corpus | Blocks, chains, P4 on fixtures only | Week-1: 20 packages |
| Resolver misses aliased `_()` | 542 calls lost | Week-1: both get 533 of 542 |
| Weak ticket → code mapping | P3, P5, chains go quiet (Spark: 5% linked) | Mapping precision |
| Few Elo matches | Learning stalls | Week-1: matches per month |
