# Problem

## The setup

Ten dev teams. Each owns some code, each calls code other teams own.

```
Platform  ──owns──>  AuthClient
                         ▲
              ┌──────────┼──────────┐
           Checkout   Payments   Fulfillment
```

Every team is both an owner and a consumer depending on the change, so we can't
build for one or the other. And nobody can see past their own repo.

## What goes wrong

Platform retires `AuthClient.verify_token`. 52 call sites, 6 teams. Everyone gets a
ticket, everyone schedules it in their own sprint, some never do it. Months later
someone deletes the old code and things break.

Netflix deprecated an internal logging library and found references everywhere six
years later. A product engineer told them:

> *"I don't have time for this, but if you do it for me, I'll merge the changes."*

That's the thesis. People aren't refusing to migrate, they're refusing to go find
the work themselves.

Seven failures, each one measurable:

1. Consumers find out late, then spend a day grepping to see if it applies
2. Jira says 31 closed, the code says 21 remain, nobody reconciles the two
3. CODEOWNERS is stale and the author left, so tickets land on the wrong team
4. All 52 look identical in a spreadsheet. Three sit in a file with 4 incidents
5. Teams block each other through a shared client and neither one knows
6. Six teams remove call sites while a seventh adds three
7. When it stalls, nobody can say why

Number 7 is the gap nobody tools for. A flat line tells you a team stopped. It
doesn't tell you the owner left in March.

## Why now

YC asked for this: [self-maintaining APIs](https://www.ycombinator.com/rfs#self-maintaining-apis),
written by Harsha Gaddipati. His framing:

> "Breaking changes ship with little warning. Useful features quietly launch and go
> unnoticed. Changelogs don't get read."

At AWS, he says, over 30% of service downtime came from external API and package
changes going unnoticed. The solution he describes is an agent that scans customer
codebases, finds affected usages, and opens a PR with the fix.

We point the same idea inward, at internal APIs, for two reasons.

Access is trivial. The external version means convincing Stripe to scan your
codebase, or convincing you to trust a third party. Internally, the company already
owns every repo, commit, and ticket.

The honest version of the tooling gap: OpenRewrite exists, and it came out of
exactly the Netflix story above — a central team automating the migration of a
*private, internal* library. So "tools can't do internal APIs" is false, and we
shouldn't say it.

What's true is narrower. Someone has to write the recipe, nobody in the community
will write one for your auth client, and the teams who'd benefit are the ones
without a platform group to spend a week on it. OpenRewrite is also strongest in
Java and much thinner in Python, which is where we're measuring.

And a recipe rewrites code. It doesn't tell you who owns each call site, which ones
are dangerous, who is blocking whom, or why Checkout stopped three weeks ago. That's
the gap — not transformation, but everything around deciding whether to run it.

## Where we start, and where this goes

The RFS ends at "open the PR." We don't start there.

**Now:** find the work, say who owns it, say which parts are dangerous, explain
what's stuck. Suggest diffs. Track whether anyone uses them.

**Later:** when the suggestions are good enough that people apply them unchanged,
making the change ourselves stops being a leap. That's the self-maintaining API
version, and it's a direction, not a plan. The evidence has to come first.
