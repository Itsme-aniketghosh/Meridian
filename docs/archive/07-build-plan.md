# Build plan

10 weeks. Supersedes both archived drafts, which disagreed with each other.

## Tracks

| Track | Owns |
|---|---|
| A · Run | Pipeline, schedule, infra, cost, status page |
| B · Find | Parsing, call-site extraction, code graph |
| C · Join | Ownership from git, Jira linking, SZZ, map schema |
| D · Reason | Models and agents |

D doesn't exist until week 5. Nobody builds a fragility model before the Jira join
lands, so D forms by pulling one from B and one from C. The old plan had three
data tracks and three mandatory ML/agent features with nobody assigned.

## Schedule

| Week | What |
|---|---|
| 1 | Repo + Jira access, billing alert. **Three spikes, below** |
| 2–4 | Foundation. Code graph (B), resolver (B), Jira-to-commit linking (C), daily pipeline (A). Ground truth frozen by end of week 4 |
| 5 | Gate. Features 1, 2, 5, 15 on Django. Measure recall |
| 6–7 | 6 blast radius, 3 fragility, 4 order, 7 stall detection |
| 7–8 | 11 brief, 8 stall diagnosis, 13 consumer inbox |
| 9–10 | 9 ticket sync, 10 drift, 12 suggested diff, 14 change radar |
| Stretch | 16 explorer, replay-based policy improvement |

Two things moved:

- Stall detection came forward. Auditors are useless without it
- Consumer inbox came forward. Demoting it gutted one of five moat claims, and
  it's a filter on the map. A day of work

## The three week-1 spikes

Each one can invalidate a chunk of the plan. Each costs about a day. All three run
before any foundation work, because all three change what the foundation is.

**1. Does the cross-repo corpus exist?** Twenty packages that depended on Django
3.2; for each, find the commit that migrated it off `url()`. 12+ found and it
becomes the week-9 benchmark and our only stall dataset. Under 5 and we drop it and
stop claiming cross-repo accuracy. Thresholds in [03-data.md](../03-data.md).

**2. Can a resolver actually bind the aliases?** Point Jedi and pyright at Django at
`4353640ea9` and see how many of the 480 aliased `_('...')` calls each one resolves
back to `ugettext_lazy`, and how long a full pass takes. This decides the extractor
architecture. If neither resolves better than about 80%, the honest move is to scope
the product to direct references, restate what a call site means, and shrink the
blast-radius claim accordingly — in week 1, not week 6.

**3. How long does hand-verification actually take?** Verify 30 of the 279
references and multiply. The week-4 freeze is load-bearing for the week-5 gate, and
"hand-verify the list" is the kind of line that reads like an afternoon and costs
four days. If the extrapolation is over two days, cut the ground truth to a subset
of files chosen in advance and report recall on that subset, which is still honest.

Write down all three answers. Two of them are things a sharp person in the room will
ask about, and having measured them is the difference between an answer and a
flinch.

## Protect / cut

- Protect: map, ownership, burndown, coverage, blast radius, fragility, order,
  brief, stall diagnosis. Those nine make it a product not a report
- Cut in this order: change radar, suggested diff, drift alarm, ticket sync
- The suggested diff is the most likely thing to be visibly wrong on stage
- Explorer and the replay loop are stretch, and should be. Three recorded
  migrations is nothing to dream over. Right architecture, wrong week

## Week 5 gate

Point it at Django. Pick a deprecation they completed.

Ground truth was undefined in the old plan and everything hangs off it:

- Django's timeline lists deprecated APIs, not call sites
- The removal commit is **not** the ground truth. Measured over 30 real removal
  commits, 62.8% of changed lines are deleted tests, 7.3% are docs, and nearly all
  the rest is the definition being deleted. Median removal commit: 72 lines, 4
  files, zero call sites. Build it from the code at the conversion commit's parent
  instead. Full procedure and numbers in [03-data.md](../03-data.md)
- Hand-verify, freeze it in week 4, before running anything. Otherwise you'll
  define truth as whatever the parser found

The symbol is `ugettext` and friends. Conversion commit `c651331b34`, 118 files, 279
references, a known endpoint. **Not** `Meta.index_together` — its removal commit is
84% deleted tests and its real call sites are in other people's repos.

Recall is measured against the edit unit: the 279 lines a human must change. Not the
649 calls affected through aliases, which are blast radius. One definition, stated
on every number.

| Recall | Do |
|---|---|
| 90%+ | Move to week 6 |
| 70–90% | Move on, one person keeps improving it |
| Under 70% | Everyone stops. Fix extraction first |

These thresholds are only meaningful because the true set is 279, not 8. On a set of
eight, 90% is one miss and the gate measures nothing.

One result to prepare for rather than be ambushed by: on this symbol a plain text
search finds the same 279. If the parser scores 95% and grep scores 95%, the right
answer is to say so, and to point at what resolution actually buys — blast radius,
and not counting an unrelated method with the same name. Claiming the AST won a race
it didn't run is how you lose the room.

The remaining hole is that Django is one repo, so the gate still tests none of the
cross-repo work. That is what the week-1 cross-repo spike is for. If that spike
fails, we go to demo day knowing cross-repo is unproven and we say so, rather than
finding out on stage.

## Demo data

- Django proves accuracy. Published timeline to measure against, and Python
  dynamic dispatch is the honest hard case
- Spark proves the Jira half. `issues.apache.org/jira` is public and readable with
  no auth — verified, the REST API answers anonymously
- Spark, not Kafka. Measured over the last 100 commits on each: Spark carries a
  ticket key on 95%, Kafka on 62%, the gap being Kafka's `MINOR:` escape hatch

Say before anyone asks: real Jira data, real linkage, and here is the measured link
coverage rather than an assurance that it's high. A company's will be messier, which
is why we report link coverage on connect.

Say it too: Apache Jira holds bug reports, not production incidents. Fragility is
trained on bug-fix history. Anyone who has run a platform team will spot the
difference, and pre-empting it costs one sentence.

## Demo day

- Pipeline running since week 2. Eight weeks of real runs
- Site reads the DB, nothing live
- Status page: run history, failures caught, recoveries
- Rehearse with the wifi off

The question we'll get: how do you know you found all the call sites?

Answer: several detection methods, coverage on every output, and a measured recall
number from Django. Have it ready, and don't blur it with the coverage estimate.
