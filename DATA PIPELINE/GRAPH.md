# Graph · step 1

Aniket's part of the [pipeline plan](PIPELINE-PLAN.md): which call site waits on which, how far each symbol reaches, and ticket-to-ticket chains.

```
python -m meridian.graph --run-id <run_id> --config meridian.toml      # from DATA PIPELINE/
```

| Reads | Writes |
|---|---|
| `out/<run_id>/`: `call_site` (each site's newest version), `call_edge`, `resolution`, `ticket_map`, `ticket` | `out/<run_id>/blocked_by.jsonl`, `blast_radius.jsonl`, `chain.jsonl` |

## Verdict

| Task | Bar | Got | |
|---|---|---|---|
| One fixture per rule | fixtures 100% | `blocked_by` (depth 1–3, max depth, owners, `unknown`, no receiver), blast radius (each edge once, at its shortest depth), cycles: **12 of 12** tests in `tests/test_graph.py` | ✅ |
| The KB's hand-filled rows | exact | graph computes the KB fixture's `blocked_by`, `blast_radius` and `chain` rows exactly, from that fixture's inputs | ✅ |
| Loads with the writer, same run same bytes | 0 diffs | both, on the KB fixture | ✅ |
| Run on the team block `70dd1dabfed544b8` | runs without errors | not yet: needs the snapshot zip on this machine | ⏳ |
| Precision ≥ 0.90 vs the GitLab gold set | | blocked: 0 of 262 human verdicts, and see Flags | ⏳ |
| Hand-check 50 Spark collision pairs | precision reported | not done | ⬜ |

## How it decides

- **`blocked_by` A → B:** A is a present call `recv.name(...)` whose `recv = F(...)` (`resolution.receiver_from`). B is a present call site inside F (depth 1), or inside a function F reaches through `depth − 1` resolved calls in `call_edge`, up to `[graph] blocked_by_max_depth`. The shortest depth wins. `via_function` is F.
- **Different owners:** A's and B's `owner_team` differ and neither is `unowned`. An unowned side can't show the block crosses teams, so it is left out (counted in the log).
- **`unknown`:** when A or B is `ast_unresolved`. Without `receiver_from` there is no F, so no row.
- **Blast radius** per (repo, symbol): `direct_sites` = present call sites. `invocations` = `call_edge` rows reaching the symbol. Depth 1 is an edge at a resolved `resolution` row of the symbol (calls and alias calls). Depth n is a call of a function holding a depth n − 1 one, up to `blast_radius_max_depth`. Each edge counts once, at its shortest depth. `files` = distinct caller files.
- **Chains** go through `ticket_map` rows with `target_kind = 'call_site'`:
  - `depends_on`: a key on A and another on B, evidence `A>B`.
  - `duplicates`: two keys on one site.
  - `collides`: two open keys (`[rules] done_statuses`) on sites in the same function (`repo:path:scope`) that share no site. As in the KB, a mapped site counts even if now removed.
  - `untracked`: one row per owner team, holding its present sites that no key maps to.
  - `in_cycle = 1` on a `depends_on` chain whose `to_key` leads back to its `from_key`.

## Flags

- **Chains will be nearly empty on the team run.** Django and the packages have no tracker, and Spark has no tracked symbols, so no ticket maps to a call site. Expect `untracked` only. Known (KB gap G7, 02-product): chains are tested on fixtures.
- **The gold-set bar doesn't fit code-level chains.** The GitLab gold set is ticket-level, and GitLab has no Python code, so graph can't produce those rows. Needs a decision from Utkarsh and Aniket on what precision is measured against.
- **`collides` is by function only.** 04-architecture says "shared function or file"; the KB query uses the function. This follows the KB. Generic names (`apply`, `__hash__`) aren't filtered: scopes are qualified by path, so they don't merge across files.
- **Depth.** `blocked_by` can need more than 3 levels (RESOLVER.md). Raising `blocked_by_max_depth` past `[resolver] call_edge_depth` finds nothing more: the edges aren't collected.
- `file_owner` isn't read: `call_site.owner_team` already comes from it.

## Planned features that read graph's tables

Graph's rows on the KB fixture are exactly the ones the KB proof filled by hand, so every query there that reads `blocked_by`, `blast_radius` or `chain` gives the doc's answer on graph's own output: `staff_chains`, `owner_teams` (blocked), `consumer_blocked`, `consumer_blocking`, `stall_upstream`, `order_of_work`, and P4 (base vs push `blocked_by`).

- **Tracking (suggestion, outcome, push_alert) is not built.** The DDL is proposed and tested in [KNOWLEDGE-BASE.md](KNOWLEDGE-BASE.md), not in `schema.sql`, and no part writes it (gap G1: stage 6). It doesn't need anything from graph beyond `call_site` history and `blocked_by`.
- **`order_of_work` never ends on a `blocked_by` cycle.** Its recursive `UNION ALL` has no cycle guard: A waits on B and B on A loops forever. Graph doesn't flag site-level cycles (04 flags them on chains). A fix for the rules stage, which owns the order.

## Dashboard

```
python -m tests.dashboard --run-id <run_id> --config meridian.toml      # -> out/<run_id>/dashboard.html
```

One local HTML page per run: rows per table, coverage, call sites by repo, team and role, `blocked_by` by depth and team pair, blast radius by depth, chains, ticket mapping, and every named KNOWLEDGE-BASE.md query run on the block (has rows, no rows, or error), with parameters taken from the run's biggest team and file. It loads the block with `meridian.writer` first, so it also proves the block loads. It stays under `out/`, which git ignores: it shows ticket keys and, in the stall queries, people's names.

## Rerun

```
python -m meridian.graph --run-id <run_id> --config meridian.toml
python -m unittest tests.test_graph -v
```
