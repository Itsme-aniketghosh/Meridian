# What's possible and what isn't

Every line here is backed by a measurement against a real repo, not an estimate.
Where something is unproven, it says so and names the check that would settle it.

The short version: the data layer is real and mostly already verified. The model
layer splits cleanly into one model we can train and two we cannot. The gap between
those two facts is where this plan either stays honest or quietly stops being true.

## Proven

| Claim | Evidence |
|---|---|
| We can enumerate call sites exactly | 279 references to `ugettext*` across 118 files at `4353640ea9`, reconstructed two independent ways that agree within 4% |
| A completed migration can be replayed as a time series | Django's own conversion commit `c651331b34` is the endpoint: 129 files, 717 lines, dated |
| Ticket-to-commit linkage is available | Spark carries a ticket key on 95% of the last 100 commits. Apache Jira's REST API answers anonymously |
| Ownership is derivable from git | `git log` per file gives commit share directly. No config, no permission |
| Deprecations are detectable as they're announced | Django marks them `RemovedInDjangoXXWarning` in code and publishes a dated timeline |

That covers the map, ownership, burndown, coverage, change radar, and the ticket
join. It is most of the product and the least glamorous part of it.

## Unproven, and decidable in a day each

These are the [three week-1 spikes](archive/07-build-plan.md). Each is cheap now and
expensive in week 9.

**Cross-repo.** Everything proven above is one repo. The candidate corpus is
third-party packages that migrated off `django.conf.urls.url()` when Django 4.0
removed it. Django REST Framework has a clean single migration commit; Wagtail has
two; three other major packages showed nothing obvious. That is not yet a dataset.
Twenty packages checked settles it.

**Resolution quality.** Whether Jedi or pyright can bind the 480 aliased `_('...')`
calls back to `ugettext_lazy`. This decides whether blast radius is a real feature
or a diagram.

**Hand-verification cost.** Whether freezing a 279-line ground truth takes an
afternoon or four days. The week-4 freeze is load-bearing for the week-5 gate.

## Not possible with any data we can get

This is the section to read twice, because each of these is a feature currently
drawn in the architecture.

**A trained stall model.** It needs completed migrations across multiple teams
inside one company, with dates and outcomes. No public dataset contains this. Open
source has no sprint boundaries, no reorgs, no manager telling a team to drop it —
which are the actual causes of stalls. Ships as a hand-written heuristic, labelled
as one.

**A trained ETA model.** Same reason. Velocity per team across a shared deadline
doesn't exist publicly.

**Fragility from production incidents.** Every public source holds *bug reports*.
None holds incidents. The model is trainable — just on bug-fix history, which is a
weaker and quieter signal than a page at 3am. The product must say "bug fixes"
wherever it currently says "incidents".

**Calibrated auditor accuracy.** Theories are scored when a stall breaks. In ten
weeks we will have a handful of stalls, possibly none. Any accuracy claim ships with
its N, and at N=3 the honest statement is that we have no accuracy number yet.

**The replay / self-improvement loop.** It needs recorded decision history that
won't exist for a year. [The archived write-up](archive/06-self-improvement.md)
already says this. It is the right architecture and the wrong quarter, and it should
not appear in a demo as though it runs.

## One thing that is possible but doesn't prove what we'd want it to

On `ugettext`, the 279 lines a human must edit are exactly what a plain text search
finds. A grep would produce the same burndown as the parser.

That doesn't make the parser pointless — resolution is what gives blast radius, and
what stops an unrelated method with the same name from being counted. But it does
mean **the burndown is not evidence that the AST work was necessary**, and we should
not present it that way. Pick the demo claim that the measurement actually supports.

## What would change these verdicts

One customer with real history moves four of them at once: incidents instead of bug
reports, completed internal migrations for stall and ETA, real link coverage,
real outcome labels on suggested diffs.

That is worth stating plainly, because it reframes the first customer. They are not
just revenue — they are the only source of the data that three of these features
need. Until then those features are heuristics, and the plan says so in the same
words on every screen that shows one.
