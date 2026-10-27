# Knowledge base · step 1 · v1.1

The knowledge base (KB) is the one SQLite file each run writes: every table in [schema.sql](schema.sql), filled by the seven parts. Everything Meridian plans to show (the four views, the ticket tools, the push check, the stall reasons, the auditor) has to be answerable from it, because the views are read-only and rules never look anywhere else.

This page checks that claim against the plan ([02-product](../docs/02-product.md), [04-architecture](../docs/04-architecture.md), [05-test](../docs/05-test.md)). Every query below runs in CI against a fixture KB ([tests/test_kb.py](tests/test_kb.py)) and must return exactly its `-- expect:` line. If the schema loses a column a view needs, or this page claims something the KB can't answer, the test fails.

```
python -m unittest tests.test_kb -v        # from Data-Pipeline/
```

**What the proof shows and what it doesn't.** It shows the KB can carry and answer each feature. The fixture fills `call_site`, `ticket_map`, `blocked_by`, `chain` and `blast_radius` by hand, so the proof needs no real data, and the `derive_*` queries show graph's tables can be computed from the other parts' inputs. It says nothing about numbers on real data: that's the [testing plan](#testing-plan).

## What's in it

25 tables in nine layers. v1.1 added the five marked **new**, each because a planned feature had nothing to read.

| Layer | Tables | Written by | What it holds |
|---|---|---|---|
| Run | `run` | collect | Snapshot time, repo SHAs, source hashes, config hash, status (`partial` says what's missing) |
| History | `git_commit`, `commit_file` | history | Every commit (first-parent flag, author, time) and every file it touched, with lines added and deleted |
| Tickets | `ticket` (+ **`description`**), `ticket_event`, `ticket_link`, `commit_ticket` | history | Tickets rolled back to `snapshot_at`, every status, label and link change, links with `removed_at`, commit → ticket keys |
| Bugs | `bug_inducing`, `file_bug_fixes` | history (SZZ) | Which commit introduced each fixed bug; bug fixes per file in the last 365 days, with their ticket keys |
| Code | `candidate` (+ **`in_except`**), `parse_failure`, **`module_import`**, **`deprecation`** | scan | Every place a tracked name appears, its role and scope; files that didn't parse; every import of every file; every function that announces it's going away |
| Resolution | `resolution` (+ **`receiver_from`**), `call_edge` | resolve | What each candidate really is; for `x.f()`, which function made `x`; who calls whom, up to `call_edge_depth` |
| People | `person`, `person_alias`, `team_map`, `file_owner` | owners | Merged identities, a team per person or path, an owner team per file with its share |
| Join | `call_site`, `ticket_map`, `coverage` | join | One versioned row per call site (append-only, `known_at`), ticket → code, what we missed |
| Graph | `blocked_by`, `blast_radius`, `chain` | graph | Which site waits on which, how far a symbol reaches, ticket-level chains |

## Does it support everything we planned?

✅ answered by the KB today (query named, proven below) · 🟡 answerable, but only partly or with a caveat · ⛔ needs a table a later stage adds · ❌ gap

### The four views (02-product)

| Feature | Status | Reads | Proof |
|---|---|---|---|
| Staff: call sites remaining, open tickets | ✅ | `call_site`, `ticket_map`, `ticket` | `staff_headline` |
| Staff: hidden chains, "Jira links N of them" | ✅ | `chain`, `ticket_link` | `staff_chains` |
| Staff: collisions, duplicates | ✅ | `chain` (derived from `ticket_map` + `call_site.scope`) | `staff_chains`, `derive_collides`, `derive_duplicates` |
| Staff: untracked work | ✅ | `call_site`, `ticket_map` | `staff_untracked` |
| Staff: vague tickets → rewrites | ✅ new | `ticket.description` (**new**: was only `desc_empty`) | `staff_vague`, `manager_facts` |
| Staff: link coverage, tickets unmapped | ✅ | `ticket_map`, `coverage` | `staff_link_coverage` |
| Staff: teams not connected | ✅ | `call_site`, `ticket_map` | `stall_never_notified` |
| Staff: legacy radar | 🟡 | `deprecation` (**new**), `call_edge` | `radar_deprecated_with_callers`. Fan-in signals need the full call graph: gap G3 |
| API owner: N of M done, ETA | ✅ | `call_site` versions (`effective_date`) | `owner_done`, `owner_eta` |
| API owner: per team left, moving / blocked / stalled, days, risky | ✅ | `call_site`, `blocked_by`, `file_bug_fixes` | `owner_teams` |
| API owner: "3 possible reasons" | ✅ | see stall reasons | `stall_*` |
| Consumer: sites per file, bug fixes this year | ✅ | `call_site`, `file_bug_fixes` | `consumer_files` |
| Consumer: blocked by shared client; "you are blocking X" | ✅ | `blocked_by`, `call_site.owner_team` | `consumer_blocked`, `consumer_blocking` |
| Consumer: one-line swaps, diffs | ⛔ | `suggestion` (stage 6) | G1 |
| Success: used, edited, ignored; burndown before vs after; week-8 review; auto-apply gate | ✅ proposed | `suggestion`, `outcome`, `push_alert` (DDL below), `call_site` | `outcome_*`, `auto_apply_gate` |
| Developer: push notes P1–P9 | ✅ (P8 in Python) | see push check | `p1_*` … `p9_*` |

### Ticket tools

| Feature | Status | Reads | Proof |
|---|---|---|---|
| Manager: flag vague, rewrite from map facts | ✅ new | `ticket.description`, `call_site`, `in_except` (**new**: "two calls sit inside `except AuthError`") | `staff_vague`, `manager_facts` |
| Maker: one draft per team per wave | ✅ | untracked `call_site` rows | `maker_drafts` |
| Intake: Slack, support, GitHub issues | ❌ | nothing collects intake yet | G5 |

### Rules (04 §5) and stall reasons

| Rule | Status | Reads | Proof |
|---|---|---|---|
| Risky: `bug_fixes` ≥ 3 | ✅ | `call_site.bug_fixes` | `owner_teams` |
| Stall: present, no change in 14 days | ✅ | `call_site.effective_date` | `owner_teams` |
| ETA: remaining ÷ removals/day over 28 days, < 3 → none | ✅ | `call_site` versions | `owner_eta` |
| Order: topological on `blocked_by`, then `bug_fixes`, then ID | ✅ | `blocked_by`, `call_site` | `order_of_work` |
| Fragility ships with ticket IDs | ✅ | `file_bug_fixes.fix_keys` | `p7_risky_edit` |
| #1 Upstream block | ✅ | `blocked_by` | `stall_upstream` |
| #2 Never notified | ✅ | `ticket_map` | `stall_never_notified` |
| #3 Ticket mismatch | ✅ | `ticket`, `ticket_map`, `call_site` | `stall_ticket_mismatch` |
| #4 Orphaned owner: top author silent 60 days | ✅ | `file_owner.top_person_id`, `person_alias`, `git_commit` | `stall_orphaned_owner` |
| #5 Semantics unclear: inside `except` | ✅ new | `call_site.in_except` (**new**: nothing recorded it) | `stall_semantics_unclear` |
| #6 Fragility freeze: all remaining risky | ✅ | `call_site.bug_fixes` | `stall_fragility_freeze` |
| #7 Test gap: no test imports the module | ✅ new | `module_import` (**new**: imports weren't kept, `call_edge` excludes them by rule) | `stall_test_gap` |
| #8 Competing load: no commits to call-site files, team at ≥ 75% of usual | ✅ | `git_commit`, `commit_file`, `person_alias`, `team_map` | `stall_competing_load` |
| Ticket checks: unmapped, no done condition, bug without repro | ✅ new | `ticket_map`, `ticket.description` (**new**) | `staff_vague` |
| Ticket checks: spans teams, duplicate, wrong owner | ✅ | `ticket_map`, `call_site.owner_team`, `ticket.components` | `derive_duplicates`, `check_wrong_owner` |
| Ticket checks: stale (30 days, code changed) | ✅ | `ticket_event`, `commit_file` | not in the fixture |

### Graph (04 §4)

| Rule | Status | Reads | Proof |
|---|---|---|---|
| `blocked_by` A → B: different owners, A's receiver made by a call to F, B inside F | ✅ new | `resolution.receiver_from` (**new**: nothing said where a receiver came from), `call_site`, `call_edge` for depth > 1 | `derive_blocked_by` |
| Blast radius, depth ≤ 3 | ✅ | `call_edge` | `derive_blast_radius` |
| Chains: `depends_on`, `duplicates`, `collides`, untracked | ✅ | `blocked_by`, `ticket_map`, `call_site.scope` | `derive_depends_on`, `derive_duplicates`, `derive_collides`, `staff_untracked` |
| Cycles flagged | ✅ | `chain.in_cycle` | graph's own fixture |
| Chains across repos | 🟡 | `call_edge.callee_repo` carries it | No source has real cross-repo calls: fixtures only (known, 02-product) |

### Push check (04 §10)

| Check | Status | Reads | Proof |
|---|---|---|---|
| P1 Drift: new call site | ✅ | base vs push `call_site`. At push time the resolver drops look-alikes, as join does: 4 of 4 true on the 21-commit replay | `p1_drift` |
| P2 Legacy use: new call to a deprecation-marked symbol | ✅ new | `deprecation` (**new**), `resolution.resolved_to` | `p2_legacy_use` |
| P3 Collision: edits a function mapped to another team's open ticket | ✅ | `ticket_map`, `call_site.scope`, `ticket` | `p3_collision` |
| P4 New block | ✅ | base vs push `blocked_by` | same pattern as `p1_drift` |
| P5 Ticket can move | ✅ | `ticket_map`, `call_site`, `ticket` | `p5_ticket_can_move` |
| P6 Mismatch: re-adds a site of a Done ticket | ✅ | same | `stall_ticket_mismatch` |
| P7 Risky edit | ✅ | `file_bug_fixes` | `p7_risky_edit` |
| P8 Bad key: commit names a key not in the snapshot | ✅ | `git_commit.subject`, `ticket` | the key regex runs in Python (SQLite has no regex): `tickets.py` already counts dead keys |
| P9 Defect risk: lines × files | ✅ | `commit_file` | `p9_defect_risk` |
| Scope: changed files + their importers | ✅ new | `module_import` (**new**) | `push_scope_importers` |

### Auditor learning (04 §9), replay, determinism

| Feature | Status | Reads |
|---|---|---|
| Replay with `known_at` ≤ stall time only | ✅ | `call_site.known_at` · proof `replay_as_of` |
| Explore: deprecation marker with callers | ✅ new | `deprecation`, `call_edge` · `radar_deprecated_with_callers` |
| Explore: same-signature twin gaining callers | ❌ | needs every function's signature: G4 |
| Explore: no commits in 2 years and fan-in ≥ 10; high `bug_fixes` and fan-in | 🟡 | commits ✅, but fan-in exists only above tracked symbols: G3 |
| Policies, matches, Elo, verdicts | ⛔ | `policy`, `match` tables (03-data), stage 9: G1 |
| Same snapshot → same bytes; partial runs flagged | ✅ | `run` |

## The proof

### The fixture

One repo, `acme/shop`, and the story from 02-product told small. Two runs a month apart: R1 at 2026-09-01, R2 at 2026-10-01. `AuthClient.verify_token` is being retired. Its own definition warns with `DeprecationWarning`.

| Team | Call sites | What happened |
|---|---|---|
| platform | `shared/client.py:40` in `get_client` | Untouched since January. Fulfillment's call goes through this client |
| checkout | `cart/pricing.py:214`, `:231` (inside `except`), `:250`; `cart/totals.py:57` | `:250` removed Sep 12. `pricing.py` has 4 bug fixes. Its top author, Ann, last committed Jul 1. Bob is busy on `cart/ui.py` |
| fulfillment | `ship/label.py:10` | Its receiver comes from `get_client()`: blocked by platform |
| payments | `pay/charge.py:5`, `:9`, `pay/refund.py:3`, new `pay/charge.py:30` | Two removed in late September, one new call added Sep 28 |
| billing | `billing/invoice.py:12` | No ticket at all |
| search | `search/q.py:1`, `:7` | Both removed: done |

Tickets: `PLAT-2291` (platform), `CHK-311` "Fix auth stuff in checkout" (no description), `CHK-298` (in progress, its only site removed), `FUL-7` (no done condition), `PAY-10` (closed Fixed, but its site is still there), `PAY-12` (payments, but mapped to a checkout site, shared with `CHK-311`), `OPS-1` (unrelated, unmapped).

### Setup: the current state

Every view reads the call sites as of one moment. `call_site` is append-only, so "now" is each site's newest version written at or before then. The test puts the moment (run, time, repo, symbol) in a one-row `asof` table. A block holds many repos and symbols: `site_now` keeps only the `asof` repo and symbol (NULL means all), and `ticket_open` only the tickets on those sites, so no view mixes datasets. Done statuses are `[rules] done_statuses`, compared lower-case: GitLab writes `closed`.

```sql
-- name: setup
CREATE TEMP VIEW site_now AS
SELECT c.* FROM call_site c, asof
WHERE c.repo = coalesce(asof.repo, c.repo) AND c.symbol = coalesce(asof.symbol, c.symbol)
  AND c.known_at = (SELECT max(x.known_at) FROM call_site x WHERE x.call_site_id = c.call_site_id AND x.known_at <= asof.at);
CREATE TEMP VIEW ticket_open AS
SELECT * FROM ticket WHERE lower(status) NOT IN ('done', 'closed', 'resolved')
  AND key IN (SELECT m.key FROM ticket_map m JOIN site_now s ON s.call_site_id = m.target WHERE m.target_kind = 'call_site');
-- the dotted module of every call-site file, as imports name it: pkg/__init__.py is pkg
CREATE TEMP VIEW file_module AS
SELECT DISTINCT repo, path, replace(CASE WHEN path LIKE '%/__init__.py' THEN substr(path, 1, length(path) - 12)
                                         ELSE substr(path, 1, length(path) - 3) END, '/', '.') AS module FROM site_now;
-- module m is imported by a row that names it, or by `from parent import leaf`
CREATE TEMP VIEW imports_of AS
SELECT f.path AS imported, f.module, i.path AS importer, i.is_test
FROM file_module f JOIN module_import i ON i.repo = f.repo AND (i.module = f.module OR i.module || '.' || i.name = f.module);
```

### Staff engineer

```sql
-- name: staff_headline
-- expect: [[8, 5]]
SELECT (SELECT count(*) FROM site_now WHERE status = 'present'),
       (SELECT count(DISTINCT m.key) FROM ticket_map m JOIN ticket_open t ON t.key = m.key WHERE m.target_kind = 'call_site');
```

Hidden chains are `depends_on` chains with no live Jira "blocks" link between the two tickets.

```sql
-- name: staff_chains
-- expect: [["collides", 2, 0], ["depends_on", 1, 0], ["duplicates", 1, 0], ["untracked", 3, 0]]
SELECT type, count(*),
       sum(EXISTS (SELECT 1 FROM ticket_link l WHERE l.link_type = 'blocked_by' AND l.removed_at IS NULL
                   AND ((l.key = c.from_key AND l.other_key = c.to_key) OR (l.key = c.to_key AND l.other_key = c.from_key))))
FROM chain c GROUP BY type ORDER BY type;
```

```sql
-- name: staff_untracked
-- expect: [["S_b1", "billing"], ["S_c3", "checkout"], ["S_new", "payments"]]
SELECT call_site_id, owner_team FROM site_now
WHERE status = 'present' AND call_site_id NOT IN (SELECT target FROM ticket_map WHERE target_kind = 'call_site')
ORDER BY call_site_id;
```

```sql
-- name: staff_vague
-- expect: [["CHK-311", "no description"], ["FUL-7", "no done condition"]]
SELECT key, CASE WHEN desc_empty THEN 'no description' ELSE 'no done condition' END FROM ticket_open
WHERE key IN (SELECT key FROM ticket_map WHERE target_kind = 'call_site') AND description NOT LIKE '%done when%'
ORDER BY key;
```

```sql
-- name: staff_link_coverage
-- expect: [[6, 7, "tickets_mapped", 6, 7]]
SELECT (SELECT count(DISTINCT key) FROM ticket_map WHERE target_kind != 'unmapped'), (SELECT count(*) FROM ticket),
       metric, numerator, denominator FROM coverage WHERE metric = 'tickets_mapped';
```

### API owner

Status per team: `done` if nothing is left, `blocked` if a present site waits on a present blocker, `stalled` if nothing changed for 14 days, else `moving`.

```sql
-- name: owner_teams
-- expect: [["billing", 1, 0, 264, "stalled"], ["checkout", 3, 2, 19, "stalled"], ["fulfillment", 1, 0, 264, "blocked"], ["payments", 2, 0, 3, "moving"], ["platform", 1, 0, 264, "stalled"], ["search", 0, 0, 21, "done"]]
SELECT owner_team, left_, risky, days,
       CASE WHEN left_ = 0 THEN 'done' WHEN blocked THEN 'blocked' WHEN days >= 14 THEN 'stalled' ELSE 'moving' END
FROM (SELECT s.owner_team,
             sum(s.status = 'present') AS left_,
             sum(s.status = 'present' AND s.bug_fixes >= 3) AS risky,
             CAST(julianday(asof.at) - julianday(max(s.effective_date)) AS INTEGER) AS days,
             max(s.status = 'present' AND s.call_site_id IN (
                 SELECT b.call_site_id FROM blocked_by b JOIN site_now k ON k.call_site_id = b.blocker_id
                 WHERE k.status = 'present')) AS blocked
      FROM site_now s, asof GROUP BY s.owner_team)
ORDER BY owner_team;
```

```sql
-- name: owner_done
-- expect: [[5, 13]]
SELECT sum(status = 'removed'), count(*) FROM site_now;
```

```sql
-- name: owner_eta
-- expect: [[8, 4, "2026-11-26"]]
SELECT left_, removed, CASE WHEN removed < 3 THEN NULL
                            ELSE date(at, '+' || CAST(round(left_ * 28.0 / removed) AS INTEGER) || ' days') END
FROM (SELECT asof.at,
             (SELECT count(*) FROM site_now WHERE status = 'present') AS left_,
             (SELECT count(*) FROM site_now WHERE status = 'removed'
                AND julianday(effective_date) > julianday(asof.at) - 28) AS removed
      FROM asof);
```

### API consumer

```sql
-- name: consumer_files
-- params: {"team": "checkout"}
-- expect: [["cart/pricing.py", 2, 4], ["cart/totals.py", 1, 0]]
SELECT s.path, count(*), coalesce(f.bug_fixes, 0)
FROM site_now s LEFT JOIN file_bug_fixes f ON f.repo = s.repo AND f.path = s.path
WHERE s.owner_team = :team AND s.status = 'present' GROUP BY s.path ORDER BY s.path;
```

```sql
-- name: consumer_blocked
-- params: {"team": "fulfillment"}
-- expect: [["S_f1", "S_shared", "platform", "shared/client.py"]]
SELECT a.call_site_id, k.call_site_id, k.owner_team, k.path
FROM blocked_by b JOIN site_now a ON a.call_site_id = b.call_site_id JOIN site_now k ON k.call_site_id = b.blocker_id
WHERE a.owner_team = :team AND a.status = 'present' AND k.status = 'present';
```

```sql
-- name: consumer_blocking
-- params: {"team": "platform"}
-- expect: [["fulfillment", 1]]
SELECT a.owner_team, count(*)
FROM blocked_by b JOIN site_now a ON a.call_site_id = b.call_site_id JOIN site_now k ON k.call_site_id = b.blocker_id
WHERE k.owner_team = :team AND a.status = 'present' AND k.status = 'present' GROUP BY a.owner_team;
```

### Ticket tools

The manager's rewrite of `CHK-311` uses only these facts, the `in_except` flag included ("two calls sit inside `except AuthError`. Keep the same handling?").

```sql
-- name: manager_facts
-- params: {"key": "CHK-311"}
-- expect: [["cart/pricing.py", 214, "checkout", 4, 0], ["cart/pricing.py", 231, "checkout", 4, 1]]
SELECT s.path, s.line, s.owner_team, s.bug_fixes, s.in_except
FROM ticket_map m JOIN site_now s ON s.call_site_id = m.target
WHERE m.key = :key AND m.target_kind = 'call_site' AND s.status = 'present' ORDER BY s.path, s.line;
```

```sql
-- name: maker_drafts
-- expect: [["billing", 1, "billing/invoice.py:12"], ["checkout", 1, "cart/totals.py:57"], ["payments", 1, "pay/charge.py:30"]]
SELECT owner_team, count(*), group_concat(path || ':' || line, ', ')
FROM (SELECT * FROM site_now WHERE status = 'present'
        AND call_site_id NOT IN (SELECT target FROM ticket_map WHERE target_kind = 'call_site') ORDER BY path, line)
GROUP BY owner_team ORDER BY owner_team;
```

```sql
-- name: check_wrong_owner
-- expect: [["PAY-12", "checkout"]]
SELECT DISTINCT t.key, s.owner_team
FROM ticket_open t JOIN ticket_map m ON m.key = t.key AND m.target_kind = 'call_site' JOIN site_now s ON s.call_site_id = m.target
WHERE s.status = 'present' AND s.owner_team NOT IN (SELECT value FROM json_each(t.components)) ORDER BY t.key;
```

### Push check

`:base` is the last nightly map's time. In the fixture, R1 plays the base and R2 the push.

```sql
-- name: p1_drift
-- params: {"base": "2026-09-01T00:00:00Z"}
-- expect: [["S_new", "pay/charge.py", 30]]
SELECT call_site_id, path, line FROM site_now WHERE status = 'present' AND call_site_id NOT IN (
  SELECT c.call_site_id FROM call_site c WHERE c.status = 'present'
    AND c.known_at = (SELECT max(x.known_at) FROM call_site x WHERE x.call_site_id = c.call_site_id AND x.known_at <= :base));
```

```sql
-- name: p2_legacy_use
-- params: {"base": "2026-09-01T00:00:00Z"}
-- expect: [["S_new", "AuthClient.verify_token", "DeprecationWarning"]]
SELECT s.call_site_id, d.scope, d.marker
FROM site_now s JOIN resolution r ON r.repo = s.repo AND r.path = s.path AND r.line = s.line AND r.col = s.col
JOIN deprecation d ON r.resolved_to = d.qualname        -- scan writes the name a call resolves to, so this is a plain join
WHERE s.status = 'present' AND s.known_at > :base;
```

```sql
-- name: p3_collision
-- params: {"path": "cart/pricing.py", "scope": "price", "team": "payments"}
-- expect: [["CHK-298", "In Progress"], ["CHK-311", "Open"]]
SELECT DISTINCT t.key, t.status
FROM ticket_map m JOIN ticket_open t ON t.key = m.key JOIN site_now s ON s.call_site_id = m.target
WHERE m.target_kind = 'call_site' AND s.path = :path AND s.scope = :scope
  AND :team NOT IN (SELECT value FROM json_each(t.components)) ORDER BY t.key;
```

```sql
-- name: p5_ticket_can_move
-- expect: [["CHK-298"]]
SELECT t.key FROM ticket_open t JOIN ticket_map m ON m.key = t.key AND m.target_kind = 'call_site'
JOIN site_now s ON s.call_site_id = m.target GROUP BY t.key HAVING sum(s.status = 'present') = 0;
```

```sql
-- name: p7_risky_edit
-- params: {"path": "cart/pricing.py"}
-- expect: [["cart/pricing.py", 4, "[\"BUG-0\", \"BUG-1\", \"BUG-2\", \"BUG-3\"]"]]
SELECT f.path, f.bug_fixes, f.fix_keys FROM file_bug_fixes f, asof
WHERE f.repo = coalesce(asof.repo, f.repo) AND f.path = :path AND f.bug_fixes >= 3;
```

```sql
-- name: p9_defect_risk
-- params: {"sha": "d1"}
-- expect: [[40, 2, 80]]
SELECT sum(added + deleted), count(*), sum(added + deleted) * count(*) FROM commit_file WHERE sha = :sha;
```

The push check runs on the changed files and every file that imports them.

```sql
-- name: push_scope_importers
-- expect: [["cart/pricing.py", "checkout/api.py"], ["shared/client.py", "ship/label.py"]]
SELECT DISTINCT imported, importer FROM imports_of WHERE is_test = 0 ORDER BY 1, 2;
```

### Stall reasons

```sql
-- name: stall_upstream
-- expect: [["fulfillment", "S_f1", "S_shared", "platform"]]
SELECT a.owner_team, a.call_site_id, k.call_site_id, k.owner_team
FROM blocked_by b JOIN site_now a ON a.call_site_id = b.call_site_id JOIN site_now k ON k.call_site_id = b.blocker_id
WHERE a.status = 'present' AND k.status = 'present';
```

```sql
-- name: stall_never_notified
-- expect: [["billing", 1]]
SELECT owner_team, count(*) FROM site_now WHERE status = 'present' GROUP BY owner_team
HAVING sum(call_site_id IN (SELECT target FROM ticket_map WHERE target_kind = 'call_site')) = 0;
```

```sql
-- name: stall_ticket_mismatch
-- expect: [["payments", "PAY-10", "S_p1"]]
SELECT s.owner_team, t.key, s.call_site_id FROM ticket t
JOIN ticket_map m ON m.key = t.key AND m.target_kind = 'call_site' JOIN site_now s ON s.call_site_id = m.target
WHERE lower(t.status) IN ('done', 'closed', 'resolved') AND s.status = 'present';
```

```sql
-- name: stall_orphaned_owner
-- expect: [["checkout", "cart/pricing.py", "p_ann", 91]]
SELECT f.owner_team, f.path, f.top_person_id, CAST(julianday(asof.at) - julianday(max(g.authored_at)) AS INTEGER)
FROM file_owner f JOIN person_alias p ON p.person_id = f.top_person_id JOIN git_commit g ON g.author_email = p.email, asof
WHERE (f.repo, f.path) IN (SELECT repo, path FROM site_now WHERE status = 'present')
GROUP BY f.repo, f.path HAVING julianday(asof.at) - julianday(max(g.authored_at)) >= 60;
```

```sql
-- name: stall_semantics_unclear
-- expect: [["checkout", "S_c2", "cart/pricing.py", 231]]
SELECT owner_team, call_site_id, path, line FROM site_now WHERE status = 'present' AND in_except = 1;
```

```sql
-- name: stall_fragility_freeze
-- expect: []
SELECT owner_team FROM site_now WHERE status = 'present' GROUP BY owner_team HAVING min(bug_fixes) >= 3;
```

```sql
-- name: stall_test_gap
-- expect: [["billing", "billing.invoice"], ["checkout", "cart.pricing"], ["payments", "pay.charge"], ["platform", "shared.client"]]
SELECT DISTINCT s.owner_team, f.module FROM site_now s JOIN file_module f ON f.path = s.path
WHERE s.status = 'present' AND s.path NOT IN (SELECT imported FROM imports_of WHERE is_test = 1)
ORDER BY s.owner_team;
```

Usual load = the team's commits in the 56 days before the last 14, per 14 days. Only teams that still own a call site: a busy team with none isn't stalled.

```sql
-- name: stall_competing_load
-- expect: [["checkout", 4, 2.0]]
WITH c AS (SELECT DISTINCT g.sha, g.authored_at, t.team FROM git_commit g
           JOIN person_alias p ON p.email = g.author_email JOIN team_map t ON t.subject_kind = 'person' AND t.subject = p.person_id),
     w AS (SELECT c.team,
                  sum(julianday(c.authored_at) > julianday(asof.at) - 14) AS recent,
                  sum(julianday(c.authored_at) BETWEEN julianday(asof.at) - 70 AND julianday(asof.at) - 14) / 4.0 AS usual,
                  sum(julianday(c.authored_at) > julianday(asof.at) - 14 AND EXISTS (
                      SELECT 1 FROM commit_file f JOIN site_now s ON s.repo = f.repo AND s.path = f.path AND s.status = 'present'
                      WHERE f.sha = c.sha)) AS on_sites
           FROM c, asof GROUP BY c.team)
SELECT team, recent, usual FROM w WHERE on_sites = 0 AND usual > 0 AND recent >= 0.75 * usual
  AND team IN (SELECT owner_team FROM site_now WHERE status = 'present');
```

### Graph, from its inputs

`blocked_by` from the new `receiver_from`: A's receiver came from F, and B sits inside F, owned by someone else. (Depth > 1 follows `call_edge` from F.)

```sql
-- name: derive_blocked_by
-- expect: [["S_f1", "S_shared", 1]]
SELECT a.call_site_id, b.call_site_id, 1
FROM site_now a JOIN resolution r ON r.repo = a.repo AND r.path = a.path AND r.line = a.line AND r.col = a.col
JOIN site_now b ON r.receiver_from = b.repo || ':' || b.path || ':' || b.scope
WHERE a.status = 'present' AND b.status = 'present' AND a.owner_team != b.owner_team;
```

```sql
-- name: derive_blast_radius
-- expect: [[1, 8], [2, 3], [3, 1]]
WITH RECURSIVE d AS (                            -- depth 1: the edges at a resolved resolution row of the symbol
  SELECT e.* FROM call_edge e JOIN resolution r ON r.repo = e.repo AND r.path = e.caller_path AND r.line = e.line AND r.col = e.col, asof
  WHERE r.detection = 'ast_resolved' AND r.symbol = coalesce(asof.symbol, r.symbol) AND e.repo = coalesce(asof.repo, e.repo)),
fn(repo, path, scope, depth) AS (                -- functions that reach the symbol, and how far up
  SELECT DISTINCT repo, caller_path, caller_scope, 1 FROM d
  UNION
  SELECT e.repo, e.caller_path, e.caller_scope, f.depth + 1 FROM call_edge e JOIN fn f
    ON e.callee_repo = f.repo AND e.callee_path = f.path AND e.callee_scope = f.scope WHERE f.depth < 2)
SELECT 1, count(*) FROM d
UNION ALL                                        -- invocations at depth n = calls into the functions at depth n - 1,
SELECT depth, count(*) FROM (                    -- each edge once, at its shortest depth (a function can sit at two)
  SELECT min(f.depth) + 1 AS depth FROM call_edge e JOIN fn f
    ON e.callee_repo = f.repo AND e.callee_path = f.path AND e.callee_scope = f.scope
  WHERE NOT EXISTS (SELECT 1 FROM d WHERE d.repo = e.repo AND d.caller_path = e.caller_path AND d.line = e.line AND d.col = e.col
                      AND d.callee_repo = e.callee_repo AND d.callee_path = e.callee_path AND d.callee_scope = e.callee_scope)
  GROUP BY e.repo, e.caller_path, e.line, e.col, e.callee_repo, e.callee_path, e.callee_scope)
GROUP BY depth;
```

```sql
-- name: derive_duplicates
-- expect: [["CHK-311", "PAY-12", "S_c1"]]
SELECT a.key, b.key, a.target FROM ticket_map a JOIN ticket_map b ON a.target = b.target AND a.key < b.key
WHERE a.target_kind = 'call_site' AND b.target_kind = 'call_site' AND a.target IN (SELECT call_site_id FROM site_now);
```

Collides: two open tickets mapped to the same function that don't already share a site.

```sql
-- name: derive_collides
-- expect: [["CHK-298", "CHK-311", "cart/pricing.py:price"], ["CHK-298", "PAY-12", "cart/pricing.py:price"]]
SELECT DISTINCT a.key, b.key, sa.path || ':' || sa.scope
FROM ticket_map a JOIN site_now sa ON sa.call_site_id = a.target
JOIN ticket_map b ON b.key > a.key JOIN site_now sb ON sb.call_site_id = b.target AND sb.path = sa.path AND sb.scope = sa.scope
WHERE a.target_kind = 'call_site' AND b.target_kind = 'call_site'
  AND a.key IN (SELECT key FROM ticket_open) AND b.key IN (SELECT key FROM ticket_open)
  AND NOT EXISTS (SELECT 1 FROM ticket_map x JOIN ticket_map y ON x.target = y.target WHERE x.key = a.key AND y.key = b.key)
ORDER BY a.key, b.key;
```

```sql
-- name: derive_depends_on
-- expect: [["FUL-7", "PLAT-2291"]]
SELECT DISTINCT ma.key, mb.key FROM blocked_by b JOIN site_now s ON s.call_site_id = b.call_site_id
JOIN ticket_map ma ON ma.target = b.call_site_id JOIN ticket_map mb ON mb.target = b.blocker_id;
```

```sql
-- name: order_of_work
-- expect: [["S_c1"], ["S_c2"], ["S_shared"], ["S_b1"], ["S_c3"], ["S_new"], ["S_p1"], ["S_f1"]]
WITH RECURSIVE lvl(id, n) AS (
  SELECT call_site_id, 0 FROM site_now WHERE status = 'present'
  UNION ALL
  SELECT b.call_site_id, l.n + 1 FROM blocked_by b JOIN lvl l ON l.id = b.blocker_id)
SELECT s.call_site_id FROM site_now s JOIN (SELECT id, max(n) AS n FROM lvl GROUP BY id) v ON v.id = s.call_site_id
ORDER BY v.n, s.bug_fixes DESC, s.call_site_id;
```

### Legacy radar and replay

```sql
-- name: radar_deprecated_with_callers
-- expect: [["auth/client.py", "AuthClient.verify_token", "DeprecationWarning", 8]]
SELECT d.path, d.scope, d.marker, count(e.line) FROM deprecation d
JOIN call_edge e ON e.callee_repo = d.repo AND e.callee_path = d.path AND e.callee_scope = d.scope, asof
WHERE d.repo = coalesce(asof.repo, d.repo) GROUP BY d.repo, d.path, d.scope;
```

Replay may only see what was known then. As of R1, only R1's rows exist:

```sql
-- name: replay_as_of
-- params: {"t": "2026-09-01T00:00:00Z"}
-- expect: [[11, 1]]
SELECT sum(c.status = 'present'), sum(c.status = 'removed') FROM call_site c, asof
WHERE c.repo = coalesce(asof.repo, c.repo) AND c.symbol = coalesce(asof.symbol, c.symbol)
  AND c.known_at = (SELECT max(x.known_at) FROM call_site x WHERE x.call_site_id = c.call_site_id AND x.known_at <= :t);
```

## Tracking whether our suggestions work

The docs already say what success is, and it's the one thing nobody can reconstruct later:

- "Diffs and drafts used, edited, or ignored. Burndown before vs after us. Ignored and flat by week 8 = we built a dashboard" ([02-product](../docs/02-product.md), scoping §10–12).
- "Outcome labels … never existed as data until we wrote it down" (an older draft): git and Jira can be re-parsed, outcomes can't. So recording starts the day the first suggestion goes out, not later.
- "We publish suggestion precision, not coverage: drafted 31 of 52, 29 applied unchanged" (an older draft).
- The gate for everything after step 1: we only make changes ourselves "once suggestions get applied unchanged" ([01-problem](../docs/01-problem.md), [06-dead-ideas](../docs/06-dead-ideas.md)).

**No separate graph.** Outcomes are facts about the same call sites, tickets and teams, so they go in the same KB as one more layer, keyed by the existing IDs. A second store would need its own join back to `call_site`, and the before/after numbers come from `call_site` history anyway.

**Code decides, not clicks.** An outcome is read off later runs, by rule, like every other fact. A diff is "applied" when the call site disappears and the line in its place is the line we drafted. No button, no survey. It replays too: rerun an old snapshot and you get the same verdicts.

| Kind | Applied | Edited | Ignored |
|---|---|---|---|
| Diff (codemod or LLM) | The site is removed, and the line in its place at the removing commit equals the drafted after-line (whitespace ignored) | The site is removed, the line differs | Still present `[outcomes] ignore_after_days` after the offer |
| Ticket draft (Maker) | A ticket created within `draft_window_days` maps to every site in the draft | It maps to some of them | No such ticket |
| Rewrite (Manager) | The ticket's description changes within the window and now passes the done-condition check | It changes and still fails | No change. Needs `description` changes in `ticket_event`: one more field in tickets.py |
| Stall reason | 04 §9's verdict rules (block removed ≤ 7 days before resumption, …), or a person's call, logged with who | | No verdict in the window: no match |
| Push alert | `fixed`: the merged SHA doesn't have the flagged row | `dismissed`: the comment was resolved with no change | `ignored`: merged with it |

**Proposed tables** (03-data named `suggestion` and `push_alert`; this fixes their columns and adds `outcome`). They move into schema.sql when stage 6 starts writing suggestions. The proof below runs on exactly this DDL.

```sql
-- name: schema_outcomes
CREATE TABLE suggestion (                    -- everything we offered, once, as it was shown
  run_id          TEXT NOT NULL,             -- the run that offered it
  suggestion_id   TEXT NOT NULL,             -- h(kind, target, facts_hash)
  kind            TEXT NOT NULL CHECK (kind IN ('diff','ticket_draft','rewrite','stall_reason','legacy')),
  target_kind     TEXT NOT NULL CHECK (target_kind IN ('call_site','ticket','team','file')),
  target          TEXT NOT NULL,
  owner_team      TEXT NOT NULL,
  source          TEXT NOT NULL CHECK (source IN ('codemod','llm','template','rules')),
  body            TEXT NOT NULL,             -- the diff, draft or rewrite, exactly as shown
  facts_hash      TEXT NOT NULL,             -- the gate's facts: all it was allowed to say
  offered_at      TEXT NOT NULL,             -- = run.snapshot_at of the offering run
  PRIMARY KEY (run_id, suggestion_id)
) WITHOUT ROWID;
CREATE TABLE outcome (                       -- append-only: a row only when the verdict changes; the newest wins
  suggestion_id   TEXT NOT NULL,
  decided_run_id  TEXT NOT NULL,
  outcome         TEXT NOT NULL CHECK (outcome IN ('applied','edited','ignored','rejected','superseded')),
  decided_at      TEXT NOT NULL,             -- when the code or the ticket showed it, not when we noticed
  latency_days    INTEGER,                   -- offer -> applied or edited; NULL otherwise
  rule            TEXT NOT NULL,             -- which [outcomes] rule decided it, or a person's initials
  evidence        TEXT NOT NULL,             -- JSON: the sha and line, or the ticket key, that proves it
  PRIMARY KEY (suggestion_id, decided_run_id)
) WITHOUT ROWID;
CREATE TABLE push_alert (
  push_sha        TEXT NOT NULL,
  base_run_id     TEXT NOT NULL,
  check_id        TEXT NOT NULL CHECK (check_id IN ('P1','P2','P3','P4','P5','P6','P7','P8','P9')),
  target          TEXT NOT NULL,
  evidence        TEXT NOT NULL,
  outcome         TEXT CHECK (outcome IN ('fixed','dismissed','ignored')),   -- NULL until merged or closed
  decided_at      TEXT,
  PRIMARY KEY (push_sha, check_id, target)
) WITHOUT ROWID;
```

In the fixture, R1 offered six suggestions. Checkout and payments got diffs, billing a ticket draft, `CHK-311` a rewrite. Search, platform and fulfillment got nothing: they are the control group.

**Precision per kind**, the number we publish. N first, always.

```sql
-- name: outcome_precision
-- expect: [["diff", 4, 2, 1, 1, 0.5, 18.0], ["rewrite", 1, 0, 0, 1, 0.0, null], ["ticket_draft", 1, 0, 0, 1, 0.0, null]]
WITH last AS (SELECT o.* FROM outcome o
              WHERE o.decided_at = (SELECT max(x.decided_at) FROM outcome x WHERE x.suggestion_id = o.suggestion_id))
SELECT s.kind, count(*), sum(l.outcome = 'applied'), sum(l.outcome = 'edited'), sum(l.outcome = 'ignored'),
       round(1.0 * sum(l.outcome = 'applied') / count(*), 2), round(avg(l.latency_days), 1)
FROM suggestion s JOIN last l ON l.suggestion_id = s.suggestion_id GROUP BY s.kind ORDER BY s.kind;
```

**The auto-apply gate.** A kind earns "we make the change ourselves" only at N ≥ `auto_apply_min_n` and an applied-unchanged share ≥ `auto_apply_min_share`. Until then, it says why not.

```sql
-- name: auto_apply_gate
-- params: {"min_n": 30, "min_share": 0.9}
-- expect: [["diff", 4, 0.5, "not yet: N < 30"], ["rewrite", 1, 0.0, "not yet: N < 30"], ["ticket_draft", 1, 0.0, "not yet: N < 30"]]
SELECT kind, n, share, CASE WHEN n < :min_n THEN 'not yet: N < ' || :min_n
                            WHEN share < :min_share THEN 'no: applied unchanged below ' || :min_share ELSE 'earned' END
FROM (SELECT s.kind, count(*) AS n, round(1.0 * sum(o.outcome = 'applied') / count(*), 2) AS share
      FROM suggestion s JOIN outcome o ON o.suggestion_id = s.suggestion_id GROUP BY s.kind)
ORDER BY kind;
```

**Burndown before vs after us, per team.** Removals in the 30 days before a team's first suggestion vs the 30 days after. A team that got nothing is measured around the first suggestion anyone got: that's the control.

```sql
-- name: outcome_before_after
-- params: {"days": 30}
-- expect: [["billing", "suggested", 0, 0], ["checkout", "suggested", 0, 1], ["fulfillment", "control", 0, 0], ["payments", "suggested", 0, 2], ["platform", "control", 0, 0], ["search", "control", 0, 1]]
WITH f AS (SELECT t.team, coalesce(min(s.offered_at), (SELECT min(offered_at) FROM suggestion)) AS at,
                  count(s.suggestion_id) > 0 AS suggested
           FROM (SELECT DISTINCT owner_team AS team FROM site_now) t
           LEFT JOIN suggestion s ON s.owner_team = t.team GROUP BY t.team)
SELECT f.team, CASE WHEN f.suggested THEN 'suggested' ELSE 'control' END,
       (SELECT count(*) FROM site_now c WHERE c.owner_team = f.team AND c.status = 'removed'
          AND julianday(c.effective_date) > julianday(f.at) - :days AND julianday(c.effective_date) <= julianday(f.at)),
       (SELECT count(*) FROM site_now c WHERE c.owner_team = f.team AND c.status = 'removed'
          AND julianday(c.effective_date) > julianday(f.at) AND julianday(c.effective_date) <= julianday(f.at) + :days)
FROM f ORDER BY f.team;
```

**The effect** is the suggested teams' change minus the control teams' change (a difference-in-differences). Here: +1.0 removals per team-month against +0.33 without us. It's an effect only once both groups have real N. Teams that get suggestions first may be the ones already moving, so roll suggestions out in a staggered order. Until there's a customer, it's replay, and we say so.

```sql
-- name: outcome_effect
-- params: {"days": 30}
-- expect: [["control", 3, 0.0, 0.33], ["suggested", 3, 0.0, 1.0]]
WITH f AS (SELECT t.team, coalesce(min(s.offered_at), (SELECT min(offered_at) FROM suggestion)) AS at,
                  count(s.suggestion_id) > 0 AS suggested
           FROM (SELECT DISTINCT owner_team AS team FROM site_now) t
           LEFT JOIN suggestion s ON s.owner_team = t.team GROUP BY t.team),
     r AS (SELECT CASE WHEN f.suggested THEN 'suggested' ELSE 'control' END AS grp,
                  (SELECT count(*) FROM site_now c WHERE c.owner_team = f.team AND c.status = 'removed'
                     AND julianday(c.effective_date) > julianday(f.at) - :days AND julianday(c.effective_date) <= julianday(f.at)) AS before,
                  (SELECT count(*) FROM site_now c WHERE c.owner_team = f.team AND c.status = 'removed'
                     AND julianday(c.effective_date) > julianday(f.at) AND julianday(c.effective_date) <= julianday(f.at) + :days) AS after
           FROM f)
SELECT grp, count(*), round(avg(before), 2), round(avg(after), 2) FROM r GROUP BY grp ORDER BY grp;
```

**Push alerts**: did the note change the merge? 05-test's bar (precision ≥ 0.80, median ≤ 1 alert per push) says whether an alert was right. This says whether anyone acted on it.

```sql
-- name: push_alert_outcomes
-- expect: [["P1", 2, 1, 0, 1], ["P7", 1, 0, 0, 1]]
SELECT check_id, count(*), sum(outcome = 'fixed'), sum(outcome = 'dismissed'), sum(outcome = 'ignored')
FROM push_alert GROUP BY check_id ORDER BY check_id;
```

**Week 8's question**, "used, or just a dashboard?", is then two of these numbers: anything applied or edited at all, and suggested teams moving faster than control. Here, 3 of 6 suggestions were used and the suggested teams moved faster. On N = 6 that's a demo, not a result.

```sql
-- name: outcome_week8
-- expect: [["used", 3, 6]]
SELECT CASE WHEN sum(outcome IN ('applied', 'edited')) = 0 THEN 'dashboard' ELSE 'used' END,
       sum(outcome IN ('applied', 'edited')), count(*) FROM outcome;
```

**Charts.** Not new graphs: four views over these queries.

| Chart | Shows | From |
|---|---|---|
| Precision per kind, by week | Applied / edited / ignored as stacked bars, N on top. The one we publish | `outcome_precision` per week |
| Burndown with suggestion marks | Present call sites over time, per team, with a tick at each suggestion and its outcome | `call_site` versions + `suggestion` |
| Before vs after, with control | One dot pair per team, suggested and control side by side | `outcome_before_after` |
| Push alert outcomes per check | fixed / dismissed / ignored per P1–P9 | `push_alert_outcomes` |

**Until there's a customer (replay).** Offer the suggestions on an old snapshot and score them against what the project really did later. Django is built for it: draft diffs at `4353640ea9`, the parent of the conversion commit, and compare them with `c651331b34`'s after-lines. That's 05-test's "Diffs: draft from parent vs real after-line ≥ 90% match", and it fills `outcome` with `rule = 'replay'`, so replay and live numbers never mix.

## Gaps

What the KB still can't answer, and what closes each.

| # | Gap | Blocks | Closes it |
|---|---|---|---|
| G1 | `suggestion`, `outcome`, `push_alert` are proposed above (DDL tested) but not in schema.sql; `policy`, `match` not drafted | Diffs, outcomes, Elo, push history | Move the three into schema.sql when stage 6 writes its first suggestion: outcomes can't be backfilled, so not one day later. `policy`, `match` with stage 9 |
| G2 | Ticket comments aren't kept, only the description | Stack-trace mapping when the trace is in a comment; GitLab stall reasons written in notes | A `ticket_comment(key, at, body)` table from the raw pulls (both already have them). SQLite only: bodies can name people |
| G3 | `call_edge` only covers callers above tracked symbols | Explore's fan-in signals (fan-in ≥ 10, high `bug_fixes` and fan-in) | A whole-repo call graph is a second resolver pass. Measure its cost on Django first |
| G4 | No function signatures | "Same-signature twin gaining callers" | A `function(repo, path, scope, params)` table from scan's tree-sitter pass: cheap |
| G5 | No intake | Intake tool | Its own collect source, later |
| G6 | The GitLab gold set lives in `labels/*.csv`, not the KB | Nothing: graph scoring reads the CSV directly | Fine as is. Labels are inputs to tests, not facts about the code |
| G7 | No dataset fills every table: Django has no Jira, GitLab has no Python, packages don't call each other | One real end-to-end story | Known since week 1 (02-product). The fixture above is the end-to-end story; each table is scored on the dataset that has it |
| G8 | "Done" differs per tracker (`Done`, `Closed`, `Resolved`; GitLab writes `closed`) | Every open/closed rule | Done: `[rules] done_statuses`, with every other rule threshold (14 days, 28 days, 3 removals, 0.75, …), is in meridian.toml for the rules stage to read. It is compared in any case (here and in graph): matched exactly, all 14,524 GitLab issues on the team block counted as open, not 3,680 |

## Testing plan

### Principles (from 05-test, unchanged)

- Pass bars are fixed before the run. Changing one needs a commit saying why.
- Every number has its N and a script. Split by time, never at random.
- Same snapshot + config = same bytes, on every machine. Any difference is a bug, not noise.

### Levels

| Level | What | When | Command | Today |
|---|---|---|---|---|
| 1. Unit fixtures | One synthetic repo or table per rule: every role, scope, junk label, bulk close, rename, share tie, link direction | Every commit | `python -m unittest discover -s tests -t .` | ✅ 22 test files, 293 tests (14 skipped by design) |
| 2. Goldens | A frozen fixture → exact rows: scan's 15 cases, resolve's edge fixtures, the writer's same-bytes check. On the team run, md5 of the 24 output files against `golden/` | Every commit | same | ✅ |
| 3. KB proof | This page's queries against the fixture KB | Every commit | `python -m unittest tests.test_kb` | ✅ 44 queries |
| 4. Cross-machine | Same run on Windows and Mac (and the Airflow image): every `out/<run_id>/*.jsonl` sha256 equal | Every new run_id | `sha256sum out/<run_id>/*.jsonl` on each, compared | ✅ Windows and Docker on a Mac. One difference found, Jedi's folder order, now fixed ([RESOLVER.md](RESOLVER.md)) |
| 5. Benchmarks | Real data against answer keys | Each new run_id, and weekly once scheduled | per part, below | partly ✅ |
| 6. End to end | Collect → graph on the new run_id, then the writer; second run → `cmp` identical | D8–D10 | Airflow DAG | ✅ both DAGs on 70dd1dabfed544b8, published ([report](report/REPORT.md)) |
| 7. Outcomes | Suggestions used, edited, ignored | Continuous, later | — | ⛔ stage 6 |

### Benchmarks per part (the bars)

| Part | Benchmark | Bar | Status |
|---|---|---|---|
| collect | Same inputs twice → same run_id; kill and resume | 100% | ✅ tests |
| history | 45,312 Spark first-parent commits; 94.0% of the last 1,000 keyed; 3 dead keys | exact | ✅ on the old run; rerun on the new one |
| tickets | 59,491 Spark tickets; bulk-closed share; rollback spot check of 20 tickets against the Jira UI | exact, 20 of 20 | ❌ spot check not done |
| SZZ | Defects4J 130 labels: beats churn and latest | beats both | ✅ 44% vs 18% vs 16% |
| scan | Django 265 of 265, 0 false; `code.sample` 2 of 2 | exact | ✅ |
| resolve | 542 of 542 aliases; packages precision ≥ 0.98, full recall | as stated | ✅ on 70dd1dabfed544b8: 1,236 of 1,236 (1,059 of 1,059 on the old run) |
| owners | Spark owner vs next author beats last toucher | > 18% | ✅ 20% vs 18% |
| owners | Top 20 identity groups + every local-part merge with different names hand-checked | ≥ 19 of 20 right | ❌ |
| gold set | Human spot check of 10 waits | ≥ 9 of 10 | ❌ the check done is a random 50 of 262 rows, not 10 picked waits. In the 50, the model's `wait` rows hold 4 of 7; its 1 `planned` and 3 `partial` all hold (`unclear` 2 of 20, `no` 9 of 14, `no_evidence` 0 of 5 match). The model agrees with the person on 18 of 50 |
| join | Call-site ID fixtures (insert, move, rename, duplicate, split, a repo added to or dropped from the config); Django weekly replay | 100%, replay error ≤ 1% | ✅ fixtures (`tests/test_join.py`, 22 tests); replay ❌ |
| join | Ticket → code on Spark, fix commits hidden | path, trace precision ≥ 0.90 | ❌ |
| graph | Fixture per rule; Spark collision pairs hand-checked (50); GitLab gold set | 100%, precision ≥ 0.90 | ✅ fixtures (`tests/test_graph.py`); hand checks ❌ |
| new KB columns | `in_except`, `module_import`, `deprecation`, `receiver_from` on Django: hand-check 20 rows each | 20 of 20 | ❌ |

### What's left

Done: the dynamic-config fixes, the new run_id (70dd1dabfed544b8), join and graph with their fixtures, the cross-machine check, and the end-to-end run in Airflow. Still open:

1. **Hand checks:** the rollback spot check, the identity groups, more gold-set rows (50 of 262 checked, the model's waits hold only 4 of 7), the 20-row checks of the new columns, and join's ticket → code sample.
2. **Reruns** of the benchmarks above that still say "old run".
3. **A second real run** with `--prev-run`, for the Django replay and join's matching on real data.

### CI

[.github/workflows/ci.yml](../.github/workflows/ci.yml) runs `python -m unittest discover -s tests -t .` from `Data-Pipeline/` on `ubuntu-latest` and `windows-latest`, with Python 3.11 and the pinned requirements, plus flake8, the Great Expectations tests and the DAG tests. Two operating systems on purpose: half of the dynamic-config bugs only showed on Windows. The writer's byte tests need the pinned SQLite. The image has it; where it's missing, the test skips and says why.
