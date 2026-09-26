# Data

## Sources

| Source | Gives | How |
|---|---|---|
| Code | Call sites | tree-sitter, then a resolver |
| Git | Owners, timing | `git log`, first-parent history |
| Jira | Tickets, bug history | REST API, SZZ |
| Intake | Problem reports | Slack, support tools, GitHub issues |

## Datasets (no customer yet)

| Dataset | Gives | Used for |
|---|---|---|
| [Django](https://github.com/django/django) | Completed deprecations on a published [timeline](https://docs.djangoproject.com/en/stable/internals/deprecation/) | Parser recall, replayed burndown, diff patterns |
| [Spark](https://github.com/apache/spark) + [Apache Jira](https://issues.apache.org/jira) | Ticket keys in commits, resolutions, links | Ticket join, SZZ, fragility, ticket tools |
| Third-party Django packages, `url()` to `path()` | One deprecation across many repos | Cross-repo, blocking. Unproven, week-1 check |
| [Defects4J](https://github.com/rjust/defects4j) | 854 curated bugs, 17 Java projects | Fragility sanity check |
| GitHub issues | needs-info, needs-repro, duplicate labels | Vague-ticket and duplicate labels |
| Our own repos | Code we know fully | Fixtures |

## Measured

**Django**
- `ugettext*` at `4353640ea9`: 279 lines to edit (110 imports, 169 calls) in 118 files
- 649 invocations, including 480 through an alias (`_('...')`)
- Conversion commit `c651331b34`: 129 files, 717 lines. It touched about 289 `.py`
  lines vs our 279, so the two methods agree within 4%
- For `ugettext`, grep finds the same 279 lines. So the burndown doesn't prove the
  resolver is needed; blast radius and name collisions do
- Removal commits hold about zero call sites. Across 30 of them: 62.8% tests, 29.9% the
  definition itself, 7.3% docs. Median 72 lines, 4 files
- `index_together`: cleanup took 13 commits over three years (2022 to 2025).
  References grew from 195 to 208 during deprecation: scaffolding, not usage

**Spark and Apache Jira** (queried 2026-09-25)
- 95% of the last 100 commits carry a ticket key (Kafka: 62%)
- 59,365 tickets, 6,023 open. Jira's REST API answers without login
- Resolutions: Fixed 39,367 · Incomplete 3,359 · Duplicate 2,827 · Not A Problem 2,007
  · Invalid 1,524 · Cannot Reproduce 534
- 3,214 of the 3,359 Incomplete are `bulk-closed`, leaving 145 real ones
- 1,036 tickets ever carried a blocks or is-blocked-by link
- Newest 500 tickets: 5% have any link, none use "Blocks", 45% are sub-tasks, and 25%
  of top-level tickets have no description

## Definitions

- **Call site:** a line a human must edit to finish the migration (the 279)
- **Blast radius:** every invocation the symbol reaches, including aliases (the 649).
  Never counted in the burndown
- **Ground truth:** every reference at the parent of the conversion commit,
  hand-verified and frozen in week 4. Call sites Django missed are reported, not
  scored as our errors
- **Fragility:** counts bug fixes on public data, or incidents when a customer
  connects an incident tracker. Named for what it counts

## Schema

All tables are append-only. A row is written only when something changes.

**run**
```
run_id  snapshot_at  repo_shas{}  jira_uri  intake_uri  config_hash  image_digest  model_versions{}
```

**call_site**
```
call_site_id    content hash, stable across moves (rules in 04)
repo, file, line, scope
symbol          AuthClient.verify_token
detection       ast_resolved | ast_unresolved | string_url | config
confidence      the detector's measured precision
owner_team      from commit share, or unowned
bug_fixes       count, plus ticket IDs
status          present | removed
blocked_by      call_site_id[] | unknown | none
effective_date  first-parent commit date when it became true
known_at        snapshot_at of the run that saw it
run_id
```

**ticket_map**
```
ticket_id  target (call_site_id | file)  method (commit_key | path | stack_trace | symbol)  run_id
```

**chain**
```
from_ticket  to_ticket  type (depends_on | collides | duplicates)  evidence[] (call_site_ids)  run_id
```

**suggestion**
```
suggestion_id  kind (diff | ticket | rewrite | solution | reason | nomination)  source (codemod | llm | template)  policy_id  facts_hash  outcome  run_id
```

**policy**
```
policy_id (code hash)  job (stall | explore)  version  author (human | llm-proposed, human-approved)  created_at
```

**match**
```
match_id  task_id  policy_a  policy_b  result (1 | 0.5 | 0)  verdict_source (rule | human | replay)  decided_by  decided_at
```

## Hard parts

- Git, Jira, and the parser share no key
- Identity drifts: renames, several emails per person, bots
- Cross-repo: 15 repos, not one
- Resolution crosses files, so naive incremental parsing is wrong
- Calls made by string URL don't show up in a call graph

## Tools

- [tree-sitter](https://tree-sitter.github.io/tree-sitter/),
  [Python grammar](https://github.com/tree-sitter/tree-sitter-python)
- Resolver: [pyright](https://github.com/microsoft/pyright) or
  [Jedi](https://github.com/davidhalter/jedi), chosen by a week-1 check and pinned
- [PyDriller](https://github.com/ishepard/pydriller) for commit traversal. It isn't a
  full SZZ; we build SZZ on top (budget a week)
- [Jira REST API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/)
