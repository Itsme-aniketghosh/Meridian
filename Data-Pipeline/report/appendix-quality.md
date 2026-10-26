# Appendix · Testing, data quality and bias (run 70dd1dabfed544b8)

Sources: `stats.json`, `anomalies.json`, `bias.json` and `meridian.sqlite` of team run `70dd1dabfed544b8` (copies taken Oct 25), and the unit tests run on Oct 25 (Windows 11, Python 3.11). Charts: `python report/figures_quality.py <dir with the three json files>` from `Data-Pipeline/` (needs matplotlib).

## 1. Tests

`python -m unittest discover -s tests -t .` from `Data-Pipeline/`, without team data: 285 tests, OK, 14 skipped, 0 failures, 0 errors. The saved verbose run (`tests_light.txt`: 261 tests in 79 s, 248 passed) and the chart below predate one new regression test in `test_kb.py`.

![](figures/tests_per_file.png)

*Each bar is one test file: blue tests passed, grey ones were skipped. Every pipeline part has its own tests, and skips only appear in files that need Airflow, GX or the private team data.*

The 22 files are one per pipeline part, plus cross-part ones (DAG structure, push checks, cache equivalence, golden hashes). The edge cases (renames, splits, parse failures, missing values, schema violations, broken alert channels) are fixtures inside them.

The 13 skips are all by design:

| Skipped | N | Why |
|---|---:|---|
| `test_dag.Dag.*` (DagBag load, spec, settings, trigger) | 4 | Airflow isn't installed on the host; CI's `airflow` job runs them in the Docker image |
| `test_gx.Audit.*` (suite on a block, Data Docs) | 2 | GX lives in its own `[gx]` environment; CI's `gx` job runs them |
| `test_golden.TeamRun`, `test_resolve.DjangoAnswerKey` ×2, `test_resolve.PackagesScore`, `test_scan.DjangoAnswerKey` ×3 | 7 | Need the private team data (`MERIDIAN_RUN_ID` and `out/70dd…/`, or Django in `snapshots/dev/`) |

TESTING.md's counts have been updated to match (it said `Ran 248 … skipped=10` on Oct 24). The tests added since include `test_gx` with its 2 GX-only skips, and `test_dag` now has 4 Airflow-only tests, not 3.

Team-run tests (`MERIDIAN_RUN_ID=70dd1dabfed544b8`, Oct 25, `tests_team.txt`):

| Test | Result |
|---|---|
| `test_scan.DjangoAnswerKey`: 265 of 265 edit lines, 0 false; 2 lines outside `.py`; no tracked name lost to parse failures | ✅ 3 of 3 |
| `test_resolve.DjangoAnswerKey`: 542 of 542 aliased `_()` calls; every edit line resolves | ✅ 2 of 2 |
| `test_resolve.PackagesScore`: precision and recall on the 20 Django packages | ✅ 1 of 1 |
| `test_golden.TeamRun`: md5 of the 24 `.jsonl` outputs vs `golden/70dd1dabfed544b8.json` (Mac, schema v1.2) | ❌ 20 of 24 match |

`test_scan` and `test_resolve` together: 60 tests, 254 s, OK, none skipped. Of the 4 golden mismatches, 3 (`resolution`, `call_edge`, `blast_radius`) are likely the resolver's known Windows gap (TESTING.md, notes/UPDATE-PLAN.md), not yet verified. `call_site` is likely the join fix made after the freeze: the outputs on disk date from Oct 24, before the golden file was regenerated for schema v1.2 and before join's "no stolen matches" fix (`b277e65`, Oct 25). A fresh Linux run in Airflow will settle it. The fixture half of the golden check (hashes of all 25 tables) runs everywhere and passed.

## 2. Schema and statistics

`meridian.validate` checks the block against `schema.sql` (types, NOT NULL, allowed values, keys, one `run_id`), then writes per-column statistics to `stats.json`. On run 70dd: 25 tables, 1,788,242 rows, 0 schema violations, 0 data errors.

![](figures/rows_per_table.png)

*Rows in each table, on a log scale (each step right is 10 times more rows). Raw git and Jira history make up most of the block; the tables the product shows are small.*

Git history dominates (`commit_file` 849,643, `git_commit` 139,228), then Jira (`ticket_map` 246,428, `ticket_event` 235,252, `ticket` 74,015). The output is 1,526 call sites, 25 blast-radius rows and 43 ticket chains; `blocked_by` is empty.

![](figures/missing_share.png)

*Share of empty values in each of the 25 columns that have any. The tall bars are empty by design (`old_path`, for example, is only set when a file is renamed), and every column stays within its allowed limit, so none raised a warning.*

The limits are in `settings.toml [validate.missing]` (unlisted columns allow none). The high shares are structural: `commit_file.old_path` (94.9%) is set on renames only, `file_owner.top_person_id` (74.6%) is empty for path- and repo-owned files, `ticket.parent_key` (77.3%) is for sub-tasks, and `resolution.receiver_from` (100%) is for a call pattern that doesn't occur on 70dd. The near-zero ones are single real rows: 23 root commits without a parent, 1 commit with an empty author email, 5 tickets and 18 events by deleted accounts.

Coverage (the `coverage` table; floors in `[validate.coverage]`): 1,525 of 1,526 call sites resolved (floor 0.95), 1,526 of 1,526 owned (floor 0.95), and 36,138 of 59,491 Jira tickets mapped to code = 0.61 (floor 0.50). The 14,524 GitLab tickets map to nothing by design: GitLab is Ruby, outside the snapshot.

## 3. Anomalies

![](figures/anomalies.png)

*`anomalies.json` holds 0 errors and 1 warning: 289 tickets with impossible creation dates. The bad dates come from the source, so they are flagged, not fixed.*

The warning is on `ticket.created_at`: 289 dates before 2000-01-01, from 0010-04-03 to 0012-10-19 (43 in year 0010, 63 in 0011, 183 in 0012). They are Spark tickets imported in 2013 from Spark's old tracker, and Apache Jira itself shows those dates, so the pipeline doesn't invent a corrected one. In the bias check those tickets map to code 2.3–5.5% of the time, the lowest slices by year. The warning was logged; no Slack or email channel is configured, so it went nowhere else. No outlier, drift (first run, nothing to compare) or row-count warnings.

## 4. The weekly GX audit

`meridian/gx.py` turns validate's generic checks into one Great Expectations (GX) suite per table: minimum rows, missing-value allowances (`ExpectColumnValuesToNotBeNull` with `mostly`), regex formats (UTC times, 16-hex IDs, 40-hex SHAs, repos, paths), numeric ranges, and dates between 2000-01-01 and the snapshot time. Domain checks (coverage floors, IQR outliers, drift, sorted JSON lists) stay in validate. The audit runs in the weekly `meridian_rebuild` DAG beside `bias`, in its own environment (`requirements-gx.txt`), and publishes Data Docs to `published/gx/<run_id>/`.

It never gates. A failed expectation alerts as a warning and the task succeeds. An expectation GX couldn't evaluate alerts as an error and exits 1, so a broken audit isn't read as clean data.

On 70dd the suite would hold 281 expectations over 25 tables: 191 missing, 52 formats, 21 ranges, 9 dates, 8 rows (counted with `gx.specs`, which doesn't need GX). The audit was not run here: no environment on this machine has `great_expectations`, and loading each table into pandas needs more memory than the machine had. Going by validate, one warning-level expectation should fail: the 289 dates in years 0010–0012 (`as_datetime` keeps year 0010, which pandas timestamps can't). Of the 7 tests in `tests/test_gx.py`, 5 passed here (specs come from `[validate]`, year 0010 survives, a GX crash is told apart from a failure, never-gate exit codes, no audit without a block). The 2 that need GX (a clean block with Data Docs published, bad rows alerted without gating) were skipped.

## 5. Bias (data slicing)

`meridian.bias` slices the pipeline's three deciding rules with Fairlearn's `MetricFrame`. A slice is a subgroup, such as one module or one team. Each slice gets a Wilson 95% interval. A slice is judged when N ≥ 30, and flagged when its whole interval is more than 0.10 below overall, so a gap the slice's own noise could explain isn't flagged. A slice under N = 30 is unjudged, not cleared. In the charts the whiskers are the intervals, the solid line is overall, the dashed line is the flag line, red bars are flagged, and unjudged slices are left out. Ticket mapping and resolver below are recomputed with the current `bias.py` from 70dd's `meridian.sqlite`. Owner-rule numbers come from 70dd's `bias.json`. The owner rule's new per-slice numbers, the prior-commits slice and the held-out check wait for the rerun. BIAS.md has the full list.

![](figures/bias_owner_rule.png)

*How often the owner rule names the right team (top panels) and what share of files get an owner (bottom). `unassigned` and `Project Infra` sit at 0.00, so no bar shows.*

Owner rule: does a file's owner team predict the team of its next author (Spark)? 70dd reports 0.373 (N = 988), with `unassigned` at 0.000 on N = 244. That 0.000 holds by construction: the rule never names `unassigned`, and the same team lookup builds both the prediction and the label. So `bias.py` now scores accuracy only on next authors with a team, **0.496 (N = 744, CI 0.460–0.532)**, and reports the unassigned rate (24.7%) as coverage on its own. `Project Infra` (0.000 on exactly N = 30, CI 0–0.114, borderline) and module `core/` (0.167, N = 78, CI 0.100–0.265) stay flagged. Owned share is 0.543 overall (N = 1,819); `examples/` 0.028 (N = 143), `mllib/` 0.149 (N = 94) and `common/` 0.281 (N = 64) are flagged. A people-level slice, by the next author's commit count before the cut (0, 1, 2–10, >10), is added. There are no email-domain slices, for privacy.

![](figures/bias_ticket_mapping.png)

*Share of Spark tickets that map to code, by component (a ticket counts in each of its components), by component since 2020, by type and by year. Old tickets map far less because their files are gone. Eight components stay low even since 2020.*

Ticket → code mapping: 0.608 per ticket (N = 59,491). 18% of tickets have several components, and each now counts in every one of them (0.612 over N = 71,845 pairs). 11 of 36 judged components are flagged, including EC2 0.083, Spark Docker 0.209, Mesos 0.287, `(none)` 0.334 and Kubernetes 0.389 (0.375 by first component). Age explains much of it: tickets from before 2014 map under 25% of the time, 2014 on 0.52–0.72. All 1,382 `(none)` tickets were filed 2017 or earlier. **Holding age fixed doesn't clear the gap.** Since 2020 (0.676, N = 35,285), EC2 (N = 8) and Mesos (N = 17) are unjudged, not cleared, and 8 of 32 judged components stay flagged: Spark Docker 0.209, Input/Output 0.315, Spark Submit 0.325, Java API 0.333, Spark Shell 0.344, Examples 0.354, Kubernetes 0.372 (N = 1,762) and Deploy 0.426. Connect (0.566, N = 3,194) is 0.11 below overall, but its interval reaches the line. Wish, Brainstorming and Question tickets (0.12–0.16) carry no code by nature.

![](figures/bias_resolver.png)

*Share of candidate call sites the resolver keeps, per repo. Every judged repo's interval reaches the 0.981 average.*

Resolver: 0.981 of edit-role candidates kept as call sites overall (N = 1,556), 0 of 12 judged repos flagged. The lowest judged repo is jazzband/django-silk at 0.933 (N = 30). django-tenants (0.857, N = 7) is unjudged.

Ticket mapping and resolver judge 111 slices and flag 27. The owner rule judged 17 on 70dd and flagged 6. The rule isn't corrected for multiple comparisons. Under Bonferroni, `core/`, `common/`, Deploy, Spark Shell and all-time Spark Submit no longer hold. Fairlearn's difference / ratio over judged slices: component 0.690 / 0.107, component since 2020 0.608 / 0.256, type 0.645 / 0.153, year 0.692 / 0.033, resolver 0.067 / 0.933.

### Mitigation and trade-offs

![](figures/bias_mitigation.png)

*Each candidate fix for the owner rule, scored four ways (arrows show which way is better). A 730-day team lookback (red) gives the best accuracy and the fewest unassigned authors without widening the module gap.*

Scored on the same files (`bias.json` `owner_rule.mitigations`, N = 1,819). The accuracy on next authors with a team is derived from 70dd's counts:

| Variant | Accuracy (unassigned = miss) | Accuracy, next authors with a team | Files owned | Next author unassigned | Worst module gap |
|---|---:|---:|---:|---:|---:|
| as configured (`min_share` 0.30, teams from 180 days) | 0.373 | 0.496 | 988 | 24.7% | 0.207 |
| `min_share` 0.20 / 0.10 | 0.372 | 0.493 | 992 | 24.6% | 0.205 |
| teams from 365 days | 0.439 | 0.538 | 989 | 18.5% | 0.234 |
| **teams from 730 days** | **0.458** | **0.545** | **989** | **16.0%** | **0.202** |
| fall back to the last toucher's team | 0.303 | 0.395 | 1,634 | 23.2% | 0.278 |

Lowering the share threshold changes nothing: files are unowned because their authors have no team, not because shares sit near 0.30. Falling back to the last toucher owns 65% more files but is wrong more often and widens the worst module gap to 0.278. A longer team lookback helps, but each variant is scored against its own labels. The 730-day lookback gives a team to 86 next authors who were guaranteed misses before. Without them, accuracy goes 0.496 → 0.545 (+0.049, with overlapping intervals), and unassigned next authors fall 24.7% → 16.0%. The cost is that a two-year-old team can be stale for someone who moved. `bias.py` now checks the pick held out. It picks the variant at a cut one window earlier (2025-10-06), confirms it on the current cut (2026-04-04), and puts a seeded paired-bootstrap 95% interval on the delta. Those numbers come with the rerun.

Applied now, without a new run_id: every slice in `bias.json` carries its N, interval and flag, so the views can show "low coverage" next to a flagged module, team or component, and "too few to judge" under N = 30. Next run (team decision): `[teams.spark] lookback_days = 730`. It changes `meridian.toml` and `owners.py`, so it needs a new team run_id. Adopt it if the held-out interval stays above 0. For ticket mapping, fuzzy matching is ruled out, so the fix is coverage: add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot. `tests/test_bias.py` (9 tests, passed) pins the CI flag rule, a slice at exactly N = 30, multi-component tickets, the prior-commits slice and the held-out scoring on a synthetic history.
