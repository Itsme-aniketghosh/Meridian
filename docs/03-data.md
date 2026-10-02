# Data

## Sources

Code (tree-sitter + resolver) · git log (owners, timing) · Jira REST (tickets, SZZ) ·
intake (Slack, support, GitHub issues).

## Datasets

| Dataset | Used for |
|---|---|
| [Django](https://github.com/django/django) | Parser recall, burndown replay, diffs, push replay |
| [Spark](https://github.com/apache/spark) + [Apache Jira](https://issues.apache.org/jira) | Ticket join, SZZ, fragility, ticket tools, push replay |
| Django packages, `url()` → `path()` | Cross-repo, blocking. Unproven |
| [Defects4J](https://github.com/rjust/defects4j) | Fragility sanity check |
| GitHub issues | Vague and duplicate labels |

## Measured

**Django**
- `ugettext*` at `4353640ea9`: 279 lines to edit (110 imports, 169 calls), 118 files
- 649 invocations, 480 through an alias `_()`
- Conversion commit `c651331b34` agrees within 4%
- Grep also finds 279, so the resolver is justified by blast radius, not burndown
- Removal commits hold ~0 call sites (62.8% tests, 29.9% definition)

**Spark + Jira** (2026-09-25)
- 95% of commits carry a ticket key (Kafka: 62%)
- 59,365 tickets, 6,023 open, public REST
- Incomplete 3,359 (3,214 bulk-closed → 145 real) · Duplicate 2,827 · Cannot Reproduce 534
- 1,036 tickets ever had a blocks link. Newest 500: 5% linked, 0 "Blocks", 25% of
  top-level have no description

## Definitions

- **Call site:** a line a human must edit (the 279)
- **Blast radius:** every invocation reached, including aliases (the 649). Not in burndown
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
