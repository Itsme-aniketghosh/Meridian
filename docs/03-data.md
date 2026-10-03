# Data

## Sources

Code (tree-sitter + resolver) · git log (owners, timing) · Jira REST (tickets, SZZ) ·
intake (Slack, support, GitHub issues).

## Datasets

| Dataset | Used for |
|---|---|
| [Django](https://github.com/django/django) | Parser recall, burndown replay, diffs, push replay |
| [Spark](https://github.com/apache/spark) + [Apache Jira](https://issues.apache.org/jira) | Ticket join, SZZ, fragility, ticket tools, push replay |
| Django packages, `url()` → `path()` | Cross-repo scanning (19 of 20 migrations found). Not blocking: they don't call each other |
| [Defects4J](https://github.com/rjust/defects4j) + Fonte | SZZ answer key: 130 bug-inducing commits, mapped to v3 |
| [GitLab](https://gitlab.com/gitlab-org/gitlab) issues | Cross-team blocks, stalls (one repo, ~100 teams) |
| GitHub issues | Vague and duplicate labels |

## Measured

**Django**
- `ugettext*` at `4353640ea9`: 265 lines to edit (113 import, 151 call, 1 alias), 115 files.
  Plus 2 in `tests/i18n/commands/code.sample`, outside `.py`
- 542 alias calls `_()` on 485 lines, 96 files
- Conversion commit `c651331b34` (2017) changed 285 `.py` lines: the 265, plus 20 non-uses
- Grep finds all 265 plus 19 non-uses, and 0 alias calls. The old 279 was grep minus the
  5 definitions. The resolver is justified by blast radius, not burndown
- Removal commits hold ~0 call sites (62.8% tests, 29.9% definition)

**Spark + Jira** (snapshot 2026-10-01)
- 94.0% of the last 1,000 commits carry a ticket key (Kafka: 62%). 50 of 50 links correct
- 59,491 tickets, 6,046 open, public REST
- Incomplete 3,359 (96% bulk-closed → 141 real) · Duplicate 2,828 (2,094 usable) ·
  Cannot Reproduce 534 (513 usable)
- 1,036 tickets ever had a blocks link. Newest 500: 5% linked, 0 "Blocks", 25% of
  top-level have no description

**GitLab** (issues created Jan–Jun 2025)
- 14,653 human-filed issues (17,502 more from a test bot, dropped). 1,649 "blocked by" links
- Cross-team blocks: 30+ with a written wait or stated prerequisite, in two independent
  passes. Median wait 74 days. Read by a model, not yet spot-checked
- Stalls: 57 blocked issues read, 50 with a written reason. Blocked issues miss their
  milestone 36% of the time vs 16%
- Debian, OpenStack, Mozilla checked too: 0, 0 and 3–4 real cross-team waits

## Definitions

- **Call site:** a line a human must edit (the 265)
- **Blast radius:** every invocation reached, including the 542 alias calls. Not in burndown
- **Ground truth:** references at the conversion commit's parent, hand-verified, frozen week 4
- **Fragility:** bug-fix count on public data

## Schema

Append-only. A row is written only on change.

```
run          run_id snapshot_at repo_shas{} config_hash image_digest model_versions{}
call_site    call_site_id repo file line scope symbol detection confidence owner_team
             bug_fixes status(present|removed) blocked_by effective_date known_at run_id
ticket_map   ticket_id target method(commit_key|path|stack_trace|symbol) run_id
chain        from_ticket to_ticket type(depends_on|collides|duplicates) evidence[] run_id
suggestion   suggestion_id kind source(codemod|llm|template) policy_id facts_hash outcome
             agent model route(jev|rules) run_id
policy       policy_id job(stall|explore) version author created_at
match        match_id task_id policy_a policy_b result verdict_source decided_by decided_at
push_alert   push_sha base_run_id check(P1–P9) target evidence[] outcome
```

## Tools

[tree-sitter](https://tree-sitter.github.io/tree-sitter/) ·
[pyright](https://github.com/microsoft/pyright) or [Jedi](https://github.com/davidhalter/jedi)
(week-1 pick) · [PyDriller](https://github.com/ishepard/pydriller) (SZZ built on top)
