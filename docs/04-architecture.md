# Architecture

## Master diagram

```
        YOUR REPOS            YOUR GIT LOG           YOUR TICKETS
      where is it called?   who works on this?     what broke before?
             │                     │                      │
             └─────────────────────┼──────────────────────┘
                                   ▼
┌─ STEP 1  ·  COLLECT ───────────────────────────────────────────────┐
│ clone N repos      git log --follow      Jira REST                 │
│ every run, dated. raw snapshot to object storage per run.          │
└────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─ STEP 2  ·  EXTRACT ───────────────────────────────────────────────┐
│ tree-sitter      ->  candidates: file, line, symbol, shape         │
│ resolver         ->  confirms the binding. unresolved = lower conf │
│ blame + log      ->  commit share per file, per person             │
│ SZZ              ->  ticket -> fix commit -> causing commit        │
└────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─ STEP 3  ·  JOIN          >>>  THE MAP  <<< ───────────────────────┐
│ one row per call site per day. append-only, never edited.          │
│ file | line | symbol | owner | fragility | status | blocked_by     │
│ effective_date = became true    known_at = we saw it               │
└────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─ STEP 4  ·  GRAPH ─────────────────────────────────────────────────┐
│ nodes  call site -> file -> module -> repo -> team                 │
│ edges  calls, imports, owns, blocks                                │
│ gives  blast radius, blocking chains, migration waves              │
└────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─ STEP 5  ·  MODELS   (ML) ─────────────────────────────────────────┐
│ fragility     stall     ETA     suggestion confidence              │
│ train on the past, test on the future. abstain when thin.          │
└────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─ STEP 6  ·  AGENTS   (LLM) ────────────────────────────────────────┐
│ WORKERS   analyst · sequencer · diff drafter · briefer             │
│ AUDITORS  why is this stuck? ranked theories + evidence            │
└────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─ STEP 7  ·  GROUNDING GATE ────────────────────────────────────────┐
│ every number traces to a map row, or it does not ship              │
└────────────────────────────────────────────────────────────────────┘
                                  ▼
┌─ STEP 8  ·  VIEWS ─────────────────────────────────────────────────┐
│ OWNER     burndown, stalls, theories, coverage                     │
│ CONSUMER  your 9 call sites, your deadline, your diffs             │
└────────────────────────────────────────────────────────────────────┘
```

Steps 1 to 4 are data engineering and they're the entire first month. 5 and 6 are
thin layers on top. 7 is what keeps the output trustworthy.

## One call site, end to end

Platform is retiring `AuthClient.verify_token`. Follow a single line of Checkout's
code through the whole pipeline: `cart/pricing.py:214`.

**1. Collect.** We clone the checkout repo, read its full git log, and pull the
Jira project. The raw pull is saved and dated before anything touches it, so if we
get an extractor wrong in week 6 we can re-derive week 2 instead of losing it.

**2. Extract.** Three extractors run independently.

- tree-sitter walks the AST and finds the candidate `self.auth.verify_token(tok)` at
  line 214. Tree-sitter stops there — it is a syntax parser and does not know what
  `self.auth` is. A resolver then confirms `self.auth` is an `AuthClient`, which is
  what promotes this from a name that looks right to a call site we'll stake a
  burndown on
- blame on `pricing.py` says `r.mehta` has 62% of recent commits, `s.liu` has 21%
- SZZ walks from ticket INC-4471 to the commit that fixed it, back to the commit
  that caused it, which touched `pricing.py`. That's the fourth time this year

They don't talk to each other. A failure in one doesn't corrupt the others.

**3. Join.** One row gets written:

```
call_site_id   cs_8812
file, line     cart/pricing.py:214
symbol         AuthClient.verify_token
detection      ast_resolved confidence 0.97
owner_team     checkout     (r.mehta, 62% commit share)
fragility      0.81         (INC-4471, INC-4102, INC-3988, INC-3771)
status         present
effective_date 2026-09-22   known_at 2026-09-22
```

**4. Graph.** `pricing.py` imports `shared/client.py`, which also calls
`verify_token` and is owned by Platform. That gives us an edge, and it's why
Checkout's row will later get a `blocked_by` pointing upstream.

**5. Models.** Fragility scores 0.81, driven by those four incidents. The stall
model doesn't fire yet, because Checkout committed three days ago.

**6. Agents.** The call-site analyst reports the return value is assigned and the
call sits inside `except AuthError`. The diff drafter checks that against its
confidence rules, fails the error-handling check, and refuses to draft. It emits a
flag instead. Nineteen days later, when Checkout has gone quiet, an auditor picks
this up as evidence for its "semantics unclear" theory.

**7. Gate.** The brief text says "appeared in 4 incidents this year." The gate
checks that 4 against the map, finds four ticket IDs behind it, and lets it
through. If the agent had written 5, it would have been stripped.

**8. Views.** Platform sees it in the burndown as one of Checkout's nine. Checkout
sees it in their brief, near the top, marked risky and flagged rather than drafted.

**Tomorrow.** Someone adds two lines above it and the call moves to 216. The
`call_site_id` stays `cs_8812`, so it doesn't show up as a new call site and the
burndown doesn't lie. When it's finally migrated, we don't delete the row. We append
a new one with `status: removed`, and the burndown ticks from 31 to 32.

## The daily loop

```
pull repos -> re-parse changed files -> diff against yesterday
           -> update the map -> re-score -> regenerate briefs
           -> run auditors on anything stuck
           -> alert on stalls, blocks, and new call sites
```

The website never triggers any of this.

## Infra

Credits are limited, so nothing runs when it isn't being used.

| What | Where |
|---|---|
| Parsing | Cloud Run Jobs, one execution per repo |
| Schedule | Cloud Scheduler |
| Site + API | Cloud Run |
| Database | Cloud SQL, the only always-on thing |
| Raw snapshots | GCS, one folder per run |
| Alerts | Cloud Monitoring |

Not Argo on Kubernetes. Argo needs a live control plane and an always-on controller,
so a pipeline that runs twenty minutes a day would pay continuously for an
orchestrator idle 98% of the time. That's the quiet credit burn that kills a demo in
week 8. Cloud Run Jobs gives the same per-repo isolation with nothing running
between runs.

Billing alert goes up on day one.

## Four rules

**Never edit a row, only add.** The history is the product.

**Every number has a source, named for what it is.** Four incidents means four
ticket IDs from an incident tracker. If the tickets are bug reports — which is all
any public dataset gives us, and so all the demo has — the screen says four bug
fixes. The walkthrough above is a customer with a real incident tracker.

**Always report what we missed**, and keep measured recall separate from estimated
coverage.

**The website never parses code.** It reads the database while the pipeline works in
the background. Also why the demo won't break on stage.
