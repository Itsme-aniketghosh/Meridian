# Meridian
### Know your codebase.
*Who owns what, what depends on what, and what has broken before.*

**First use: internal API migrations.**

---

## 1. The setup

A company has ten dev teams. Each team owns some code. Each team calls code owned by other teams.

```
Platform  ──owns──>  AuthClient
                         ▲
              ┌──────────┼──────────┐
           Checkout   Payments   Fulfillment
           (calls it) (calls it)  (calls it)


Checkout  ──owns──>  PricingEngine
                          ▲
                     Fulfillment
                     (calls it)
```

**Every team is both an owner and a consumer**, depending on which change you're looking at. Checkout is a consumer of the auth change and an owner of the pricing change, in the same week.

**Nobody can see past their own repo.**

---

## 2. The problems we solve

**Problem 1: A deprecated API never actually dies.**

Platform announces `AuthClient.verify_token` is going away. 52 call sites, 6 teams. Each team gets a ticket. Each schedules it in their own sprint. Some never do.

Months later someone deletes the old code. Things break.

> Netflix deprecated an internal logging library. **Six years later** references were still everywhere. Product engineers said: *"I don't have time for this, but if you do it for me, I'll merge the changes."*

**Problem 2: Consuming teams find out too late.**

A Slack message, or a ticket that lands three weeks before the deadline. Then they spend a day grepping to find out if it even affects them.

**Problem 3: Nobody knows how much work is left.**

Jira says 31 tickets closed. The code says 21 call sites remain. Which is true? Nobody checks, because nobody has joined the two.

**Problem 4: Nobody knows who should do it.**

CODEOWNERS is stale. The person who wrote that module left. Tickets get assigned to teams who no longer touch the code.

**Problem 5: Nobody knows which changes are dangerous.**

All 52 call sites look identical in a spreadsheet. Three of them sit in a file that caused four incidents last year. Those need a reviewer. The other 49 are one-line swaps.

**Problem 6: Teams block each other invisibly.**

Fulfillment can't finish because their calls route through a shared client that Checkout hasn't migrated. Neither team knows.

**Problem 7: The migration goes backwards.**

While six teams remove calls, a seventh adds three new ones. Nobody notices for two months.

---

## 3. What Meridian does

You tell it which API is being retired. It answers, for every team:

| Question | Answer |
|---|---|
| Where is it called? | Every call site, file and line, across all repos |
| Who owns each one? | From actual git history, not stale config |
| Which are dangerous? | Which sit in code that has broken before |
| What order? | Blocking and risky first, leaf consumers last |
| How far along? | 31 of 52 done. Payments has 9 left |
| Who stopped? | Checkout, 19 days, blocking two teams |
| What's the change? | The diff, where we're confident. A flag, where we're not |
| Is it going backwards? | 3 new call sites since the freeze date |
| **What did we miss?** | **Coverage estimate, reported every time** |

---

## 4. Two doors into one product

| | Owning team | Consuming team |
|---|---|---|
| **Who** | Wrote the API, making the change | Calls it from their code, has to change |
| **They see** | All 52 call sites, who's stalled, full burndown | Only their 9. Their deadline. Their suggested diffs |
| **They ask** | Is this going to land this quarter? | Does this affect me, and what do I change? |

Same map. Different filter.

**Consuming teams love it. Platform leadership buys it.**

---

## 5. Features

### For the owning team

| # | Feature | One line |
|---|---|---|
| 1 | **Impact map** | Every call site of this API, file and line, across all repos |
| 2 | **Ownership assignment** | Who owns each call site, from git history, not stale CODEOWNERS |
| 3 | **Fragility score** | Which call sites sit in code that has caused incidents before |
| 4 | **Migration order** | Blocking and risky first, leaf consumers last |
| 5 | **Live burndown** | 31 of 52 done. Payments has 9 left |
| 6 | **Stall detection** | Checkout hasn't moved in 19 days, blocking two teams |
| 7 | **Ticket sync** | What Jira claims is done, versus what the code says |
| 9 | **Drift alarm** | New call sites appearing after the freeze date |

### For consuming teams

| # | Feature | One line |
|---|---|---|
| 11 | **Change radar** | An API you call is being deprecated. You hear before the ticket exists |
| 12 | **Consumer inbox** | Your slice only: your call sites, your deadline, your diffs |

### Shared

| # | Feature | One line |
|---|---|---|
| 8 | **Migration brief** | Plain English per team: what to change, what's risky, what's blocked |
| 10 | **Suggested change** | The diff, learned from how the owning team migrated their own call sites first |
| 13 | **Coverage report** | What we found, what we're unsure about, what we probably missed |

**Feature 13 is not optional.** It appears on every output.

---

## 6. Product flow

```
   ┌──────────────────────────────────────────────┐
   │  Platform: "We're retiring verify_token()"   │
   │  (or Change Radar detects it first)          │
   └───────────────────┬──────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐   ┌──────────┐   ┌──────────┐
   │  CODE   │   │   GIT    │   │  TICKETS │
   │ parse   │   │ history  │   │  Jira    │
   │ N repos │   │ per file │   │  GitHub  │
   └────┬────┘   └────┬─────┘   └────┬─────┘
        │             │              │
     call sites    ownership      fragility
        │             │              │
        └─────────────┼──────────────┘
                      ▼
        ╔═════════════════════════════╗
        ║        THE MAP              ║
        ║  one row per call site      ║
        ║  file · line · owner        ║
        ║  risk · status · blocked_by ║
        ║  append-only, dated daily   ║
        ╚══════════════╤══════════════╝
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
    ┌─────────┐  ┌──────────┐  ┌──────────┐
    │ MODELS  │  │  RULES   │  │  AGENTS  │
    │ risk    │  │ ordering │  │ explain  │
    │ ETA     │  │ blocking │  │ draft    │
    │ stall   │  │ coverage │  │ the diff │
    └────┬────┘  └────┬─────┘  └────┬─────┘
         └────────────┼─────────────┘
                      ▼
            ┌──────────────────┐
            │  GROUNDING GATE  │
            │ every number must│
            │ trace to the map │
            └────────┬─────────┘
                     ▼
   ┌─────────────────────────────────────────┐
   │  OWNER VIEW        │  CONSUMER VIEW     │
   │  burndown          │  your call sites   │
   │  stalls            │  your diffs        │
   │  coverage          │  your deadline     │
   └─────────────────────────────────────────┘
```

**Daily loop:** pull repos → re-parse → diff against yesterday → update the map → re-score → regenerate briefs → alert on stalls, blocks, and new call sites.

---

## 7. How we do it

We join three things nobody joins:

| Source | What it gives |
|---|---|
| **Your code** | Where the API is called |
| **Your git history** | Who actually works on each file |
| **Your tickets** | Which files caused incidents before |

Then we keep that joined record **every day**, so we can answer "what was true last Tuesday," not just "what is true now."

A migration is a time series. You cannot reconstruct it after the fact.

---

## 8. The three layers

### 8.1 Data Engineering

**Job: build and maintain the map.**

| What | How |
|---|---|
| Find call sites | Tree-sitter parse of every repo, daily |
| Find owners | `git log` per file, recent commit share. PyDriller |
| Find fragility | Link tickets to fix commits, trace back to the causing commit (SZZ, already in PyDriller) |
| Track progress | Diff today's call-site set against yesterday's |
| Read tickets | Jira API, configured per customer |

**The hard parts, and this is where the time goes:**

- **Nothing shares a key.** Git speaks in SHAs and file paths. Jira speaks in ticket IDs. The parser speaks in symbols
- **Two clocks on every row.** `effective_date` is when it became true. `known_at` is when we saw it. A call site removed Tuesday but observed Thursday is not the same fact
- **Identity drifts.** Files get renamed, functions move, one person has three git emails, bots pollute everything
- **Append only.** Never overwrite. The history is the product
- **Cross-repo.** An internal API is called from 15 repos, not one

**Defined behavior when things break:**

| Failure | What happens |
|---|---|
| Jira unreachable | Progress from code alone, flagged partial |
| Repo force-pushed | Halt. Do not silently reconcile |
| A file fails to parse | Excluded, counted, **reported.** Never silently dropped |
| No ticket-to-code links | Map and ownership still work. Risk scoring unavailable, and we say so |
| Rate limited mid-run | Checkpoint, back off, resume. No partial writes |

### 8.2 ML

**Job: four small predictions. Deliberately modest.**

| Model | Answers | Trained on |
|---|---|---|
| **Risk** | Will changing this file break something | Which files caused past incidents |
| **ETA** | When will this team finish their share | Past migration velocity |
| **Stall** | Which team stops before finishing | Days since last commit, open load, past follow-through |
| **Suggestion confidence** | Will this drafted diff be applied unchanged | Whether past suggestions were accepted |

**Rules we hold to:**

- Beat the dumb baseline or report that we didn't. The dumb baseline is "the file that broke last time breaks next time," and it is genuinely hard to beat
- Train on the past, test on the future. Never random splits
- Report calibration, not accuracy
- **Abstain when history is thin.** A confident wrong score is worse than "we don't know"

**The suggestion model has the cleanest feedback loop in the product:** applied clean, applied with edits, or ignored. Labels arrive free from normal use.

### 8.3 AI

**Job: explain and draft. Never decide.**

Five agents. Each reads structured data, never another agent's prose.

| Agent | Produces |
|---|---|
| **Call-site analyst** | Structured facts per call site: pattern, complexity, test coverage |
| **Ownership reasoner** | Who should do this, with confidence. Flags orphaned code |
| **Sequencer** | Migration order, with the reason for each wave |
| **Diff drafter** | The suggested change, **only where the pattern matches prior migrated examples** |
| **Briefing writer** | The plain-English brief a team actually reads |

**Example consumer brief:**

> **Team Checkout, 9 call sites remaining.**
> Three sit in `cart/pricing.py`, which appeared in 4 incidents this year. Pair with a reviewer.
> Two are blocked: `order/submit.py` routes through `shared/client.py`, which Platform hasn't migrated yet.
> Four are one-line swaps. Diffs drafted below, based on how Platform migrated their own call sites in `a3f21c9` and `7b40e18`.
> Last commit on this migration: 19 days ago. You are currently blocking Fulfillment.

---

## 9. How suggested changes work

**We do not guess what your code should become. We propagate a change your colleague already made.**

1. The owning team migrates the first few call sites by hand
2. We diff before and after at each one
3. We extract the transformation pattern
4. A new call site matching a seen pattern → draft the diff
5. No match → flag only, no suggestion

**Confidence requires all of these:**

| Check | Why |
|---|---|
| Call shape matches a seen pattern | The obvious one |
| **Return value used the same way** | Assigned in one place, ignored in another means a different change |
| **Not inside error handling referencing old behavior** | A `try/except AuthError` means semantics may shift |
| Same argument types where inferable | Different types, different transformation |
| At least N prior examples | One example is a coincidence |

**Fail any check → flag, don't suggest.**

**The rule:** if we can't show you which of your own commits this is based on, we don't suggest it. Every drafted diff cites the SHAs it learned from.

**We publish suggestion precision, not coverage.** "We drafted 31 of 52, and 29 were applied unchanged" is a better number than "we drafted all 52."

---

## 10. How change radar works

Detect the change before the ticket exists. **Observed facts only, never inferred intent.**

| Signal | Alert? |
|---|---|
| Deprecation marker added to the API | **Yes.** Explicit |
| New function added alongside the old, same shape | **Yes** |
| Owning team's PR removes or rewrites the old path | **Yes** |
| Commit message or ticket says deprecate, migrate, sunset | Medium confidence, flag not alert |
| Old function's churn spikes after a year of silence | **No. Never alert on this** |

**Why the discipline matters:** two false "a change is coming" alerts and teams stop reading you entirely.

---

## 11. How we stay grounded

**The rule: if it isn't in the map, it doesn't appear in the output.**

- Agents never produce a number. Every count, date, score and file path comes from the map
- Any number or file path in agent text not present in the input gets stripped and replaced with a template
- Every claim cites a commit SHA, a ticket ID, or a file path
- Every drafted diff cites the migrations it learned from
- **We publish how often the gate fires.** Nobody else in this category reports how often their AI says something unsupported

**And the harder honesty:** we always report coverage.

> Found 47 call sites. 41 high confidence from static analysis, 6 from string matching. Estimated coverage 85%.

A migration tracker that silently misses call sites is **worse than a spreadsheet**, because people trust it and then delete the old code. The number is never optional.

---

## 12. Why this is defensible

**What we are not claiming**

- Not a better code parser. Sourcegraph parses at more scale
- Not automated refactoring. Moderne does that, with recipes someone writes
- Not self-maintaining code. Tools that open PRs already exist and get ignored, because nobody knows which of the 200 matter
- Not a novel model. Defect prediction is a 20-year academic field

**What is actually ours**

| | Why it holds |
|---|---|
| **The join** | Code plus ownership plus incident history, in one place. Sourcegraph has code. CodeScene has git. Jira has tickets. Nobody joins all three |
| **The history** | Accumulated daily and **impossible to backfill.** A competitor starting in 2028 cannot recover 2026 |
| **Internal APIs** | Moderne needs a recipe. Nobody will ever write one for your private auth client |
| **Both directions** | Owner burndown and consumer early warning from one map. Everyone else serves one side |
| **Honest coverage** | We report what we missed. Nobody does, and it is the first thing engineers test |

**Where the gaps are**

| Competitor | Has | Lacks |
|---|---|---|
| Sourcegraph | Finds call sites at scale | No ownership, no risk, no completion state |
| Moderne / OpenRewrite | Automated transformation, huge scale | Needs a recipe. Java-centric. No internal APIs |
| Dependabot / Renovate | Opens the PRs | Doesn't know if your code touches the broken part |
| Jira / Linear | Tracks tickets | Doesn't know the code |
| Jellyfish / Cortex | Team dashboards | Never names a file |
| CodeScene | Hotspots from git | Never joins to tickets |

---

## 13. The honest risks

| Risk | What we do |
|---|---|
| **We miss call sites** | Multiple detection modes: symbols, imports, string URLs, config files. Report coverage always. Let the user seed known examples |
| **Internal APIs are called invisibly** | An HTTP call with a string URL is not findable by a call graph. We say so and use pattern matching as a second pass |
| **Customer has no ticket-to-code links** | Report link coverage on connect. Map works, risk scoring doesn't |
| **Every Jira is configured differently** | Config on connect, not a universal connector. Budget weeks per unusual customer |
| **A wrong suggested diff** | Worse than no diff, because someone applies it unread. Hence the five-check confidence rule |
| **False change-radar alerts** | Observed facts only. Never infer intent from churn |
| **Moderne extends into internal APIs** | Likely one day. Our edge is coordination and non-Java, not transformation |

---

## 14. Build order

| Phase | Features | When |
|---|---|---|
| **Core** | 1, 2, 5, 13 | **Week 5 checkpoint.** If this works on one real repo, everything else is upside |
| **Coordination** | 3, 4, 6, 7, 9 | Weeks 6 to 7 |
| **Explanation** | 8, 12 | Week 8. Consumer inbox is cheap, a filter on the map |
| **Intelligence** | 10, 11 | Weeks 9+. Cut first if behind |

---

## 15. In one paragraph

Every company already has this data sitting in git and Jira. Nobody has joined it, because the join is annoying: no shared keys, identity that drifts, and it only means anything if you keep it every day. Meridian does the join, keeps the history, and uses it to run the migrations that never finish. Owning teams see who hasn't moved. Consuming teams find out before it lands, and get the change. And it always tells you what it missed.
