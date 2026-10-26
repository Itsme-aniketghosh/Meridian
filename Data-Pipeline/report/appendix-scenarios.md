# Appendix: does the pipeline's output carry the signal the product needs?

Every view, ticket tool, push check and stall reason in [02-product](../../docs/02-product.md) is a named SQL query in
[KNOWLEDGE-BASE.md](../KNOWLEDGE-BASE.md), tested in CI on a hand-built fixture (`acme/shop`). Here the same queries
run unchanged on the real block of team run `70dd1dabfed544b8` (Django at `4353640ea9`, Spark + Apache Jira, 20 Django
packages, GitLab issues). The block is also scored against the answer keys in [labels/](../labels/), and real commits
are replayed through the push check.

Reproduce from `Data-Pipeline/` (read-only; `--replay` reruns the push replay cached in `figures/push_replay.json`).
Every number below is in `figures/scenarios.json`. Needs matplotlib, plus tree-sitter (`requirements.txt`) for the push replay.

```
python report/figures_scenarios.py --db <run 70dd…>/meridian.sqlite
```

## How the queries were run

On the fixture, `python -m unittest tests.test_kb tests.test_pushcheck -v` passes 18 of 18: all 43 `-- expect:`
queries in KNOWLEDGE-BASE.md (one subtest each, plus `setup` and `schema_outcomes`), two KB regression tests (path →
module rule, `asof` scoping) and the 15 push-check tests (P1 to P9 on one scripted PR, plus scope, quiet, limits, path
list, CLI, never writes the block).

The queries assume a block holds one dataset. Their `asof` row names a repo and a symbol, but no query filters on
them, so on the whole block they mix datasets (`stall_competing_load` joins Spark people to Django paths;
`derive_blast_radius` adds 20 repos' `url()` edges together). So each scenario shadows every table with a TEMP view of
its dataset's rows (`django`, `packages`, `spark`, `gitlab`), and the doc's SQL runs as is. (Fixed since: `site_now`
and the queries that bypassed it are now scoped to `asof.repo`/`asof.symbol`, NULL = all.)

`asof` is the run's time (2026-10-01), because `call_site.known_at` is when the run learned a fact. `replay_as_of` at
2017 returns `[null, null]`, so the leak guard works. But day counts are then measured from 2026, and every Django and
package team looks like it stalled years ago. A fifth scenario, `django@pin`, moves `known_at` to the pinned commit's
date (2017-02-03) so days, stalls and owners read as they did then.

Fixture parameters were swapped for real targets (team `contrib.admin`, a real path, a real SHA). Where the SQL
hard-codes the fixture's symbol (`derive_blast_radius`), it was rerun with each real symbol.

The block is one run at schema v1.1 (no `effective_sha` or `introduced_sha`; no query reads them), so no call site can
be `removed` yet. Burndown is checked against the next real commit instead (the conversion and migration commits),
replayed through `meridian.pushcheck`.

## Verdicts at a glance

| Area | Behaves as wanted | Partial | Quiet |
|---|---|---|---|
| Finding the work (call sites, aliases, blast radius) | ✅ exact on both answer keys | | |
| Burndown at the converting commit | ✅ 267 of 267 Django lines edited | packages 1,160 of 1,236 | owner_done / owner_eta: one run |
| Owners, stalls from code | `django@pin` statuses, orphaned owner, semantics unclear, test gap | competing load fires with no sites (fixed since: 0) | upstream, fragility freeze |
| Tickets (Spark) | link rate, ticket → code 61%, P9 | vague check fires on 100% | every ticket → call-site join |
| Cross-team blocks, chains | | GitLab links exist; label rule precision 0.14 | blocked_by = 0, chains other than untracked = 0 |
| Push check replay | P1 (4 true, Django), P9 | P1 look-alikes in packages | P2–P8 |
| Outcomes (6 queries) | | | no tables yet (G1) |

In short: the code side carries exact signal, and the ticket side carries real signal on Spark. But the two sides never
meet in one dataset (G7: Django has no Jira, Spark has no tracked symbol, GitLab has no Python). So every situation
that joins a ticket to a call site (P3, P5, P6, collisions, duplicates, depends_on, wrong owner, manager facts) is
quiet on real data because of how the data is shaped, not because it is wrong.

---

## 1. Finding the work: call sites, aliases, blast radius

Product question: "212 call sites remaining in code" (staff headline), and how far a removal reaches. Queries:
`staff_headline`, `derive_blast_radius`, plus the 265 and 30-row answer keys.

| | Real block | Known fact | Verdict |
|---|---|---|---|
| `staff_headline` (django) | [289, 0]: 289 present sites, 0 open mapped tickets | 289 sites on 267 lines = 265 `.py` + 2 in `code.sample` | ✅ |
| Lines vs `django_edit_sites_265.csv` | 265 of 265, 0 false, 0 missed (P = R = 1.00) | 265 | ✅ |
| Roles of those lines | 113 import, 151 call, 1 alias | 113 / 151 / 1 | ✅ exact |
| Aliased `_()` calls (resolution) | 542 calls, 485 lines, 96 files | 542 / 485 / 96 | ✅ exact |
| `alias_hand_check_30.csv` | 30 of 30 resolved to the checker's symbol | | ✅ |
| `staff_headline` (packages) | [1237, 0] url() sites in 20 repos | resolve: 1,236 of 1,236 recall on 20 packages (RESOLVER.md) | ✅ |
| `derive_blast_radius` as written | [[1, 0]]: the SQL names `AuthClient.verify_token` | | ⚠ fixture-bound |
| … with the real symbol (`ugettext`) | depth 1/2/3 = 209 / 241 / **160** | graph's `blast_radius` row: 209 / 241 / **135** | 🟡 depth 3 differs (fixed since: 209 / 241 / 135) |

Two caveats. The doc's blast-radius recursion counted a function once per depth it is reached at, while graph's table
does not, so depth 3 disagreed for `ugettext` (+25), `ugettext_lazy` (+15) and `ungettext` (+3). Fixed since: graph.py
was right, and the query now matches its table and no longer hard-codes the symbol. And the staff headline's ticket
half is 0 by design: Django uses Trac.

![](figures/blast_radius.png)

*Grey is the call sites someone must edit; the colored stack is the calls that reach them, up to 3 calls deep (the
blast radius). A removal can reach far more code than its call-site count, which is 03-data's case for the resolver.*

For `ugettext`, 93 call sites reach 585 invocations; `ugettext_noop` has 1 site and 29 invocations. Packages are flat
(urlconfs are rarely called), except `django-allauth`, where 86 calls reach `url()` through `default_urlpatterns`.

## 2. Burndown and "N of M done"

Product question: "31 of 52 done, est. complete Nov 14". Does burndown reach 0 at the conversion commit? Queries:
`owner_done`, `owner_eta`, `replay_as_of`, plus the conversion check.

| | Real block | Verdict |
|---|---|---|
| `owner_done` (django / packages) | [0, 289] / [0, 1237]: nothing removed yet | ⛔ quiet: one run, no second version |
| `owner_eta` | [289, 0, null]: needs ≥ 3 removals in 28 days | ⛔ quiet, same reason |
| `replay_as_of` (t = 2017) | [null, null]: the block didn't know 2017 facts until 2026 | ✅ the leak guard holds |
| Conversion commit `c651331b34` vs the block | edits 267 of 267 site lines; 285 `.py` lines changed = 265 + 20 non-uses | ✅ |
| 20 url() migration commits vs the block | 1,160 of 1,236 lines edited; 12 of 20 repos fully | 🟡 |

![](figures/django_burndown.png)

*The blue line counts the block's 267 Django call-site lines by the date each was last written (its blame date); the
orange drop is the commit that converts all of them. A second run at that commit would show 0 old sites left, plus 4
new ones added on purpose.*

The 4 new lines are `test_legacy_aliases` (tests/i18n/tests.py:60–64), which calls all four legacy names; the push
replay flags exactly these 4 as P1. So 05-test's "0 at conversion" holds for the 267, not for the file as it stands
after the commit.

![](figures/burndown_next_commit.png)

*For each repo, grey is the call-site lines in the block and blue is how many of them the next migration commit
edited. Where the bars match, the block found exactly the lines the real fix touched.*

Where a migration commit finished the job, the match is exact (oscar 247/247, wagtail 240/240, DRF 182/182, cms
160/160). The gaps are in 8 packages (worst: autocomplete-light 20/67, graphene 3/13, extensions 2/10, import-export
3/7). Most likely those migrations took more than one commit, so the leftover sites are real "partial migration"
signal. I did not hand-check the 76 leftover lines to rule out scanner false positives.

## 3. API owner: per-team status

Product question: "Payments 9 left, moving, 3 risky / Checkout STALLED 19 days." Query: `owner_teams`.

| Scenario | Result |
|---|---|
| `django@pin` | 23 path-prefix teams: 22 stalled, `tests` moving (10 d), 0 risky, 0 blocked. contrib.admin 43 left (121 d), contrib.humanize 31 (737 d), forms 23 (37 d) |
| `django` at run time | same 23, all stalled with 3,000+ days (2026 clock) |
| `packages` | 20 repos, all stalled (1,770–3,792 d), 0 risky |

Verdict: partial. The status logic does what the spec says, and "stalled" is historically right: in Feb 2017 nobody
was migrating off `ugettext` (it carried no deprecation warning until later). Two things are weak. Teams are
synthetic (path prefixes; 02-product says the screen must say so). And "risky" is always 0 because no call site has a
bug-fix count (§6).

![](figures/django_owner_teams.png)

*Call sites per Django team (a path prefix) at the pinned commit, with status and days since the last change. Only
the test tree changed in the 14 days before the pin, so every other team shows as stalled.*

The map is concentrated (tests 83, contrib.admin 43, humanize 31), with a long tail of 1–2-site teams.

## 4. API consumer view

| Query | Real result | Verdict |
|---|---|---|
| `consumer_files` (contrib.admin) | 13 files, e.g. `admin/models.py` 16 sites, `helpers.py` 4. Bug fixes 0 everywhere | 🟡 sites yes, fragility no |
| `consumer_files` (wagtail) | 44 files, `admin/urls/__init__.py` 33 | 🟡 same |
| `consumer_blocked`, `consumer_blocking` | 0 | ⛔ `blocked_by` is empty (§7) |

## 5. Ticket tools

| Query | Real result | Verdict |
|---|---|---|
| `maker_drafts` | 23 Django drafts, 20 package drafts, one per team, every untracked site in one draft | ✅ (trivially: nothing is tracked) |
| `staff_untracked` | 289 / 1,237: every site | ✅ but uninformative without tickets |
| `manager_facts`, `check_wrong_owner`, `staff_vague` | 0: they join tickets to call sites | ⛔ no dataset has both (G7) |
| Ticket check on Spark, adapted (open, mapped to code, no "done when") | 621 of 621 open mapped tickets fail it | 🟡 fires on everything: Apache Jira never writes "done when", so it can't tell tickets apart |
| Newest 500 Spark tickets | 48% empty description (241), 5.8% have any non-parent link (29), 0 `blocked_by` | matches "5% linked, 0 Blocks" |

## 6. Fragility ("risky call sites look like the rest")

Queries: `p7_risky_edit`, `stall_fragility_freeze`, `owner_teams.risky`.

![](figures/fragility.png)

*How many Spark files had each number of bug-fix commits in the year before the snapshot (log scale); files right of
the orange line (3 or more) count as risky. None of the 1,526 call sites is in these files, so the risk features
can't fire on real data.*

SZZ (it traces each bug fix back to the commit that caused it) gives 180 of 1,492 Spark files ≥ 3 bug fixes (max 36),
with ticket keys attached. No call site carries one because Spark has no tracked symbol, and Django and the packages
have no Jira. P7, "risky" and the fragility freeze stay quiet (G7) until one dataset has both.

## 7. Cross-team blocks and chains (the claims no competitor covers)

Queries: `staff_chains`, `derive_blocked_by`, `derive_depends_on`, `derive_collides`, `derive_duplicates`,
`stall_upstream`, `order_of_work`.

| | Real block | Why |
|---|---|---|
| `blocked_by` | 0 rows | `resolution.receiver_from` is null on all 3,739 rows. `url()` and `ugettext` are module functions, called with no receiver, so the A→B rule has nothing to read |
| `chain` | 43 rows, all `untracked` (23 Django teams, 20 packages). 0 `depends_on`, `collides`, `duplicates` | needs `ticket_map` → call site (G7) and `blocked_by` |
| `order_of_work` | 289 / 1,237 rows, but just ID order (no blocks, bug_fixes all 0) | |
| Cross-repo edges | 1,010 package → `django/django` edges, 0 package → package | the packages don't call each other (03-data) |

Real cross-team waits do exist in GitLab, at ticket level:

![](figures/graph_and_blocks.png)

*Left: of the row kinds the graph step can produce, only `untracked` appears on real data (grey is the test fixture,
where every kind appears). Right: GitLab has thousands of "blocked by" links, but the label rule keeps 116 and only 13
of those are real cross-team waits.*

Of GitLab's 2,166 `blocked_by` links, 1,890 are live and 1,683 have both ends in the snapshot. The doc's cheap rule
("the two issues carry different `group::` labels") keeps 116; 95 of those are in the model-read gold set, and only 13
are a real written wait (precision 0.14). It finds 13 of the gold set's 43 waits (recall 0.30), though 34 of the 43 do
have a KB link. Partial: the links are there, but group labels can't tell a cross-team wait from a planned sequence.
The gold set is model-read too: 0 of 262 rows have a human verdict, and the 26-row spot check (21 hold) is also by
model.

## 8. Push check (P1–P9)

The block-level queries (`p1_drift`, `p2_legacy_use`, `p3_collision`, `p5_ticket_can_move`, `p7_risky_edit`) return 0
on the real block: one run, no tickets on code datasets, no bug fixes on sites. `p9_defect_risk` works: 15 lines × 3
files = 45 for `4353640ea9`, and 633 × 6 = 3,798 for Spark's snapshot head. `push_scope_importers` finds 347 Django and
167 package importer pairs.

For the replay, each of the 21 pinned SHAs' next commit (Django's conversion and the 20 url() migrations) was run
through `meridian.pushcheck` against this block, once with settings.toml's limits and once with the scope limit lifted.

![](figures/push_replay.png)

*How often each push check fired across the 21 real migration commits, with the normal limits (blue) and with the
file-count limit lifted (grey). Only P1 and P9 ever fire; the others stay at 0 for the reasons below.*

With the real limits, 2 of 21 pushes are skipped (Django: 129 changed + 100 importers = 229 files; django-cms: 335), 6
get one P9 note, 1 (DRF) gets a P1 and a P9, and 12 are silent. That meets 05-test's "median ≤ 1 alert per push". With the limit
lifted, P1 fires 11 times. 4 are true: Django's `test_legacy_aliases` really adds 4 legacy calls. 7 are false, in DRF
(`tests/test_fields.py:1832`) and django-cms (6): each is `self.url = url`, a parameter named `url`. So P1 precision on
this replay is 4/11 = 0.36, below 05-test's 0.80 bar. The cause is the documented shortcut: there is no resolver at
push time, so a look-alike the resolver would drop still counts.

P2–P8 are 0 for structural reasons:

| Check | Why 0 |
|---|---|
| P2 | `ugettext` isn't deprecation-marked at that SHA (the 19 Django deprecations are other functions) |
| P3, P5, P6 | no tickets on these datasets |
| P4 | no `blocked_by` |
| P7 | no bug fixes on these files |
| P8 | Django and the packages have no Jira keys. On Spark, 944 of the last 1,000 commits carry a key and 0 name a missing ticket |

![](figures/p9_vs_szz.png)

*For 41k Spark commits, the share above each size (lines × files changed), split into commits SZZ blames for a later
bug (orange) and the rest (grey). The 2,000 threshold catches 32% of bug-inducing commits but only 9% of the others,
so commit size alone already carries risk signal.*

P9 is the one push check with real-data signal outside Django. On these 41,056 Spark first-parent commits since 2015 (those with at least one
`commit_file` row; 107 of the block's 41,163 have none),
a flagged commit is bug-inducing 49% of the time vs a 22% base rate. But SZZ blames changed lines, so big commits are
more likely to be blamed by construction. 05-test's real bar ("beats lines × files" with a time split) is
still open; this only shows the baseline has signal.

## 9. Stall reasons

| Query | `django@pin` result | Verdict |
|---|---|---|
| `stall_upstream` | 0 | ⛔ no `blocked_by` |
| `stall_never_notified` | all 23 teams (no tracker) | ✅ true, uninformative |
| `stall_ticket_mismatch` | 0 | ⛔ no tickets on code |
| `stall_orphaned_owner` | 2 files: `contrib/auth/forms.py` (top author silent 132 d), `tests/i18n/urls_default_unprefixed.py` (90 d). At the 2026 clock: 91 files, i.e. everyone | ✅ at the pin |
| `stall_semantics_unclear` | 2 sites inside `except`: `contrib/auth/forms.py:35`, `template/defaultfilters.py:849` | ✅ the exact case the manager rewrite asks about |
| `stall_fragility_freeze` | 0 | ⛔ bug_fixes = 0 |
| `stall_test_gap` | 57 Django modules (mostly `apps.py`), 196 package modules no test imports | ✅ plausible, not hand-checked |
| `stall_competing_load` | Django 0 (path teams have no people). Spark: 8 teams "busy elsewhere" | 🟡 on Spark it fires only because no team has call sites. Meaningless. Fixed since: only teams that own call sites count, 8 → 0 |

Against `gitlab_stall_reasons.csv` (57 blocked issues, model-read), 41 are "upstream", and 33 of those 41 (0.80) have
a `blocked_by` link in the block. So the most common real stall reason is visible in the data, at ticket level. But
the KB's stall queries read call sites, and GitLab has none. The other reasons (unknown 7, other 6, owner 2, risk 1)
live in comments, which aren't kept (G2).

## 10. Ticket → code mapping and link coverage

Query: `staff_link_coverage` → [36,138 mapped, 74,015 tickets]. That's 48.8% block-wide, diluted by GitLab, which
can't be mapped.

![](figures/ticket_mapping.png)

*How tickets get linked to code: a ticket key in a commit message, a stack trace, or a file path in the text. About
61% of Spark tickets reach a file; GitLab reaches none because its code (Ruby) wasn't scanned.*

Commit keys do most of the work (33,351). Stack traces map 4,435 tickets and paths in text 777, but most of those
also have a commit key: beyond commit keys they add 2,524 and 263. 23,353 Spark tickets stay
unmapped, and GitLab is 0 of 14,524.

The mapping is to paths, not call sites, so it can't feed P3, P5 or collisions until a tracked symbol lives in a
Jira-tracked repo. The best path-level collision rule scores 0.64 on the 50 Spark pairs (32 real; 48 of 50 have both
keys mapped to the file). The labels' function-level flag (`inside`) agrees with the verdict 50/50, which is why
05-test wants function scope. This confirms the 02-product known gap ("P3, P5, chains go quiet").

## 11. Legacy radar

`radar_deprecated_with_callers` → 0. The block has 52 deprecation markers (19 in Django, e.g.
`contrib.auth.views.login`, `RemovedInDjango21Warning`), but `call_edge` only covers callers of tracked symbols, and
none of the 52 is tracked. Quiet (G3): it needs the whole-repo call graph.

## 12. Tracking whether suggestions work

`outcome_precision`, `auto_apply_gate`, `outcome_before_after`, `outcome_effect`, `push_alert_outcomes` and
`outcome_week8` don't run: the `suggestion`, `outcome` and `push_alert` tables are proposed in the doc but not in
schema.sql (G1). They pass on the fixture only and stay quiet by design until stage 6.

## 13. Answer keys in one picture

![](figures/labels_vs_block.png)

*Each row compares the block with an answer key: dots are precision (share of the block's hits that are right),
squares are recall (share of real cases the block finds), and the dashed line is the 0.90 bar. The code side clears
it; the ticket and cross-team side does not yet.*

The scores come from §1, §7, §9 and §10. The Spark and GitLab keys are model-read, not human-checked, so those
numbers are provisional.

## Issues found in passing

The numbers above are from the run before items 1–4 were fixed.

1. `ticket_open` treated every GitLab issue as open. The view keyed on `Done/Closed/Resolved` and GitLab writes
   `closed`, so 14,524 issues read as open instead of 3,680. Fixed since: `ticket_open`, `stall_ticket_mismatch` and
   graph.py's collides rule compare done statuses case-insensitively, so by the status rule alone GitLab open issues
   are 3,680. The fixed `ticket_open` view also requires a mapping to a call site, so on 70dd it returns 0 GitLab
   issues.
2. The doc's queries ignored `asof.repo` and `asof.symbol`, so on a multi-dataset block they mixed datasets (see "How
   the queries were run"). Fixed since: they are scoped to both (NULL = all); django/django + `ugettext` gives 93
   sites, not 1,526 from 25 repos.
3. `derive_blast_radius` disagreed with graph's `blast_radius` at depth 3 (+25 for `ugettext`). Fixed since: 209 /
   241 / 135, matching graph's table.
4. `stall_competing_load` fired for teams with no call sites (8 Spark teams). Fixed since: it only considers teams
   that own call sites, so 0.
5. Still open: `receiver_from` is never set on real data, so `blocked_by`, P4, `consumer_blocked` and `depends_on` can't be
   exercised outside fixtures. This needs a tracked method symbol (the week-1 "method-call benchmark").
