# Appendix E: red team

Before handing this in, we attacked our own pipeline and this report. Nine reviewers each took one angle and worked independently, read-only. Then we fixed what they found. Every fix has a regression test unless it is a doc or config change.

| Angle | What they did | Found | Fixed |
|---|---|---|---|
| Strict grader | Scored the 12 criteria in the course guidelines, 0 to 10 each | About 82 of 120. Points lost on things a grader can't see: no Gantt, no alert ever sent, GX never run, no public way to run end to end, report not in git | Report committed, Airflow evidence (Appendix A), TA demo with a no-team-data path, `data/` and `scripts/` mapped in the README |
| Fact-check | Checked every number in the report against the run files and the code | 18 errors, e.g. "5% of commits keyed" (really 82%; the 5% is tickets with a link), overlapping mapping counts, a "halts" that is really "alerts and publishes the rebuild" | All corrected |
| Clean clone | Followed the README as a stranger with no team data | Nothing ran end to end without the private zip; a missing `out/` gave a raw sqlite traceback; `dvc.yaml` missed deps; no `.dockerignore` | `run_demo.py short` runs on the fixture; `--public-pull` documented; clear error; deps added; `.dockerignore` |
| Failure injection | 27 injected faults on a fixture run (missing, empty, corrupt, wrong-typed tables, bad webhooks, unwritable disk) | A failed block could publish (stale `anomalies.json`); a bad webhook URL killed alerting; a missing table loaded as empty; raw tracebacks on corrupt files | All fixed, each with a test |
| Data quality | Injected anomalies into a copy of the real block | Missed: impossible dates, `..` paths, bad ticket keys, one huge outlier, a column collapsing to one value. Alert spam from 289 known bad dates and small-table drift | New format, range and row rules (0 false errors on the real block), constant-column check, known-anomaly allowance |
| Bias | Recomputed slices with confidence intervals | One false claim (EC2 and Mesos "clear"; really too few tickets to judge); the 0.000 for people with no team is true by construction; mitigation picked and scored on the same data; no intervals | Wilson intervals, flag only when the whole interval is below the line, held-out check, accuracy without the circular class, a people-level slice |
| DAG and DVC | Compared every task's reads with its declared dependencies; looked for races | A forced rerun dropped its history; the weekly rebuild could switch runs mid-way or roll the live block back; `dvc commit` recorded the wrong run; daily runs skipped ticket refreshes every other day | All fixed; a template-rendering test for the DAG |
| Privacy | Secrets in 139 commits, personal data, licences | No secrets anywhere. One GitLab handle the scrubber missed; a raw-data Drive link in a public file; no licence | Scrubber fixed, link removed, LICENSE and a data-licences section |
| Code quality | flake8, unused imports, untested paths | No lint config, ~260 issues at 120 columns, `print` instead of logging, untested corrupt-JSON and HTTP-retry paths | flake8 in CI, imports and lines fixed, Python logging, new tests |

The knowledge-base queries also had four bugs, found while running the product's situations on real data (Appendix C): GitLab's lowercase `closed` read as open, queries mixing datasets, blast radius double-counting at depth 3, and a stall rule firing for teams with no call sites. All four are fixed and covered by a regression test.

## Still open

- Bias: the flag rule isn't corrected for testing ~125 slices at once. Under Bonferroni a few flags (`core/`, `common/`, Deploy) don't hold, and BIAS.md says so.
- The 730-day lookback for a person's team is validated but not applied: it changes `meridian.toml` and so needs a new team run.
- The old deleted CSV with public GitLab usernames stays in git history (team decision: public data).
- P1's precision on the 21-commit replay is 0.36, under the 0.80 bar, because the push check has no resolver at push time.
- No real Slack or email alert has gone out: no channel is configured. The alert path is tested with mocks and logs every alert.
