# Problem

- 10 teams. Each owns code and calls code other teams own. Nobody sees past their repo
- Platform retires `AuthClient.verify_token`: 52 call sites, 6 teams. Tickets go out,
  some never get done, the old code is deleted, things break
- Netflix found calls to a deprecated internal logging library six years later. *"I
  don't have time for this, but if you do it for me, I'll merge the changes."*

## Failures

1. Consumers find out late
2. Jira says 31 closed, code says 21 remain
3. CODEOWNERS is stale, tickets go to the wrong team
4. Risky call sites look like the rest
5. Teams block each other through a shared client and don't know it
6. One team adds call sites while others remove them
7. Nobody knows why it stalled

No tool covers #5 or #7.

## Why now

- YC RFS, [self-maintaining APIs](https://www.ycombinator.com/rfs#self-maintaining-apis):
  over 30% of AWS downtime came from external API and package changes
- We point it inward: the company owns every repo, commit, and ticket
- OpenRewrite migrates internal libraries, but needs a written recipe, is thin in
  Python, and ignores owners, risk, blocks, and stalls

## Direction

- **Now:** find the work, owners, risk, chains, stall reasons. Suggest. Track use
- **Later:** make the changes ourselves, once suggestions get applied unchanged
