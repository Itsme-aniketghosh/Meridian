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
| 1 | Repo + Jira access, pick the target deprecation, billing alert |
| 2–4 | Foundation. Code graph (B), Jira-to-commit linking (C), daily pipeline (A) |
| 5 | Gate. Features 1, 2, 5, 15 on Django. Measure recall |
| 6–7 | 6 blast radius, 3 fragility, 4 order, 7 stall detection |
| 7–8 | 11 brief, 8 stall diagnosis, 13 consumer inbox |
| 9–10 | 9 ticket sync, 10 drift, 12 suggested diff, 14 change radar |
| Stretch | 16 explorer, replay-based policy improvement |

Two things moved:

- Stall detection came forward. Auditors are useless without it
- Consumer inbox came forward. Demoting it gutted one of five moat claims, and
  it's a filter on the map. A day of work

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
- Find the commit that removed the API, take every changed line as a true call site
- Hand-verify, freeze it in week 4, before running anything. Otherwise you'll
  define truth as whatever the parser found

| Recall | Do |
|---|---|
| 90%+ | Move to week 6 |
| 70–90% | Move on, one person keeps improving it |
| Under 70% | Everyone stops. Fix extraction first |

Two holes: the true set may be small, so 90% on eight call sites is one miss. And
Django is one repo, so the gate tests none of the cross-repo work. If week 4 has
slack, add a second ecosystem repo calling a deprecated Django API.

## Demo data

- Django proves accuracy. Published timeline to measure against, and Python
  dynamic dispatch is the honest hard case
- Spark or Kafka proves the Jira half. `issues.apache.org/jira` is public, no
  auth, and Apache requires ticket keys in commits and PR titles
- Verify in week 1 that both still enforce it. Track C rests on it

Say before anyone asks: real Jira data, real linkage. A company's will be messier
with lower link coverage, which is why we report link coverage on connect.

## Demo day

- Pipeline running since week 2. Eight weeks of real runs
- Site reads the DB, nothing live
- Status page: run history, failures caught, recoveries
- Rehearse with the wifi off

The question we'll get: how do you know you found all the call sites?

Answer: several detection methods, coverage on every output, and a measured recall
number from Django. Have it ready, and don't blur it with the coverage estimate.
