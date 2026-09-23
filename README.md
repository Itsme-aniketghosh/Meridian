# Meridian

Internal API migrations get announced, ticketed, and then never finish. Meridian
finds every call site, says who owns it, says which ones are dangerous, and explains
why a team has stopped.

It works by joining three things that live in every company and never get joined:
the code, the git history, and the tickets. That join is kept dated and append-only,
so we can answer what was true last Tuesday.

## Read in order

| | | |
|---|---|---|
| [01](docs/01-problem.md) | Problem | Why migrations don't finish, and why now |
| [02](docs/02-product.md) | Product | What a user actually sees |
| [03](docs/03-data.md) | Data | Sources, schema, how we test and train |
| [04](docs/04-architecture.md) | Architecture | Master diagram, plus one call site end to end |
| [05](docs/05-agents.md) | Agents | Workers and auditors |

Older drafts and the build plan are in [docs/archive/](docs/archive/).

## Scope right now

We suggest changes. We don't apply them, open PRs, or merge anything.

The thing we're measuring is whether the suggestions get used and whether the
burndown moves. Applying changes ourselves is where this goes later, once the
evidence says the suggestions are good enough.

## Two rules

If a number isn't in the map, it doesn't appear in the output. And we always report
what we missed.
