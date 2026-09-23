# Agents

```
        THE MAP  +  THE GRAPH
                 │
        ┌────────┴────────┐
        ▼                 ▼
     WORKERS          AUDITORS
   explain + draft   explain the stall
        │                 │
        └────────┬────────┘
                 ▼
          GROUNDING GATE
                 ▼
              OUTPUT ──> outcomes logged
```

One rule: agents read structured rows, never another agent's prose. Prose in, prose
out is a rumour mill.

## Workers

| Agent | Produces |
|---|---|
| Call-site analyst | Pattern, complexity, test coverage per call site |
| Ownership reasoner | Who should do this, with confidence. Flags orphans |
| Sequencer | Order, with the reason for each wave |
| Diff drafter | The change, only where the pattern matches prior examples |
| Briefing writer | The brief a team actually reads |

They explain and draft. They don't decide, and nothing they produce gets applied
automatically.

## Auditors

Take something stuck, emit ranked theories for why, each with evidence and a check.

This is the part nobody else does. Every tool will tell you Checkout hasn't
committed in 19 days. None will tell you the owner left in March.

Input is a stall: a team at zero past a threshold, an untouched call site, a
flatlining migration. Output is a ranked list:

```
claim        one sentence, labelled a hypothesis
evidence[]   map rows, SHAs, ticket IDs. every item a fact
check        what would confirm or refute it
confidence   calibrated, allowed to be low
verdict      set when the stall breaks
```

### Theory catalog

Eight to start.

| Theory | Evidence |
|---|---|
| Upstream block | Calls route through another team's unmigrated file |
| Orphaned owner | Top author has no commit anywhere in N days |
| Semantics unclear | Inside error handling referencing old behavior, no diff drafted |
| Test gap | No covering tests, nobody wants to touch it |
| Competing load | Commit volume moved to another repo or epic |
| Ticket mismatch | Jira closed, code unchanged |
| Fragility freeze | High fragility, team avoiding it deliberately |
| Never notified | No ticket exists for this team |

### Grounding a guess

The product rule is "not in the map, not in the output," and a theory isn't in the
map. So we split it. The claim is a hypothesis and gets labelled as one. The
evidence isn't: every item is a row, a SHA, or a ticket ID, and the gate strips
anything else. An auditor can say "probably blocked upstream." It can't invent the
file that's blocking you.

If nothing has evidence, it says so. "We don't know why this is stuck" is a valid
output and better than a confident story.

### Example

> Checkout, 9 call sites, no movement in 19 days.
>
> 1. Blocked upstream (0.71). Six of nine route through `shared/client.py:88`,
>    Platform-owned, still on the old path as of 2026-09-21. Check: does Checkout
>    move within a week of PLAT-2291 landing?
> 2. Semantics unclear (0.44). Two in `order/submit.py` inside `except AuthError`.
>    No diff drafted, both failed the error-handling check.
> 3. Orphaned owner (0.19). `cart/pricing.py` top author `r.mehta`, no commit in any
>    repo since 2026-03-04.
>
> Not proposed: competing load. Commit volume flat, not redirected.

## How they get better

Every theory is logged as a dated prediction. When the stall breaks we record which
one was right. That gives us labelled stalls at no annotation cost, and the ranking
improves on real outcomes instead of intuition.

The cheap part is that a revised catalog can be re-scored against every past stall
without re-querying anything, because the evidence is already recorded. Same trick
as [Dream-RSI](https://arxiv.org/html/2609.14858v1) (Zheng et al., 2026), which
replays frozen decision trees to score new policies without paying to re-run them.
That paper is about exploration policies, not auditors, so the transfer is ours.

Worth being honest: this does nothing early. Three recorded migrations is not a
training signal. Until the record is real, the ranking is hand-written and we say
so.

## Later, not now

An explorer that walks the graph and nominates migration targets nobody has filed:
a symbol with a deprecation marker and live call sites, two functions with the same
shape where one is gaining callers and the other losing them, high fragility plus
high fan-in. Interesting, and it needs the same recorded history the auditors need.

## Cost

Agents are the expensive layer, so they run last on filtered input. Workers see only
changed call sites. Auditors fire only on something actually stuck, which is a
handful a day, not 52.
