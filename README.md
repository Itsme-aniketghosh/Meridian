# Meridian

Internal API migrations get announced, ticketed, and never finish. Meridian joins
code, git history, and Jira to show every call site, its owner, its risk, the hidden
cross-team chains between tickets, and why work stalled.

- Code is the source of truth. Jira is the plan
- The map is dated and append-only, so we can answer what was true last Tuesday
- Deterministic: same snapshot + same config = same output

## Docs

| | | |
|---|---|---|
| [01](docs/01-problem.md) | Problem | Why migrations don't finish |
| [02](docs/02-product.md) | Product | What each user sees |
| [03](docs/03-data.md) | Data | Sources, datasets, measured facts, schema |
| [04](docs/04-architecture.md) | Architecture | The pipeline, stage by stage, with exact rules |
| [05](docs/05-test.md) | Test | How each component is tested, pass bars, week-1 checks |
| [06](docs/06-dead-ideas.md) | Dead ideas | What we rejected and why |

Older drafts: [docs/archive/](docs/archive/).

## Scope

- We suggest. We don't apply changes, open PRs, merge, or file or edit tickets
- Success = suggestions get used and the burndown moves
- Later: make the changes ourselves (self-maintaining APIs), once the evidence supports it

## Rules

- Not in the map, not in the output
- Always report what we missed
