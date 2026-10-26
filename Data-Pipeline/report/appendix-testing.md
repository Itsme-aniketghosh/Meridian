# Appendix D: Testing the pipeline against itself

Unit tests check each part on its own fixture ([Appendix B §1](appendix-quality.md#1-tests)). Before handing in, we also tested the whole pipeline in two ways.

- Red team: nine reviewers each took one angle and worked independently, read-only, on the pipeline and this report. Then we fixed what they found.
- Scenario tests: 42 situations the pipeline will meet, from new pushes and renames to a tampered table and Docker, each answer written down before the run. Rounds 1–4 were each run by an independent agent in its own copy of the code and data; round 5 is the Mac runs in [Appendix A](appendix-airflow.md).

Of the 42 scenarios, 24 passed first time. 13 found a bug, now fixed, tested and passing. 4 keep the data right but credit the change report to an older commit (still open). 1, the golden hashes under Docker's git, was a machine difference. Every fix has a regression test unless it is a doc or config change.

## What we tested, found and fixed

| What we tested (red team first, then the scenario rounds) | How | What we found | What we fixed |
|---|---|---|---|
| Strict grader | Scored the 12 criteria in the course guidelines, 0 to 10 each | About 82 of 120, lost on things a grader can't see: no Gantt, no alert ever sent, GX never run, no public way to run end to end, report not in git | Report committed, Airflow evidence ([Appendix A](appendix-airflow.md)), a TA demo with a no-team-data path, `data/` and `scripts/` mapped in the README |
| Fact-check | Every number in the report against the run files and the code | 18 errors, e.g. "5% of commits keyed" (really 82%; the 5% is tickets with a link), overlapping mapping counts, a "halts" that is really "alerts and publishes the rebuild" | All corrected |
| Clean clone | Followed the README as a stranger with no team data | Nothing ran end to end without the private zip; a missing `out/` gave a raw sqlite traceback; `dvc.yaml` missed deps; no `.dockerignore` | `run_demo.py short` runs on the fixture; `--public-pull` documented; a clear error; deps added; `.dockerignore` |
| Failure injection | 27 faults on a fixture run: missing, empty, corrupt and wrong-typed tables, bad webhooks, an unwritable disk | A failed block could publish (stale `anomalies.json`); a bad webhook URL killed alerting; a missing table loaded as empty; raw tracebacks on corrupt files | All fixed, each with a test |
| Data quality | Injected anomalies into a copy of the real block | Missed impossible dates, `..` paths, bad ticket keys, one huge outlier, a column collapsing to one value. Alert spam from 289 known bad dates and small-table drift | New format, range and row rules (0 false errors on the real block), a constant-column check, a known-anomaly allowance |
| Bias | Recomputed the slices with confidence intervals | One false claim (EC2 and Mesos "clear", really too few to judge); the 0.000 for people with no team is true by construction; mitigation picked and scored on the same data; no intervals | Wilson intervals, flag only when the whole interval is below the line, a held-out check, accuracy without the circular class, a people-level slice |
| DAG and DVC | Every task's reads against its declared dependencies; races | A forced rerun dropped its history; the weekly rebuild could switch runs midway or roll the live block back; `dvc commit` recorded the wrong run; daily runs skipped ticket refreshes every other day | All fixed, plus a template-rendering test for the DAG |
| Privacy | Secrets in 139 commits, personal data, licences | No secrets. One GitLab handle the scrubber missed; a raw-data Drive link in a public file; no licence | Scrubber fixed, link removed, LICENSE and a data-licences section |
| Code quality | flake8, unused imports, untested paths | No lint config, about 260 issues at 120 columns, `print` instead of logging, untested corrupt-JSON and HTTP-retry paths | flake8 in CI, imports and lines fixed, Python logging, new tests |
| Round 1: the update DAG (8 scenarios) | `airflow dags test meridian_update` on the fixture `acme/shop`, Airflow 2.10.5 | 6 passed, among them a click with nothing new (exit 99, nothing downstream runs) and the same parts run twice (byte-identical, all 29 output files). 2 bugs: a push stayed invisible for 24 h because `status` didn't fetch; the gate ignored a forced run | `status` fetches a followed branch first (`[status] fetch`), and a rerun through Airflow saw the push. A trigger naming a run skips `anything_new` |
| Round 2: matching and per-commit attribution (11) | One fixture run, nine single-purpose commits (C1–C9), a second run, `changes.json` and `call_site` checked commit by commit | 6 passed, among them a deleted call (C2), a duplicated line (C6) and a split file (C7). C3 (lines inserted above), C4 (a function moved), C5 (a rename) and C9 (a call in a shared helper) keep the right IDs but credit an older commit. 1 bug: C8's broken file had its import site matched to an identical line in another file, because the fallback ran before the parse-failure check | Fixed in `join.match`, with a test that fails on the old code |
| Round 3: rebuild, compare and the publish gate (10) | The fixture, with tampering and a failing validation | 4 passed: a tampered `call_site.jsonl` (compare exits 3, alerts, names the rows, publishes the rebuild), a failing validate (publish never runs), caches on vs off (0 differences), and a rebuild of an older run while a newer one is live (refused). 6 bugs: the rebuild took a new clock time and branch tip; the next update carried a corrected run's bad rows forward; bias crashed on a repo with no Jira tickets; the cleanup's `ln -sfn` linked inside last week's folder; a rebuild folder set to the main one would `rm -rf` the live outputs; an update publishing mid-rebuild switched the rebuild's run | collect replays an existing run's SHAs and snapshot_at (0 differences). Publishing a rebuild copies its `out/` over the update's. bias handles an empty frame. The link is removed first. The DAG refuses that folder (`test_fresh_refuses_the_main_data_root`). `fresh` names the run once and the rest read it through xcom |
| Round 4: the real team run (6) | Both DAGs on a copy of 70dd (1,788,242 rows), the golden hashes as the answer key | All 6 passed: the update on a fresh data root (24 of 24 golden, 0 errors, 1 known warning); a second click (stops in about 2 s); the rebuild (0 differences); caches cold then warm (24 of 24 both times, history 312 s → 74 s); all tests with the team data; the GX audit (the same finding as validate, the 289 dates) | |
| Round 5: other machines (7) | Windows, and Docker on a Mac on the real data | 2 passed: the update and the rebuild in Docker ([Appendix A](appendix-airflow.md)). 4 bugs: Windows found 5 extra Django edges, as Jedi walked folders in the file system's order (NTFS by name, APFS by hash); CRLF changed all 13 code hashes; in Docker, history and resolve failed in 1 s because checkouts pointed at their mirror by an absolute host path; a non-default `AIRFLOW_UID` kept Airflow from starting. 1 machine difference: golden 23 of 24 under Docker's git (`commit_file`, 9 of 849,643 rows, git 2.50 vs 2.39 rename detection) | One folder order on every OS. `.gitattributes` (LF everywhere), `dvc.lock` regenerated. A relative link, and collect repairs old snapshots (`test_checkouts_work_wherever_the_data_root_is_mounted`). Init runs through `/entrypoint`. The golden is defined under the pipeline image (CONTRACTS.md), so `commit_file` was recorded from the Docker run: 24 of 24 |
| Product queries on real data | The knowledge base's named queries on 70dd ([Appendix C](appendix-real-data.md)) | 4 bugs: `ticket_open` read GitLab's lowercase `closed` as open; the queries ignored `asof.repo` and `asof.symbol` and mixed datasets; `derive_blast_radius` counted a function once per depth (+25 at depth 3 for `ugettext`) and hard-coded the symbol; `stall_competing_load` fired for 8 Spark teams that own no call sites | Done statuses compared case-insensitively in `ticket_open`, `stall_ticket_mismatch` and graph.py's collides rule: GitLab open issues by status go 14,524 → 3,680, and the fixed view, which also needs a call-site mapping, returns 0 on 70dd. Queries scoped to `asof.repo`/`asof.symbol` (NULL = all): django/django + `ugettext` gives 93 sites, not 1,526 from 25 repos. Blast radius 209/241/135, as graph's table. Competing load counts only teams owning call sites: 8 → 0 |

The fixture scenarios of rounds 1–3 are scripts in scratch runs, not in the repo; each bug they found has a regression test in `tests/`.

## What the bugs had in common

- Paths and order that depend on the machine: the absolute alternates path, Jedi's folder order, CRLF, git's rename detection. Each is invisible on the machine that made the data. The fixes make the data depend only on its inputs.
- A check that read stale state: status without a fetch, the gate without the trigger, the rebuild re-reading `latest.json`, the cleanup linking into last week's folder.
- A fallback that was too eager: a broken file's sites taking another file's identical line.

## Still open

- The change report credits a moved, shifted or renamed line to the commit that last wrote its text (C3, C5), since `effective_sha` comes from blame. The data is consistent, but "what this push changed" reads as an older commit.
- Edges added by a call in a shared helper are credited to the callers' last commits (C9), and a function moved within a file shows its edge as removed and added (C4). Per-push blast radius belongs to the push check on PRs.
- `receiver_from` is never set on real data, so `blocked_by`, P4, `consumer_blocked` and `depends_on` run on fixtures only. That needs a tracked method symbol.
- The bias flags aren't corrected for testing 136 slices. Under Bonferroni 26 of the 32 hold; 6 (`core/`, `common/`, Deploy twice, GraphX, Spark Shell) don't.
- P1's precision on the 21-commit replay is 0.36, under the 0.80 bar: no resolver at push time.
- No real Slack or email alert has gone out, since no channel is configured.
- An old deleted CSV with public GitLab usernames stays in git history (team decision: public data).

## Rerun it

```
python -m unittest discover -s tests -t .                                   # 287 tests, from Data-Pipeline/
MERIDIAN_RUN_ID=70dd1dabfed544b8 python -m unittest tests.test_golden -v    # the golden check, with the team data
python run_demo.py short                                                    # the fixture end to end, no data, no Docker
```
