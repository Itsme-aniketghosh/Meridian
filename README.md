# Meridian

Meridian joins code, git and Jira to show every call site of a dying internal API, who owns it, how risky it is, the hidden cross-team chains between tickets, and why the work stalled.

What we hold to:

- Code is the truth. Jira is the plan.
- The map is append-only and dated.
- The same snapshot and config give the same output.
- We only suggest. No PRs, no filed tickets. Self-maintaining APIs come later.
- What isn't in the map isn't in the output, and we always report what we missed.

The data pipeline is in [Data-Pipeline/](Data-Pipeline/README.md), with its course report in [Data-Pipeline/report/REPORT.md](Data-Pipeline/report/REPORT.md). How to run the tests: [TESTING.md](TESTING.md).

| Doc | What's in it |
|---|---|
| [01 Problem](docs/01-problem.md) | Why migrations don't finish |
| [02 Product](docs/02-product.md) | Views, build order, gaps |
| [03 Data](docs/03-data.md) | Datasets, measured facts, schema |
| [04 Architecture](docs/04-architecture.md) | Exact rules per stage |
| [05 Test](docs/05-test.md) | Tests, pass bars, week-1 checks |
| [06 Dead ideas](docs/06-dead-ideas.md) | Rejected, and why |
