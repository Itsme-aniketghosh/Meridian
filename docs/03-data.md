# Data

Three sources every company already has. None of them is hard to get. The work is
in joining them.

| Source | What it gives us | How |
|---|---|---|
| Your code | Where the API is called | tree-sitter parse of every repo |
| Your git history | Who actually works on each file | `git log` per file, recent commit share |
| Your tickets | Which files caused incidents | Jira API, then SZZ to the causing commit |

Code tells us where the work is. Git tells us who should do it, which is usually
more accurate than CODEOWNERS. Tickets tell us which of it is dangerous.

## Datasets we have

No customer yet, so everything below is public. Each one exists to answer a
specific question, and together they cover all of the product except the two models
that need a real company.

| Dataset | Gives us | Used for |
|---|---|---|
| Django | Completed deprecations with a documented timeline | Parser recall, change radar, replayed burndown |
| An Apache project (Spark or Kafka) | Jira tickets with enforced commit links | Ticket-to-commit join, SZZ, fragility labels |
| Our own repos | A codebase we understand completely | Sanity checks, seeding known call sites |
| ApacheJIT or similar | Pre-labelled bug-inducing commits | Fragility baseline comparison |

### Django, for accuracy

The only dataset where we can know the true answer.

Django deprecates on a published schedule, marks it in code with
`RemovedInDjangoXXWarning`, and then removes it in a named release. That gives us
three things at once: a list of real deprecations, an explicit signal for change
radar to detect, and a removal commit we can turn into ground truth.

Candidates to pick from in week 1: `django.conf.urls.url()`, `providing_args` on
Signal, `ugettext` and friends. Whichever we choose, the job is to find the commit
that removed it, treat every line it changed as a true call site, hand-verify the
list, and freeze it before running our parser.

The same removal commit does double duty. It contains before-and-after pairs for
every call site Django migrated, which is exactly what the diff drafter learns
transformation patterns from. We get the ground truth and the training examples out
of one commit.

It's Python, which is the honest hard case. Dynamic dispatch through a factory or a
DI container is exactly what a naive parser misses, so a good number here means
something. It's also one repo, so it tests none of the cross-repo work.

### Replaying Django to get a time series

We don't have ten weeks of history. But Django already has it, so we borrow it.

Check the repo out at a commit before the deprecation, run our pipeline, step
forward a week at a time to the removal commit, and write a map row at each step.
What comes out is a real burndown against a migration that genuinely happened, with
real ownership, real blocking, and a known endpoint.

This is worth doing early. It gives us a populated product to look at in week 5
rather than a table with four rows in it, and it exercises `effective_date` versus
`known_at` on real data instead of a fixture.

### An Apache project, for the ticket half

Django won't teach us anything about Jira. Apache will.

`issues.apache.org/jira` is publicly readable with no auth, and Apache requires a
ticket key in commit messages and PR titles, so the link between a ticket and the
code that fixed it is already there. That's what SZZ needs to produce fragility
labels, and it's the half of the join that's hardest to fake.

Check in week 1 that Spark and Kafka still enforce the ticket key. Apache projects
have been changing tooling and the whole ticket track rests on this.

One caveat to say out loud in a demo: this is unusually clean data. A real customer
will have lower link coverage, which is exactly why we measure link coverage on
connect and report it.

### What we have no data for

Stall and ETA need completed migrations across teams inside one company. That data
does not exist publicly, and no amount of open source substitutes for it.

So those two ship as heuristics, labelled as heuristics, until a customer gives us
real history. Pretending otherwise would mean shipping a model trained on nothing
and letting someone plan a quarter around it.

## The map

One row per call site per observation, appended forever.

```
call_site_id      stable across renames
repo, file, line
symbol            AuthClient.verify_token
detection         ast | import | string_url | config
confidence
owner_team        from recent commit share
fragility         0-1, plus the ticket IDs behind it
status            present | removed | new
blocked_by        upstream call_site_id
effective_date    when it became true
known_at          when we observed it
run_id
```

Three fields carry more weight than they look.

`detection` records which method found it, because the methods aren't equally
reliable. An AST match is near-certain, a string URL match is a guess we're being
honest about. Keeping them apart is what lets us report coverage.

`status` is how progress gets tracked without anyone filing an update. A call site
that disappears doesn't get deleted, it gets a new row saying gone as of today. The
burndown is a count over rows.

`blocked_by` puts cross-team blocking in the data instead of in someone's head.

## Two clocks

`effective_date` is when it became true. `known_at` is when we saw it.

A call site removed Tuesday but observed Thursday is not the same fact as one
removed Thursday. Collapse them and you can't reconstruct an honest burndown, since
you'd credit Tuesday's progress to Thursday.

## The hard parts

**Nothing shares a key.** Git has SHAs and paths, Jira has ticket IDs, the parser
has symbols. This is where most of the engineering time goes.

**Identity drifts.** Renames, moved functions, three emails per person, bots in the
authorship signal. Each one quietly corrupts ownership.

**Cross-repo is the real shape.** Fifteen repos, not one.

**Incremental parsing is a trap.** Safe for detecting removals. Unsafe when
resolution crosses files, since a re-export in `a.py` changes how `b.py` resolves
without touching it. Reverse-dependency invalidation plus a weekly full re-parse.

**Some calls aren't findable.** An HTTP call with a string URL can't be found by a
call graph at all, so pattern matching runs as a second pass and everything it finds
is labelled lower confidence.

## How we test and train

### The parser

Django gives us the ground truth, built as described above. Recall is the number
that matters, because a missed call site is what gets live code deleted. We report
precision too, and we break both down by detector so we know whether AST matching is
carrying the result or string matching is quietly doing the work.

### The models

Four small ones. Each has a stupid baseline it has to beat.

| Model | Dataset | Labels from | Baseline to beat |
|---|---|---|---|
| Fragility | Apache + ApacheJIT | SZZ: commits that caused ticketed incidents | the file that broke last time breaks next time |
| Suggestion confidence | Django, then real usage | Applied clean, edited, or ignored | always confident |
| Stall | none yet | Team went quiet past N days, then resumed or didn't | the team with the most open tickets stalls |
| ETA | none yet | Past migration velocity per team | remaining / average rate |

The bottom two have no dataset, which is the point made above: nothing public
contains cross-team migration history. They ship as labelled heuristics.

The fragility baseline is the one to watch. "Broke last time, breaks next time" is
genuinely hard to beat. If we don't beat it, we ship the baseline as the score, cite
the ticket IDs, and say it's a heuristic. That's still useful. We don't pretend a
model won when it didn't.

Three rules:

- **Split by time, never at random.** A random split trains on a file's 2026 incident
  to predict its 2025 fragility. The offline numbers look great and mean nothing
- **Report calibration, not accuracy.** A 0.8 has to be wrong one time in five
- **Abstention ships with an answer rate.** Models say "don't know" when history is
  thin, which makes accuracy gameable. Accuracy on 40% of call sites is not better
  than slightly lower accuracy on 95%

### The agents

Outcomes arrive free from normal use.

Every suggested diff ends up applied clean, applied with edits, or ignored. That's
the cleanest feedback loop in the product and it's also the main signal for whether
the whole thing works. We publish precision, not coverage: "drafted 31 of 52, 29
applied unchanged."

When a stall breaks, we check which theory predicted it. Early on N is small, so any
claim about auditor accuracy ships with its N.

The grounding gate counts how often it strips an unsupported number from agent text,
and we publish that rate.

## Coverage, two numbers

**Recall is measured.** Django, known deprecation, ground truth from the removal
commit. Real and defensible.

**Coverage is estimated**, because a customer's repo has no ground truth. Three
ways, report the spread: agreement between independent detectors, recapture against
call sites the customer seeds by hand, and the parse-failure count. When they
disagree, report the low one.

Don't blur these in a demo. An engineer in the room will notice.

## When things break

| Failure | What happens |
|---|---|
| Jira unreachable | Progress from code alone, flagged partial |
| Repo force-pushed | Halt. Do not silently reconcile |
| A file fails to parse | Excluded, counted, reported. Never silently dropped |
| No ticket-to-code links | Map and ownership work. Risk scoring doesn't, and we say so |
| Rate limited mid-run | Checkpoint, back off, resume. No partial writes |

Link coverage between tickets and commits varies wildly by customer. We measure it
on connect and say so immediately rather than finding out in week three.
