# Appendix B: Data quality and bias

Everything here comes from team run `70dd1dabfed544b8` in the Airflow run of Oct 26 ([Appendix A](appendix-airflow.md)): its `stats.json`, `anomalies.json`, `bias.json` and `meridian.sqlite`, plus the unit tests run the same day (macOS, Python 3.11). The charts come from `python report/figures_quality.py <dir with the three json files>`, run from `Data-Pipeline/`.

## 1. Tests

From `Data-Pipeline/`, without team data, `python -m unittest discover -s tests -t .` runs 293 tests: OK, 14 skipped, 0 failures, 0 errors (verbose run in `tests_light.txt`).

![](figures/tests_per_file.png)

*Tests per file, passed in blue and skipped in grey, with skips only where Airflow, GX or the private team data is needed.*

There's one test file per pipeline part, plus files for the DAG (Airflow's task graph), push checks, cache equivalence and golden hashes (md5 sums of a known-good run's outputs). The edge cases are fixtures inside them: renames, splits, parse failures, missing values, schema violations, broken alert channels. We ran each skipped test in its own environment:

| Skipped here | N | Why | Run where | Result |
|---|---:|---|---|---|
| `test_dag.Dag.*` (DagBag load, spec, settings, trigger, templates) | 5 | Airflow isn't on the host | Airflow 2.10.5; CI's `airflow` job in the Docker image | ✅ 13 of 13 in `test_dag` |
| `test_gx.Audit.*` (suite on a block, Data Docs) | 2 | GX has its own `[gx]` environment | GX 1.24.1; CI's `gx` job | ✅ 7 of 7 in `test_gx` |
| `test_golden.TeamRun`, `test_resolve.DjangoAnswerKey` ×2, `test_resolve.PackagesScore`, `test_scan.DjangoAnswerKey` ×3 | 7 | Need the private team data | With `MERIDIAN_RUN_ID=70dd1dabfed544b8` | ✅ below |

With the team data the whole suite is 293 tests, OK, 7 skipped. The team-run tests ran on the Airflow run's outputs (`tests_team.txt`: 62 tests, 40 s, OK):

| Test | Result |
|---|---|
| `test_scan.DjangoAnswerKey`: 265 of 265 edit lines, 0 false; 2 lines outside `.py`; no tracked name lost to parse failures | ✅ 3 of 3 |
| `test_resolve.DjangoAnswerKey`: 542 of 542 aliased `_()` calls; every edit line resolves | ✅ 2 of 2 |
| `test_resolve.PackagesScore`: precision and recall on the 20 Django packages | ✅ 1 of 1 |
| `test_golden.TeamRun`: md5 of the 24 `.jsonl` outputs vs `golden/70dd1dabfed544b8.json` | ✅ 24 of 24 |

On Windows on Oct 25 the golden check was 20 of 24. Jedi's folder order changed `resolution`, `call_edge` and `blast_radius`, the git version changed `commit_file` ([Appendix D](appendix-testing.md), round 5), and `call_site` on disk predated join's "no stolen matches" fix. The fixture half, which hashes all 25 tables, runs everywhere and passes.

## 2. Schema and statistics

`meridian.validate` checks the block against `schema.sql` (types, NOT NULL, allowed values, keys, one `run_id`) and writes statistics for each column to `stats.json`. On 70dd: 25 tables, 1,788,242 rows, 0 schema violations, 0 data errors.

![](figures/rows_per_table.png)

*Rows per table on a log scale, where raw git and Jira history make up most of the block.*

The biggest are `commit_file` (849,643) and `ticket_map` (246,428). The output is 1,526 call sites, 25 blast-radius rows and 43 ticket chains. `blocked_by` is empty.

![](figures/missing_share.png)

*Share of empty values in each of the 25 columns that have any, all within their limits.*

Limits are in `settings.toml [validate.missing]`, and unlisted columns allow none. The high shares are empty by design. `commit_file.old_path` (94.9%) is only set on renames, `file_owner.top_person_id` (74.6%) is empty for path- and repo-owned files, `ticket.parent_key` (77.3%) is only for sub-tasks, and `resolution.receiver_from` (100%) is for a call pattern that doesn't occur on 70dd. The near-zero ones are single real rows: 23 root commits without a parent, 1 commit with an empty author email, and 5 tickets and 18 events by deleted accounts.

Coverage floors are in `[validate.coverage]`. 1,525 of 1,526 call sites resolved (floor 0.95), 1,526 of 1,526 owned (floor 0.95), and 36,138 of 59,491 Jira tickets mapped to code, 0.61 (floor 0.50). The 14,524 GitLab tickets map to nothing by design, because GitLab is Ruby and outside the snapshot.

## 3. Anomalies

![](figures/anomalies.png)

*`anomalies.json` has 0 errors and 1 warning, for 289 tickets with impossible creation dates.*

The warning is on `ticket.created_at`: 289 dates before 2000-01-01, from 0010-04-03 to 0012-10-19 (43 in year 0010, 63 in 0011, 183 in 0012). They're Spark tickets imported in 2013 from Spark's old tracker. Apache Jira itself shows those dates, so we flag them and don't make up a corrected one. They map to code 2.3–5.5% of the time, the lowest year slices in §5. With no Slack or email channel set up, the warning only went to the log. There were no outlier, drift or row-count warnings.

## 4. The weekly GX audit

`meridian/gx.py` turns validate's generic checks into one Great Expectations suite per table: minimum rows, missing-value allowances (`ExpectColumnValuesToNotBeNull` with `mostly`), regex formats (times, IDs, SHAs, repos, paths), numeric ranges and date bounds. Coverage floors, outliers and drift stay in validate. The audit runs in the weekly rebuild next to `bias`, in its own environment (`requirements-gx.txt`).

It never gates. A failed expectation alerts as a warning and the task succeeds. An expectation GX couldn't evaluate alerts as an error and exits 1, so a broken audit can't pass for clean data.

On 70dd it ran 308 expectations over the 25 tables, 0 failed, and published its Data Docs to `published/gx/70dd1dabfed544b8/index.html`.

![](figures/gx_data_docs.jpg)

*GX marks `ticket` with a cross for the 289 old dates, which the audit allows (`settings.toml [validate.dates] allow`), so only one more bad date would alert.*

## 5. Bias (data slicing)

`meridian.bias` slices the pipeline's three deciding rules with Fairlearn's `MetricFrame`. Each slice gets a Wilson 95% interval, a confidence interval for a share that still behaves on small samples. A slice is judged when N ≥ 30, and flagged when its whole interval is more than 0.10 below overall, so a gap that noise alone could explain isn't flagged. On the Airflow run's outputs, 136 slices were judged and 32 flagged. [BIAS.md](../BIAS.md) lists them all, with the full mitigation table. In the charts, whiskers are the intervals, the solid line is overall, the dashed line is the flag line and red bars are flagged.

![](figures/bias_owner_rule.png)

*How often the owner rule names the next author's team (top) and what share of authors and files get a team (bottom), with `core/` and `Project Infra` (at 0.00, so no bar) flagged.*

Owner rule (Spark): does a file's owner team predict its next author's team? Counting authors with no team as misses gives 0.373 (N = 988). But `unassigned` scores 0.000 by construction, because the same team lookup builds both the prediction and the label. So we score accuracy on next authors who have a team, 0.496 (N = 744, CI 0.460–0.532), and report the unassigned rate (24.7%) as coverage. Flagged: module `core/` 0.228 (N = 57), and team `Project Infra` 0.000 on exactly N = 30, which is borderline. The slice by the next author's prior commits flags nothing. Owned share is 0.543 (N = 1,819), with `examples/` 0.028, `mllib/` 0.149 and `common/` 0.281 flagged. We don't slice by email domain, for privacy.

![](figures/bias_ticket_mapping.png)

*Share of Spark tickets that map to code by component, by component since 2020, by type and by year.*

Ticket to code mapping is 0.608 per ticket (N = 59,491), or 0.612 over N = 71,845 (ticket, component) pairs. 11 of 36 judged components are flagged, among them EC2 0.083, Spark Docker 0.209 and Kubernetes 0.389. Age explains a lot of it. Tickets from before 2014 map under 25% of the time because their files are gone, and from 2014 on 0.52–0.72. Holding age fixed doesn't close the gap, though. Since 2020 (0.676, N = 35,285), EC2 and Mesos are too few to judge, and 8 of 32 judged components stay flagged, Kubernetes 0.372 (N = 1,762) among them. Wish, Brainstorming and Question tickets (0.12–0.16) don't carry code by nature.

![](figures/bias_resolver.png)

*Share of candidate call sites the resolver keeps per repo, where every judged repo's interval reaches the 0.981 average.*

The resolver keeps 0.981 overall (N = 1,556), and 0 of 12 judged repos are flagged. The lowest judged repo is jazzband/django-silk at 0.933 (N = 30).

Our flag rule isn't corrected for multiple comparisons, so with 136 slices a few flags could be chance. Under Bonferroni (a stricter cutoff for many tests, here z = 3.56), 26 of the 32 hold. `core/`, `common/`, Deploy (all time and since 2020), GraphX and Spark Shell don't, so treat those as likely, not established. BIAS.md has Fairlearn's difference and ratio for each slicing.

### Mitigation and trade-offs

![](figures/bias_mitigation.png)

*Each candidate fix for the owner rule scored four ways, where the 730-day lookback (red) wins in-sample but not held out.*

A lower share threshold changes nothing. Falling back to the last toucher owns 65% more files but is wrong more often. Taking a person's team from the last 730 days instead of 180 looks best: 0.496 → 0.545 on next authors with a team, and unassigned 24.7% → 16.0%. So `bias.py` checks the pick on held-out data. It picks at a cut one window earlier (2025-10-06), then confirms on the current cut (2026-04-04) with a seeded paired bootstrap, which resamples the same files with a fixed seed. At the earlier cut the configured 180-day rule wins (0.505 against 0.502 on 1,279 files), so the held-out delta is 0.0 (interval 0.0–0.0). The gain is in-sample only, and we didn't adopt the longer lookback.

What we applied now: every slice in `bias.json` carries its N, interval and flag, so the views can show "low coverage" next to a flagged slice and "too few to judge" under N = 30. For ticket mapping we ruled out fuzzy matching, so the fix is more coverage: add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot. `tests/test_bias.py` (9 tests, passed) pins the flag rule, the N = 30 edge, multi-component tickets, the prior-commits slice and the held-out scoring.
