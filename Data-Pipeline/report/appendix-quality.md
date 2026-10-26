# Appendix B: Data quality and bias

Sources: `stats.json`, `anomalies.json`, `bias.json` and `meridian.sqlite` of team run `70dd1dabfed544b8` from the Airflow run of Oct 26 ([Appendix A](appendix-airflow.md)), and the unit tests run the same day (macOS, Python 3.11). The charts come from `python report/figures_quality.py <dir with the three json files>`, run from `Data-Pipeline/`.

## 1. Tests

`python -m unittest discover -s tests -t .` from `Data-Pipeline/`, without team data: 293 tests, OK, 14 skipped, 0 failures, 0 errors (verbose run in `tests_light.txt`).

![](figures/tests_per_file.png)

*Tests per file, passed (blue) and skipped (grey): every part has its own tests, and skips only appear where Airflow, GX or the private team data is needed.*

There is one file per pipeline part, plus files for the DAG structure, push checks, cache equivalence and golden hashes. The edge cases are fixtures inside them: renames, splits, parse failures, missing values, schema violations, broken alert channels. Each skip was run in its own environment:

| Skipped here | N | Why | Run where | Result |
|---|---:|---|---|---|
| `test_dag.Dag.*` (DagBag load, spec, settings, trigger, templates) | 5 | Airflow isn't on the host | Airflow 2.10.5; CI's `airflow` job in the Docker image | ✅ 13 of 13 in `test_dag` |
| `test_gx.Audit.*` (suite on a block, Data Docs) | 2 | GX has its own `[gx]` environment | GX 1.24.1; CI's `gx` job | ✅ 7 of 7 in `test_gx` |
| `test_golden.TeamRun`, `test_resolve.DjangoAnswerKey` ×2, `test_resolve.PackagesScore`, `test_scan.DjangoAnswerKey` ×3 | 7 | Need the private team data | With `MERIDIAN_RUN_ID=70dd1dabfed544b8` | ✅ below |

With the team data the whole suite is 293 tests, OK, 7 skipped. The team-run tests ran on the outputs of the Airflow run (`tests_team.txt`: 62 tests, 40 s, OK):

| Test | Result |
|---|---|
| `test_scan.DjangoAnswerKey`: 265 of 265 edit lines, 0 false; 2 lines outside `.py`; no tracked name lost to parse failures | ✅ 3 of 3 |
| `test_resolve.DjangoAnswerKey`: 542 of 542 aliased `_()` calls; every edit line resolves | ✅ 2 of 2 |
| `test_resolve.PackagesScore`: precision and recall on the 20 Django packages | ✅ 1 of 1 |
| `test_golden.TeamRun`: md5 of the 24 `.jsonl` outputs vs `golden/70dd1dabfed544b8.json` | ✅ 24 of 24 |

The golden check was 20 of 24 on Windows on Oct 25. `resolution`, `call_edge` and `blast_radius` differed by Jedi's folder order and `commit_file` by git version ([Appendix D](appendix-testing.md), round 5), and `call_site` on disk predated join's "no stolen matches" fix. Its fixture half, which hashes all 25 tables, runs everywhere and passes.

## 2. Schema and statistics

`meridian.validate` checks the block against `schema.sql` (types, NOT NULL, allowed values, keys, one `run_id`) and writes per-column statistics to `stats.json`. On 70dd: 25 tables, 1,788,242 rows, 0 schema violations, 0 data errors.

![](figures/rows_per_table.png)

*Rows per table on a log scale: raw git and Jira history make up most of the block, and the tables the product shows are small.*

The biggest are `commit_file` (849,643) and `ticket_map` (246,428). The output is 1,526 call sites, 25 blast-radius rows and 43 ticket chains; `blocked_by` is empty.

![](figures/missing_share.png)

*Share of empty values in each of the 25 columns that have any: the tall bars are empty by design, and every column stays within its limit.*

The limits are in `settings.toml [validate.missing]`; unlisted columns allow none. The high shares are structural: `commit_file.old_path` (94.9%) is set on renames only, `file_owner.top_person_id` (74.6%) is empty for path- and repo-owned files, `ticket.parent_key` (77.3%) is for sub-tasks, and `resolution.receiver_from` (100%) is for a call pattern that doesn't occur on 70dd. The near-zero ones are single real rows: 23 root commits without a parent, 1 commit with an empty author email, 5 tickets and 18 events by deleted accounts.

Coverage (floors in `[validate.coverage]`): 1,525 of 1,526 call sites resolved (floor 0.95), 1,526 of 1,526 owned (floor 0.95), and 36,138 of 59,491 Jira tickets mapped to code, 0.61 (floor 0.50). The 14,524 GitLab tickets map to nothing by design: GitLab is Ruby, outside the snapshot.

## 3. Anomalies

![](figures/anomalies.png)

*`anomalies.json` holds 0 errors and 1 warning, 289 tickets with impossible creation dates, which come from the source and are flagged, not fixed.*

The warning is on `ticket.created_at`: 289 dates before 2000-01-01, from 0010-04-03 to 0012-10-19 (43 in year 0010, 63 in 0011, 183 in 0012). They are Spark tickets imported in 2013 from Spark's old tracker, and Apache Jira itself shows those dates, so the pipeline doesn't invent a corrected one. They map to code 2.3–5.5% of the time, the lowest year slices in §5. It was logged, and with no Slack or email channel set it went nowhere else. No outlier, drift or row-count warnings.

## 4. The weekly GX audit

`meridian/gx.py` turns validate's generic checks into one Great Expectations suite per table: minimum rows, missing-value allowances (`ExpectColumnValuesToNotBeNull` with `mostly`), regex formats (times, IDs, SHAs, repos, paths), numeric ranges and date bounds. Coverage floors, outliers and drift stay in validate. It runs in the weekly rebuild beside `bias`, in its own environment (`requirements-gx.txt`).

It never gates. A failed expectation alerts as a warning and the task succeeds. An expectation GX couldn't evaluate alerts as an error and exits 1, so a broken audit isn't read as clean data.

On 70dd it ran 308 expectations over the 25 tables, 0 failed, and published its Data Docs to `published/gx/70dd1dabfed544b8/index.html`.

![](figures/gx_data_docs.jpg)

*GX marks `ticket` with a cross because its date check finds the 289 tickets dated 0010–0012; like validate, the audit allows those 289 (`settings.toml [validate.dates] allow`) and fails only above them, so one more bad date would alert.*

## 5. Bias (data slicing)

`meridian.bias` slices the pipeline's three deciding rules with Fairlearn's `MetricFrame`, with a Wilson 95% interval per slice. A slice is judged when N ≥ 30 and flagged when its whole interval is more than 0.10 below overall, so a gap its own noise could explain isn't flagged. On the outputs of the Airflow run: 136 slices judged, 32 flagged, all listed in [BIAS.md](../BIAS.md). In the charts, whiskers are the intervals, the solid line is overall, the dashed line is the flag line and red bars are flagged.

![](figures/bias_owner_rule.png)

*How often the owner rule names the next author's team (top three panels) and what share of authors and files get one (bottom two): `core/` and `Project Infra` are flagged, and Project Infra sits at 0.00, so no bar shows.*

Owner rule (Spark): does a file's owner team predict its next author's team? Counting authors with no team as misses gives 0.373 (N = 988), but `unassigned` scores 0.000 by construction, since the same team lookup builds the prediction and the label. So accuracy is scored on next authors with a team: 0.496 (N = 744, CI 0.460–0.532), with the unassigned rate (24.7%) reported as coverage. Flagged: module `core/` 0.228 (N = 57) and team `Project Infra` 0.000 on exactly N = 30, borderline. The people-level slice, by the next author's commits before the cut, flags nothing. Owned share is 0.543 (N = 1,819), with `examples/` 0.028, `mllib/` 0.149 and `common/` 0.281 flagged. There are no email-domain slices, for privacy.

![](figures/bias_ticket_mapping.png)

*Share of Spark tickets that map to code by component, by component since 2020, by type and by year: old tickets map far less because their files are gone, and eight components stay low even since 2020.*

Ticket to code mapping: 0.608 per ticket (N = 59,491). A ticket counts in each of its components (0.612 over N = 71,845 pairs), and 11 of 36 judged components are flagged, among them EC2 0.083, Spark Docker 0.209 and Kubernetes 0.389. Age explains much of it: tickets from before 2014 map under 25% of the time, 2014 on 0.52–0.72. Holding age fixed doesn't clear the gap. Since 2020 (0.676, N = 35,285), EC2 and Mesos are too few to judge, and 8 of 32 judged components stay flagged, Kubernetes 0.372 (N = 1,762) among them. Wish, Brainstorming and Question tickets (0.12–0.16) carry no code by nature.

![](figures/bias_resolver.png)

*Share of candidate call sites the resolver keeps, per repo: every judged repo's interval reaches the 0.981 average.*

Resolver: 0.981 kept overall (N = 1,556), 0 of 12 judged repos flagged; the lowest judged is jazzband/django-silk at 0.933 (N = 30).

Ticket mapping and resolver judge 111 slices and flag 27; the owner rule judges 25 and flags 5. The rule isn't corrected for multiple comparisons. Under Bonferroni (z = 3.56 for 136 slices) 26 of the 32 hold; `core/`, `common/`, Deploy (all time and since 2020), GraphX and Spark Shell don't, so treat those as likely, not established. Fairlearn's difference / ratio over judged slices: component 0.690 / 0.107, component since 2020 0.608 / 0.256, type 0.645 / 0.153, year 0.692 / 0.033, resolver 0.067 / 0.933.

### Mitigation and trade-offs

![](figures/bias_mitigation.png)

*Each candidate fix for the owner rule, scored four ways: the 730-day team lookback (red) is best in-sample, but the held-out check keeps 180 days.*

All variants are scored on the same files (`bias.json` `owner_rule.mitigations`, N = 1,819); the module gap is on next authors with a team.

| Variant | Accuracy (unassigned = miss) | Accuracy, next authors with a team | Files owned | Next author unassigned | Worst module gap |
|---|---:|---:|---:|---:|---:|
| as configured (`min_share` 0.30, teams from 180 days) | 0.373 | 0.496 | 988 | 24.7% | 0.268 |
| `min_share` 0.20 / 0.10 | 0.372 | 0.493 | 992 | 24.6% | 0.265 |
| teams from 365 days | 0.439 | 0.538 | 989 | 18.5% | 0.263 |
| teams from 730 days | 0.458 | 0.545 | 989 | 16.0% | 0.242 |
| fall back to the last toucher's team | 0.303 | 0.395 | 1,634 | 23.2% | 0.369 |

A lower share threshold changes nothing, since files are unowned because their authors have no team. Falling back to the last toucher owns 65% more files but is wrong more often. The 730-day lookback gives a team to 86 next authors who were guaranteed misses before; without them, accuracy goes 0.496 → 0.545 (+0.049, overlapping intervals), and a two-year-old team can be stale for someone who moved.

`bias.py` checks the pick held out: it picks at a cut one window earlier (2025-10-06), confirms on the current cut (2026-04-04), and puts a seeded paired-bootstrap 95% interval on the delta. At the earlier cut the configured 180-day rule wins (0.505 vs 0.502 for 730 days, on 1,279 files), so the held-out delta is 0.0 (interval 0.0–0.0). The gain is in-sample only, and the 730-day lookback is not adopted. Its coverage gain (unassigned 24.7% → 16.0%) stays a team call that would need a new run_id.

Applied now: every slice in `bias.json` carries its N, interval and flag, so the views can show "low coverage" next to a flagged slice and "too few to judge" under N = 30. For ticket mapping, fuzzy matching is ruled out, so the fix is coverage: add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot. `tests/test_bias.py` (9 tests, passed) pins the flag rule, the N = 30 edge, multi-component tickets, the prior-commits slice and the held-out scoring.
