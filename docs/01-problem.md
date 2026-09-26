# Problem

## Setup

- 10 teams. Each owns code and calls code other teams own
- Every team is both owner and consumer, depending on the change
- Nobody can see past their own repo

## Example

- Platform retires `AuthClient.verify_token`: 52 call sites, 6 teams
- Everyone gets a ticket, some never do it, the old code gets deleted, things break
- Netflix found references to a deprecated internal logging library six years later
- A product engineer there: *"I don't have time for this, but if you do it for me,
  I'll merge the changes."* People don't refuse to migrate. They refuse to go find
  the work themselves

## Seven failures

1. Consumers find out late, then spend a day grepping
2. Jira says 31 closed, the code says 21 remain
3. CODEOWNERS is stale, so tickets land on the wrong team
4. All 52 look the same, but three sit in a file with 4 bug fixes this year
5. Teams block each other through a shared client and don't know it
6. Six teams remove call sites while a seventh adds three
7. When it stalls, nobody can say why

No tool covers #5 or #7.

## Why now

- YC RFS, [self-maintaining APIs](https://www.ycombinator.com/rfs#self-maintaining-apis)
  (Harsha Gaddipati): breaking changes go unnoticed. At AWS, over 30% of service
  downtime came from external API and package changes. The pitch: an agent that
  finds affected usages and opens the PR
- We point it inward. The company already owns every repo, commit, and ticket, so
  access isn't a problem
- OpenRewrite already migrates internal libraries. But:
  - someone has to write the recipe
  - it's strong in Java, thin in Python
  - it rewrites code, and doesn't say who owns it, what's risky, who blocks whom, or
    why it stalled

## Direction

- **Now:** find the work, the owners, the risk, the chains, and why things stalled.
  Suggest diffs and tickets. Track whether they get used
- **Later:** make the changes ourselves, once suggestions get applied unchanged
