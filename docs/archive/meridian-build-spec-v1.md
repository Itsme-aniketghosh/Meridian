# Meridian, How We Build It
### One page everyone should be able to hold in their head.

---

## The product, in one picture

```
   Your repos          Your git log        Your tickets
        │                    │                   │
   where is it         who works on        what broke
     called?            this file?          before?
        │                    │                   │
        └────────────────────┼───────────────────┘
                             ▼
                      ┌─────────────┐
                      │   THE MAP   │
                      │  one row    │
                      │ per call    │
                      │    site     │
                      └──────┬──────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
      Owner sees:                  Consumer sees:
      who's done, who's stalled    your 9 call sites
      full burndown                your deadline
```

**Everything we build serves that map.**

---

## The three jobs

| Job | What it means | Track |
|---|---|---|
| **Find** | Parse the code, find call sites | B |
| **Join** | Add owners from git, add risk from tickets | C |
| **Run** | Make it happen on schedule, without breaking | A |

Two people per track. Everyone owns their piece end to end.

---

## How it runs

```
  On schedule (daily, or every 3 days, configurable)
       │
       ▼
  Pull the repos
       │
       ▼
  What changed since last time?        ← only parse changed files
       │
       ▼
  Find call sites in changed files
       │
       ▼
  Add owner (from git) + risk (from tickets)
       │
       ▼
  Compare to the last map
       │
       ▼
  Write new rows. Never edit old ones.
       │
       ▼
  Update burndown + coverage report
```

Same loop, every run, for ten weeks.

---

## The infra, plainly

We have limited credits. So nothing runs when it isn't being used.

| What | Where | Why |
|---|---|---|
| **Parsing work** | Kubernetes, cheap nodes that **disappear when idle** | Runs 20 min a day. No point paying for the rest |
| **The schedule** | Argo Workflows | Each repo runs on its own. One failing doesn't break the rest |
| **Website + API** | Cloud Run | Sleeps when nobody's looking |
| **Database** | Cloud SQL | The only thing always on |
| **Raw snapshots** | GCS, one folder per run | Cheap. Lets us redo things if we get it wrong |
| **Alerts and graphs** | Cloud Monitoring | Built in, no extra setup |

**Billing alert goes up on day one.** A cluster quietly eating credits is what kills demos in week 8.

---

## Four rules we don't break

**1. Never edit a row. Only add.**
A migrated call site isn't deleted. It gets a new row saying "gone as of today." The history is the product.

**2. Every number has a source.**
If we say a file caused 4 incidents, we show the 4 ticket IDs. If we can't cite it, we don't say it.

**3. Always report what we missed.**
"Found 47 call sites, about 85% coverage." A tool that silently misses things is worse than a spreadsheet, because people trust it.

**4. The website never parses code.**
It reads the database. The pipeline does the work in the background. This is also why the demo won't break.

---

## Where the product features land

Nothing is dropped. The product doc has all 13. This is when each gets built.

### First: the foundation (weeks 2 to 4)

Not user-facing, which is why they aren't in the product doc's feature list. They are the whole first month.

| Piece | Track | What it is |
|---|---|---|
| **The code graph** | B | Parse every repo, find call sites, know what calls what |
| **Jira to git linking** | C | Connect tickets to the commits that fixed them |

If these aren't solid, nothing else works.

### Must have

One feature from each layer, so the product is complete end to end rather than deep in one place.

| # | Feature | Layer | Week |
|---|---|---|---|
| 1 | Impact map | Data | 5 |
| 2 | Ownership assignment | Data | 5 |
| 5 | Live burndown | Data | 5 |
| 13 | Coverage report | Data | 5 |
| - | **Blast radius** | Data | 6 |
| 3 | **Fragility score** | **ML** | 6-7 |
| 4 | **Migration order** | **Rules + ML** | 6-7 |
| 8 | **Migration brief** | **AI** | 7-8 |

**Why the last four aren't optional:**

- **Blast radius** answers "if this file changes, what else is affected." Cheap, because it's the same graph traversal as migration order. Most useful close to a release, when a lead is deciding whether a change is safe to ship this week
- **Fragility score** is the one model we must have. It tells a team which 3 of their 52 call sites are dangerous. Labels come free from the Jira-to-git join we're already building
- **Migration order** turns a list into a plan. Uses blast radius plus fragility
- **Migration brief** is the AI layer. Plain English per team: what to change, what's risky, what's blocked. Without it we're a dashboard

### Good to have (weeks 8+)

Cut these before touching anything above.

| # | Feature | Layer |
|---|---|---|
| 7 | Ticket sync (Jira claims vs what the code says) | Data |
| 6 | Stall detection | ML, second model |
| 9 | Drift alarm (new call sites after the freeze) | Data |
| 12 | Consumer inbox (each team sees only their slice) | Data |
| - | Migration ETA | ML, third model |

### Cut first (week 9+)

| # | Feature | Why it goes first |
|---|---|---|
| 10 | Suggested change (the diff) | Most likely to be visibly wrong on stage |
| 11 | Change radar | Needs high-confidence signals we may not have yet |

**The rule when we fall behind:** cut extra data features and extra models. Never cut blast radius, the one model, the ordering, the brief, or the coverage report. Those five are what make it a product rather than a report.

---

## Demo data

Two repos, two stories.

| Repo | What it proves |
|---|---|
| **Django** | Accuracy. Their published deprecation timeline gives us a known true answer, so we can measure recall |
| **Spark or Kafka** (Apache) | The Jira half. `issues.apache.org/jira` is publicly readable with no auth, and Apache requires a ticket key in PR titles and commits. Real tickets, real commits, real linkage |

**Say this in the demo before anyone asks:** this is real Jira data with real linkage. A company's will be messier, with lower link coverage, which is exactly why we report coverage on connect.

---

## Week 5, the checkpoint

Point it at Django. Pick a real deprecation they did.

It should show every call site with file and line, who owned each one, a burndown of how the real migration went, and a coverage number.

**Does the parser work?** Measure recall against the known true answer.

| Recall | What we do |
|---|---|
| 90%+ | Good. Move to week 6 |
| 70 to 90% | Move on, but one person keeps improving it |
| **Under 70%** | **Everyone stops.** Fix extraction before any week 6 work starts |

We cut features, not accuracy. A demo that finds 95% and says so beats a demo with more features that quietly misses 30%.

---

## Demo day

- Pipeline has been running since week 2
- Website reads the database, nothing live
- A status page showing 6 weeks of runs, failures caught, recoveries
- We rehearse with the wifi off

**The question we will get:** *"How do you know you found all the call sites?"*

**Our answer:** several detection methods, coverage reported every time, and a real recall number measured against Django where we know the truth.

Have that number ready.
