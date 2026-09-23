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
| [Django](https://github.com/django/django) | Completed deprecations with a documented timeline | Parser recall, change radar, replayed burndown, diff patterns |
| [Spark](https://github.com/apache/spark) + [Apache Jira](https://issues.apache.org/jira) | Tickets with commit links, 95% measured | Ticket-to-commit join, SZZ, fragility labels |
| Third-party Django packages | A deprecation crossing many repos and owners | Cross-repo, blast radius, stall labels — **unproven, week-1 check** |
| Our own repos | A codebase we understand completely | Sanity checks, seeding known call sites |
| [Defects4J](https://github.com/rjust/defects4j) | 854 curated bugs with fix commits, 17 Java projects | Fragility baseline sanity check |

### Django, for accuracy

The only dataset where we can know the true answer.

Django deprecates on a published schedule, marks it in code with
`RemovedInDjangoXXWarning`, and then removes it in a named release. That gives us
a list of real deprecations and an explicit signal for change radar to detect.

The timeline is at
[docs.djangoproject.com/en/stable/internals/deprecation](https://docs.djangoproject.com/en/stable/internals/deprecation/).

Two things have to be settled before any of it is usable, and both were measured
against the repo rather than assumed.

#### First, define call site

The word has two defensible meanings and they differ by 2.3x. Measured on
`ugettext` and friends at `4353640ea9`, the commit before Django migrated its own
usages:

| | Count |
|---|---|
| **Lines to edit** — references naming the symbol | **279** |
| — import statements | 110 |
| — direct invocations | 169 |
| **Invocations affected** — calls that reach the function | **649** |
| — direct | 169 |
| — through an alias | 480 |

Most Django code does `from django.utils.translation import ugettext_lazy as _` and
then calls `_('...')`. Those 480 calls are real invocations of a deprecated
function and not one of them contains the string `ugettext` — but none of them needs
editing, because fixing the import at the top of the file fixes every call below it.

**We count the 279.** The unit is *the line a human must change to complete the
migration*, because that is the unit the whole product is about: what to do, who
does it, is it done. The 649 is blast radius, reported separately and never mixed
into a burndown.

Two consequences worth stating before someone finds them.

The edit set here is exactly what a text search finds, so for this migration a grep
would produce the same burndown as our parser. Resolution earns its place on the
other two jobs — blast radius, and *not* counting an unrelated method that happens
to share a name. We should not claim the AST is what makes the burndown possible.

And the ground truth has an independent check. Django's own conversion commit
modified about 289 lines in `.py` files, against our reconstructed 279. Two methods,
built from different things, landing within 4%. That agreement is the reason to
trust the number, and it's worth running for whichever symbol we pick.

#### Second, the removal commit is not the ground truth

The plan used to say: find the commit that removed the API, treat every line it
changed as a true call site. That produces a set that is close to empty, because
Django migrates its own usages years before removal. Measured across 30 real
`per deprecation timeline` removal commits:

| Where the changed lines are | Share |
|---|---|
| `tests/` | 62.8% |
| `django/` source, nearly all of it the definition being deleted | 29.9% |
| `docs/`, including release notes | 7.3% |

Median removal commit: 72 lines across 4 files. Spot-checked on the candidates:
`make_random_password` removal is 4 files and 51 lines, `django.conf.urls.url()` is
4 files and 42 lines, `providing_args` is 3 files and 36 lines. Genuine call sites
in all three: zero.

Worse, there is often no single removal commit. `Meta.index_together` was deprecated
in May 2022 and its references were still being cleaned up in July 2025, across
thirteen commits. That is three years of trailing work, which is a point in favour
of the product's premise and fatal to any ground truth built from one diff.

#### So we build it from the code, not from a diff

1. Pick the symbol. Find the commit where Django migrated its own usages — the
   *conversion* commit, not the removal. For `ugettext` that is `c651331b34`,
   129 files and 717 lines, versus `52a238ddf2` at 4 files and 119 lines
2. Check out its parent. Enumerate every reference in `.py` files, excluding the
   module that defines the symbol
3. Hand-verify all of them and freeze the list in week 4, before the parser runs
4. Diff the frozen list against what the conversion commit touched

Step 4 is the one people skip. Ground truth taken from a commit measures agreement
with what Django's developers found, not truth. If our parser finds a live call site
they missed, a commit-derived set scores it as a false positive — penalising us for
doing the thing we are selling. Their misses are a result to report, not an error.

The conversion commit still does double duty: it holds before-and-after pairs for
every call site Django migrated, which is what the diff drafter learns from. The
removal commit holds none, which is why the drafter reads the conversion commit.

#### Which symbol, and why not the obvious one

Use `ugettext` and friends. It has a clean conversion commit, 279 verifiable
references, 118 files, an aliasing problem that exercises cross-file resolution, and
a known endpoint.

`Meta.index_together` looks like the better pick — it's the one that sounds widely
used — and it is the trap. Its removal commit is 1,349 lines, of which 84.4% is
deleted tests and 10.5% is the implementation machinery itself
(`options.py`, `autodetector.py`, `schema.py`). Its actual call sites are
`class Meta: index_together = [...]` declarations in *user* models, which do not
live in Django at all. Measuring parser recall against it would measure our ability
to find deleted test fixtures.

That makes `index_together` the wrong week-5 benchmark and the right week-9 one, as
soon as we have the cross-repo corpus below. It is a real migration whose call sites
are all in other people's repos, which is the actual product.

It's Python, which is the honest hard case. Dynamic dispatch through a factory or a
DI container is exactly what a naive parser misses, so a good number here means
something. It's also one repo, so it tests none of the cross-repo work.

### The cross-repo corpus, unproven and worth a week-1 check

Everything above is one repo, so it tests none of the cross-repo work — which is the
product. There is a candidate fix, and it is not yet confirmed.

When Django 4.0 removed `django.conf.urls.url()`, every third-party package using it
had to migrate. Those packages are separate repos, with separate owners, separate
velocities, and a shared upstream deadline. Some migrated immediately, some lagged,
some were abandoned mid-migration. That is the exact shape of the product, on public
data, with known outcomes — including real stalls, which is the one thing we
otherwise have no data for at all.

The evidence so far is thin and mixed. Django REST Framework has a clean single
migration commit (`Replace all url() calls with path() or re_path()`, 2020-09-08).
Wagtail has two, in 2018. Three other major packages checked showed nothing
obvious, which may mean they migrated differently or not at all.

So this is a week-1 spike, not a plan. The check: take twenty packages that depended
on Django 3.2, and for each try to locate the commit that migrated it off `url()`.

| Found in | Then |
|---|---|
| 12 or more | Build it. It becomes the week-9 benchmark and the stall dataset |
| 5 to 11 | Use it for blast radius and ownership only. Not for stall labels |
| Under 5 | Drop it. Say in the demo that cross-repo is untested, and don't claim stall accuracy at all |

Deciding this in week 1 costs a day. Discovering it in week 9 costs the demo.

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

[issues.apache.org/jira](https://issues.apache.org/jira) is publicly readable with
no auth — verified, the REST API answers anonymously and Kafka alone has about 9,000
bug issues. Apache asks for a ticket key in commit messages and PR titles, so the
link between a ticket and the code that fixed it is largely already there.

Largely, not entirely. Measured over the last 100 commits on each default branch:

| Project | Commits carrying a ticket key |
|---|---|
| Spark | 95% |
| Kafka | 62% |

The gap is the `MINOR:` convention, an explicit escape hatch for changes that need
no ticket. **Use Spark.** Kafka's linkage is worse than some real customers', which
makes it a bad place to develop the join and a fine place to test robustness later.

One thing to say out loud rather than be caught on: Apache Jira holds **bug
reports, not production incidents**. Nothing public holds incidents. So the
fragility model is trained on bug-fix history and must be described that way
everywhere in the product — see the wording rule in
[02-product.md](02-product.md). A real customer's incident data is strictly better
and is one of the things a first customer buys us.

### Defects4J, as a second opinion

[Defects4J](https://github.com/rjust/defects4j) is 854 curated real bugs across 17
Java projects, each with its fix commit, the triggering test, and the original issue
reference. It's Java, so it's no use to the parser.

It's useful for one thing: checking whether our fragility scoring is sane against a
dataset where somebody else already did the labelling carefully. If our SZZ pipeline
disagrees wildly with Defects4J's curated bug-to-commit links on the same projects,
the bug is in our pipeline.

### What we have no data for

Stall and ETA need completed migrations across teams inside one company. That data
does not exist publicly, and no amount of open source substitutes for it.

So those two ship as heuristics, labelled as heuristics, until a customer gives us
real history. Pretending otherwise would mean shipping a model trained on nothing
and letting someone plan a quarter around it.

### Tools

| | |
|---|---|
| [tree-sitter](https://tree-sitter.github.io/tree-sitter/) | Parsing. [Python grammar](https://github.com/tree-sitter/tree-sitter-python) |
| [PyDriller](https://github.com/ishepard/pydriller) | Commit, diff and authorship traversal. [Docs](https://pydriller.readthedocs.io/en/latest/) |
| [Jira REST API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/) | Ticket pull, configured per customer |

One thing to check rather than assume: the earlier draft of this plan said SZZ was
"already in PyDriller." PyDriller gives us commit and diff traversal and a helper
for finding the commits that last modified a set of lines, which is the core idea,
but that is not the same as a full SZZ implementation with the usual refinements.
Budget a week of Track C for this rather than an afternoon, and confirm what
PyDriller actually ships before building on it.

## The map

One row per call site per observation, appended forever.

```
call_site_id      stable across renames
repo, file, line
symbol            AuthClient.verify_token
detection         ast_resolved | ast_unresolved | import | string_url | config
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
reliable. A resolved AST match — where a resolver confirmed the name binds to the
symbol we're tracking — is near-certain. An unresolved one is a syntactic match
nobody has confirmed. A string URL match is a guess we're being honest about.
Keeping them apart is what lets us report coverage, and collapsing `ast_resolved`
and `ast_unresolved` into one `ast` value is how a coverage number quietly becomes
a lie.

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

Recall is quoted against the edit unit — the 279, not the 649 — and every number
carries which one it is.

The thing that will actually cost us the week: **tree-sitter alone cannot do this.**
It is a syntax parser. It produces a concrete syntax tree and performs no name
resolution, no type inference, and nothing across files. Given
`from django.utils.translation import ugettext_lazy as _`, tree-sitter can see the
import and it can see `_('...')`, but binding the second to the first is resolution,
and resolution is what tells you whether `self.auth.verify_token` is the symbol
being deprecated or an unrelated method with the same name.

So the extractor is two stages: tree-sitter for candidate extraction, then a
resolver — [Jedi](https://github.com/davidhalter/jedi) or
[pyright](https://github.com/microsoft/pyright) — to confirm the binding. An
unresolved candidate is not dropped, it is emitted at lower confidence and counted
in coverage. That second stage is weeks 2 to 4 work, not a week-5 surprise, and the
`detection` and `confidence` columns are meaningless without it.

### The models

Four small ones. Each has a stupid baseline it has to beat.

| Model | Dataset | Labels from | Baseline to beat |
|---|---|---|---|
| Fragility | Apache + Defects4J | SZZ: commits that caused ticketed incidents | the file that broke last time breaks next time |
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
