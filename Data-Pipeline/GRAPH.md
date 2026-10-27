# Graph · step 1

Aniket's part of the pipeline plan: which call site waits on which, how far each symbol reaches, and ticket-to-ticket chains.

```
python -m meridian.graph --run-id <run_id> --config meridian.toml      # from Data-Pipeline/
```

| Reads | Writes |
|---|---|
| `out/<run_id>/`: `call_site` (each site's newest version), `call_edge`, `resolution`, `ticket_map`, `ticket` | `out/<run_id>/blocked_by.jsonl`, `blast_radius.jsonl`, `chain.jsonl` |

## Verdict

| Task | Bar | Got | |
|---|---|---|---|
| One fixture per rule | fixtures 100% | `blocked_by` (depth 1–3, max depth, owners, `unknown`, no receiver), blast radius (each edge once, at its shortest depth), cycles: **13 of 13** tests, the collision rule included in `tests/test_graph.py` | ✅ |
| The KB's hand-filled rows | exact | graph computes the KB fixture's `blocked_by`, `blast_radius` and `chain` rows exactly, from that fixture's inputs | ✅ |
| Loads with the writer, same run same bytes | 0 diffs | both, on the KB fixture | ✅ |
| Run on the team block `70dd1dabfed544b8` | runs without errors | **0** `blocked_by`, **25** `blast_radius`, **43** `chain` (all `untracked`, one per team), 2 s. `call_site.jsonl` sha256 `faff6dfd75931183`, as the team's | ✅ |
| Precision ≥ 0.90 vs the GitLab gold set | | blocked: 0 of 262 human verdicts, and see Flags | ⏳ |
| Hand-check 50 Spark collision pairs, generic names dropped first | precision reported | model read of 50 (`tests/collisions.py`, `labels/spark_collision_pairs_50.csv`): hunk headers **0.64**; with the inside rule **1.00** (32 of 32), missing no real pair. Human check of the same 50 still to do | 🟡 |

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

## On 70dd1dabfed544b8

- **No `blocked_by`, by the data.** The resolver logs 0 `receiver_from` in every repo: `url()` and `ugettext()` are always called directly, never as `x = make(); x.method()`. Code-level blocks stay on fixtures (KB gap G7).
- **Blast radius is where the resolver pays.** Django `ugettext`: 93 call sites, 585 invocations in 78 files (209 / 241 / 135 at depth 1 / 2 / 3). `django-allauth` `url`: 50 sites, 124 invocations, 86 at depth 2. Grep sees only the direct sites.
- **Call edges: 2,293 here vs 2,288 in RESOLVER.md.** The whole difference is Django's (1,191 vs 1,186; the packages give 1,102 both times). Not the Python version and not the `array.py` fix: on Windows, Python 3.12 before the fix and Python 3.11 after it write byte-identical `call_edge.jsonl` (sha256 `8c6b9450d25c2fdb`). Still open: compare with the machine that wrote 2,288 (its sha256, or the 5 Django edges it lacks). `call_site` doesn't read edges; `blast_radius` does.

## Flags

- **Chains will be nearly empty on the team run.** Django and the packages have no tracker, and Spark has no tracked symbols, so no ticket maps to a call site. Expect `untracked` only. Known (KB gap G7, 02-product): chains are tested on fixtures.
- **The gold-set bar doesn't fit code-level chains.** The GitLab gold set is ticket-level, and GitLab has no Python code, so graph can't produce those rows. Needs a decision from Utkarsh and Aniket on what precision is measured against.
- **`collides` is by function only.** 04-architecture says "shared function or file"; the KB query uses the function. This follows the KB. Generic names (`apply`, `__hash__`) aren't filtered: scopes are qualified by path, so they don't merge across files.
- **Depth.** `blocked_by` can need more than 3 levels (RESOLVER.md). Raising `blocked_by_max_depth` past `[resolver] call_edge_depth` finds nothing more: the edges aren't collected.
- `file_owner` isn't read: `call_site.owner_team` already comes from it.

## Spark collisions

Week 1's collision check, on the team run: two first-parent Spark commits keyed to different Bug+Fixed tickets, within 30 days, editing the same function. 1,241 fix commits since 2024 → 682 ticket pairs share a file, 144 a function (by git's hunk header), 83 after dropping generic names (`apply`, `__hash__`, …) and names defined more than once in their file.

**Hunk headers name the wrong function a third of the time.** A header is the nearest `def` above a change, so a new method, a class field or a sibling function added after `f` counts as an edit of `f`. In the 50-pair sample, 18 were like that (model read, notes in the CSV). The **inside rule** keeps an edit only if a changed line is in the function's body: below its def, deeper than it, with nothing in between back at the def's depth. On the same 50 it agrees with the read every time: 32 kept, all real. It leaves **62 pairs, 12 with both tickets open at once** (what P3 fires on).

Caveats: the rule was written after reading this sample, so a fresh sample (or the human check) has to confirm it; and the read is a model's (`checked_by = model`), not yet a person's.

**Proposal for the precision bar** (Utkarsh and Aniket to decide): graph's code-level rules are scored on fixtures (100%) and on these Spark collisions (≥ 0.90 after the human check), because the GitLab gold set is ticket-level and graph has no GitLab code to produce its rows. The gold set then scores ticket-level chains (`ticket_link` based) when the stall stage builds them.

```
python -m tests.collisions --run-id 70dd1dabfed544b8 --config meridian.toml    # keeps verdicts already filled in
```

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
