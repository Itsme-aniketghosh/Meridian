# What the user gets

Two people open this product and they want different things. Same map underneath,
different filter.

## If you own the API

You announced the deprecation and you need to know if it's going to land.

You get one page:

```
verify_token          31 of 52 done          est. complete Nov 14

  Payments        9 left     moving       3 risky
  Fulfillment     7 left     blocked      waiting on Checkout
  Checkout        5 left     STALLED      19 days  ──> 3 theories
  Search          0 left     done

  coverage 85%   ·   3 new call sites since Sept 1   ·   6 unowned
```

Two things on that page we could not find anywhere else: **who is blocking whom**,
and **a ranked list of theories for why Checkout went quiet**, each with evidence
you can click. Lead with those.

The other two are real features and not novel ones, and it's worth knowing which is
which before someone in the room tells us:

| | Who else does it | What's different here |
|---|---|---|
| Ownership from commits, not CODEOWNERS | Sourcegraph infers it from recent contributors | Ours is per call site and feeds routing, not a UI hint |
| Burndown | Sourcegraph Batch Changes charts one | Theirs counts merged PRs. Ours counts call sites in code, so it moves without anyone filing anything |

Fragility is the same story. Defect prediction from file history is a twenty-year
academic field and we are not inventing it — we're putting it next to the call site
you're about to edit, which is where nobody has bothered to put it.

## If you consume the API

You didn't ask for this work and you want it off your plate.

You get a brief:

> **Checkout, 9 call sites remaining.**
> Three sit in `cart/pricing.py`, which appeared in 4 incidents this year. Pair
> with a reviewer.
> Two are blocked: `order/submit.py` routes through `shared/client.py`, which
> Platform hasn't migrated yet.
> Four are one-line swaps. Diffs below, based on how Platform migrated their own
> call sites in `a3f21c9` and `7b40e18`.
> You are currently blocking Fulfillment.

Your nine, not all 52. Sorted by what to do first. With diffs where we're confident
enough to draft one, and a flag where we aren't.

## What's underneath

| | |
|---|---|
| Impact map | Every call site, file and line, across all repos |
| Ownership | From git history, not config |
| Fragility | Which call sites sit in code that has broken before, named for what it's actually counting |
| Order | Blocking and risky first, leaves last |
| Burndown | Counted from the code, not from tickets |
| Blast radius | If this file changes, what else is affected |
| Stall detection | Checkout hasn't moved in 19 days |
| Stall diagnosis | Ranked theories for why, with evidence |
| Ticket sync | What Jira claims versus what the code says |
| Drift alarm | New call sites appearing after the freeze |
| Suggested diff | Drafted only where it matches your own prior migrations |
| Coverage | What we found and what we probably missed, on every output |

Coverage is not optional. It appears on every screen.

### One wording rule

Say what the data is. If fragility was computed from bug-fix history, the screen
says "4 bug fixes", not "4 incidents". If a customer connects incident data, it says
incidents.

This sounds pedantic and isn't. Every public dataset we train on holds bug reports;
none holds production incidents. An engineer who has run a platform team knows the
difference between a bug someone filed and a page at 3am, and using the louder word
for the quieter data is the fastest way to lose a room. The same rule is why the
fragility score ships with its ticket IDs — so anyone can click through and see
exactly which kind of thing it counted.

## What we don't do

We suggest changes. We don't apply them, we don't open PRs, and we don't merge
anything. Every diff is something a human chooses to use.

We're not trying to be right about that forever. We're trying to earn it, and the
way to earn it is to find out whether the suggestions are any good first.

## How we'll know it's working

One question decides everything: does the work actually move?

- **Do people use the diffs?** Every suggestion ends up applied clean, applied with
  edits, or ignored. That number is the product working or not working, and it
  costs nothing to collect
- **Does the burndown move faster after we show up?** Compare the rate before and
  after for the same migration
- **Do consuming teams open it twice?** A tool you read once is a report
- **Are the theories right?** When a stall breaks, we recorded a prediction. Check it

If suggestions get ignored and the burndown looks the same, we built a dashboard.
Better to find that out in week 8 than to add features on top of it.
