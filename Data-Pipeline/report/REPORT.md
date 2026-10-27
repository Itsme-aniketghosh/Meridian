# Meridian data pipeline

Team run `c5118b019d642a9b`: Django, 20 Django packages, Spark with its Apache Jira tickets, and GitLab issues, 1,788,375 rows in 25 tables. The sections follow the assignment's guidelines in order. Appendices A to D hold the evidence.

## 1. Overview

Meridian does the cross-codebase review an engineer would do. It joins a company's code, git history and tickets to show every call to an API, who owns it, how risky it is, which teams block each other, and which tickets have drifted from the code. It starts with the hardest case, retiring an internal API across many teams, and is step one toward self-maintaining APIs: software that updates its own callers when an API changes. When a platform team removes something like `AuthClient.verify_token`, someone has to find every call to it, work out which team owns each one, and spot the calls that are risky or that block another team. Today people do that by hand, with Jira tickets that drift away from the code. Meridian works it out from the code, git history and tickets ([docs/02-product.md](../../docs/02-product.md)).

This pipeline builds the data behind it: one SQLite file (the "block") that every view and check reads. It's two Airflow DAGs, a daily update and a weekly rebuild from scratch that checks the update:

```
meridian_update (click, daily 02:00 UTC)
anything_new ── collect ──┬── history ───────── owners ──┐
                          │                              ├── join ── graph ── validate ── report ── publish
                          └── scan ──── resolve ─────────┘

meridian_rebuild (Sunday 04:00 UTC): fresh ── collect ── … ── validate ──┬── report ── compare ── publish (only if they differ)
                                                                        ├── bias (never gates)
                                                                        └── gx (never gates)
```

There's no trained model at this stage. The pipeline's "models" are three rules (who owns a file, which ticket maps to which code, which name really is the retired API), and we test and slice them the way you would a model.

## 2. Key components

### 2.1 Data acquisition

`meridian/collect.py` fetches everything and freezes it. It clones 22 public repos at pinned commits (`meridian.toml`), pulls Spark's tickets from the Apache Jira REST API and GitLab's issues from the GitLab API, and keeps the raw responses. The run id is a hash of the manifest of everything it read, so the same inputs give the same run id on any machine.

Dependencies are pinned in `requirements.txt`, and the GX audit has its own `requirements-gx.txt`. The raw ticket pulls contain real names, so DVC tracks them instead of git (`snapshots/_cache/*.dvc`, §2.5). Without team access, `python run_demo.py download --public-pull` makes a run of your own from the public APIs, with different numbers.

### 2.2 Data preprocessing

Each step is its own module, run as `python -m meridian.<part> --run-id <id> --config meridian.toml`. Every setting that changes a row lives in `meridian.toml`, so adjusting a rule means editing one file. [CONTRACTS.md](../CONTRACTS.md) lists what each part reads and writes.

| Step | What it does |
|---|---|
| `history` | Commits and changed files from git, tickets as they were at the snapshot, commit-to-ticket links from ticket keys, and SZZ (tracing each bug fix back to the commit that caused it) |
| `scan` | tree-sitter finds every place a tracked name shows up, with its role (import, call, alias) and the function it's in |
| `resolve` | Jedi checks what each name really refers to, so a look-alike `_()` or `url()` gets dropped |
| `owners` | Merges a person's emails into one identity (bots and shared addresses aren't people), gives people teams from Jira components, and gives each file an owner team |
| `join` | The features: call sites with owner, bug-fix count, status and a stable id, tickets mapped to code, and coverage |
| `graph` | Which call site waits on which, how far each call reaches (blast radius), and ticket chains |

Against answer keys: on Django's `ugettext*` the block finds all 265 lines in a hand-checked key with 0 false hits and resolves 542 of 542 aliased `_()` calls. On Spark, 61% of 59,491 tickets map to code, and a person found 49 of 50 random commit-to-ticket links right ([Appendix C](appendix-real-data.md)).

#### Cross-team blocks use the same machinery

A block between teams is the same problem as one between people. An owner team is an aggregate of the people who change a file: `owners` gives each person a team (in Spark, the Jira component of their keyed commits) and sums commit shares per team. So who touches what, who calls whose code and which ticket maps to which code work the same for either unit. The real data is also multi-team in practice. On v1's block (`70dd1dabfed544b8`, the same git snapshot and tickets as the team run):

| | Spark | Django |
|---|---|---|
| First-parent commits whose committer isn't the author | 33,148 of 44,567 (74%): 122 committers merge the work of 2,954 authors | 3,682 of 22,445 (16%) |
| Files per commit: median, 90th percentile, share over 10 | 2, 11, 10.2% | 1, 6, 4.5% |
| Owned files with authors from 2+ teams in the 180-day window | 1,268 of 5,708 (22%) | path-prefix teams only |
| Distinct authors per call-site file, all history | no call sites | median 13; 94% of 116 files have 2+ |

And 10,647 of 59,491 Spark tickets (18%) name more than one component. In Spark a committer's name usually stands for someone else's work, and a fifth of owned files mix teams. Django's committer gap is small, so that point holds for Spark only. The limit stays: no public dataset has both a tracker and tracked code, so on real data 0 tickets map to a call site and the block and chain tables are empty. The hand-built repo `acme/shop` runs the cross-team story end to end, and all 43 product queries pass on it.

### 2.3 Test modules

340 unit tests (`unittest`), one test file per part, all passing. On a plain Windows machine 19 are skipped: 18 need Airflow, GX or the team's private data, and 1 runs on Mac and Linux only. Those run in their own CI jobs or with the team data. The fixtures carry the edge cases: renamed and split files, files that don't parse, missing values, schema violations, corrupt JSON, broken alert channels, a repo added to or dropped from the config.

```
python -m unittest discover -s tests -t .        # from Data-Pipeline/
```

CI runs this on Linux and Windows on every push, plus an Airflow job and a GX job ([Appendix B §1](appendix-quality.md#1-tests), [TESTING.md](../../TESTING.md)).

### 2.4 Pipeline orchestration (Airflow DAGs)

Both DAGs come from one spec, `dags/pipeline_spec.py`, and `test_dag` checks them against it. They cover the whole workflow: `collect` downloads, each later task is one of the steps above, and `publish` writes the final block. `anything_new` stops the update in about 2 s when nothing changed since the last publish, and `publish` only runs after `validate` passes, so a bad run never replaces the last good block.

We ran both on the real data in Docker on a Mac. The update passed every task on the first try in 943 s. The rebuild recomputed everything with caches off in 902 s, and compare found 0 differences from the update ([Appendix A](appendix-airflow.md)).

### 2.5 Data versioning with DVC

`dvc.yaml` has one stage per part, with the code, settings and tables each one depends on. `dvc.lock` is in git, so every commit pins the exact data its code produced, and `dvc repro` reruns only the stages whose inputs changed. The raw Jira and GitLab pulls are pinned with `.dvc` files.

The run_id hashes the inputs, so changing `meridian.toml` means a new team run. On Oct 27 v1, `70dd1dabfed544b8`, became `c5118b019d642a9b`: the owner surname rule, `donotreply` as a shared address, a third join match step and no SQLite pin. Code fixes (SZZ skips reverts, stack-trace mapping) changed outputs too. The snapshot is the same. People went from 8,378 to 8,442, bug-inducing rows from 26,559 to 26,302 and mapped Spark tickets from 36,138 to 36,198, while the call-site tables are byte for byte the same apart from the run_id. `golden/70dd1dabfed544b8.json` stays as the v1 record.

The remote is still a placeholder GCS bucket, so `dvc pull` doesn't work yet; the team shares the raw data as a checksummed zip.

### 2.6 Tracking and logging

Every part logs counts as it goes through Python's `logging`, and Airflow keeps one log file per task try, in the UI and in `logs/`. A failed task or a data anomaly sends an alert (§2.8). `report` writes what changed since the last published run and which commit caused it, and `status` prints how stale the data is, for example "data 2 days old · 14 new commits".

### 2.7 Data schema and statistics generation

`schema.sql` defines every table: columns, types, keys and allowed values, and the writer refuses any row that breaks it. `validate` computes statistics per column (rows, missing, distinct values, min, max, mean, common values) into `stats.json`, and the next run compares against them, so quality is checked over time and not just once.

Every Sunday a Great Expectations suite re-checks the block on its own and publishes Data Docs. On `c5118b019d642a9b` it ran 308 expectations and none failed. It never blocks a run; `validate` is the gate ([Appendix B §2 and §4](appendix-quality.md#2-schema-and-statistics)).

### 2.8 Anomaly detection and alerts

`validate` sorts problems into two kinds, with thresholds in `settings.toml [validate]`:

- Errors fail the task and stop the publish: too few rows, bad formats (UTC times, ids, SHAs, paths, ticket keys), numbers out of range, coverage below its floor, schema violations.
- Warnings send an alert and the run goes on: too many missing values, impossible dates, outliers past the IQR fences, a row count that drifted from the last run, a column that collapsed to a single value.

On `c5118b019d642a9b` there are 0 errors and 1 warning. 289 Spark tickets are dated 0010 to 0012, as Apache Jira has them from its 2013 import. A date before 2000 or after the snapshot is invalid, so every run warns with the count, the range and example keys, and no part uses those dates as dates. The run goes on, because they're the source's own data.

`alert.py` sends one message to Slack or email per failed task, data error or data warning. A test and the demo's step 8 send a real alert, an HTTP POST and an SMTP session, to local Slack-style and SMTP servers and check what arrived ([Appendix B §3](appendix-quality.md#3-anomalies)). The team's own channel is a webhook URL in `.env` (`MERIDIAN_SLACK_WEBHOOK`), and it isn't connected yet.

### 2.9 Pipeline flow optimization

The Gantt chart of the team run's update shows where the time goes. Run one after another, the tasks add up to 1,183 s. The DAG took 943 s, because `scan` and `resolve` run beside `history`, which saves 20%.

`history` is the bottleneck: it walks all of Spark's git history for SZZ. It already runs one process per CPU, and its cache brings a warm run from 312 s down to 74 s ([Appendix D](appendix-testing.md)). Most of what's left is Docker's folder mount on a Mac, which makes git about three times slower than native. A Docker volume or a Linux host fixes that ([Appendix A](appendix-airflow.md)).

## 3. Data bias detection using data slicing

### 3.1 Detecting bias

We don't have demographic data, and we don't want it. What we can check is whether the pipeline serves some teams worse than others, since a team the rules get wrong is sent the wrong work or looks idle when it isn't. So we check three rules:

- the owner rule (does a file's owner team predict who edits it next?)
- the ticket mapping (does a ticket reach the code it's about?)
- the resolver (does it keep the real calls in every repo?)

### 3.2 Data slicing for bias analysis

`meridian/bias.py` slices each rule with Fairlearn's `MetricFrame`: by module, team, Jira component, ticket type, year filed and repo. The one people-level slice is how many commits the next author had made before. We don't slice by email domain or any other personal detail.

A slice counts when it has at least 30 rows. It's flagged when its whole 95% Wilson interval (a confidence range for a rate) sits more than 0.10 below the overall value. 133 slices were judged and 29 flagged:

- The owner rule is right 49.6% of the time where it can be scored. A quarter of next authors have no team (none of their commits in the last 180 days names a ticket with a Jira component), so the rule can't score them. That's a coverage gap, not a skew.
- Ticket mapping depends mostly on age. Tickets from before 2014 point at files that are gone by our pinned commit. Kubernetes and Spark Docker stay low even on recent tickets, because their code lives in other Apache repos we didn't snapshot.
- The resolver treats every repo the same: 98.1% kept overall, no repo flagged.

### 3.3 Mitigation of bias

We scored each fix on the same files, then checked the best one on held-out data.

- Lowering the owner threshold (min_share 0.30 to 0.20 or 0.10) changed nothing: files are unowned because their authors have no team, not because shares sit near the line.
- Falling back to the last person who touched the file owns 65% more files but is wrong more often (0.395), so we didn't take it.
- Taking a person's team from their last 730 days instead of 180 looked best: accuracy 0.496 to 0.545, and unassigned authors 24.7% down to 16.0%. But picked on an earlier cut and checked on the current one, it lost to the current rule (0.502 vs 0.505). So we didn't adopt it.

What we shipped: every slice in `bias.json` carries its size, interval and flag, so the views can show "low coverage" next to a flagged team or module and an unmapped team doesn't look idle. For ticket mapping the fix is more data, not fuzzy matching: add the two missing Apache repos in the next team run.

### 3.4 Documenting bias mitigation

[BIAS.md](../BIAS.md) has every slice, the mitigation table and the held-out check, and [Appendix B §5](appendix-quality.md#5-bias-data-slicing) has the charts. The trade-offs:

- The longer lookback gives more people a team, but a two-year-old team can be wrong for someone who moved.
- The flags aren't corrected for testing 133 slices at once. Under a Bonferroni check 23 of the 29 still hold.
- `tests/test_bias.py` pins the slicing and flag rules on a synthetic history.

## 4. Additional guidelines

### 4.1 Folder structure

```
Data-Pipeline/
├── dags/          the two Airflow DAGs and the spec they're built from
├── meridian/      the pipeline code, one module per part (the guidelines' scripts/)
├── tests/         unit tests, one file per part
├── logs/          Airflow task logs (git-ignored)
├── golden/        frozen hashes the reproducibility test checks
├── labels/        hand-checked answer keys
├── report/        this report, charts and the scripts that draw them
├── notes/         planning notes
├── dvc.yaml, dvc.lock, schema.sql, meridian.toml, settings.toml
├── Dockerfile, docker-compose.yaml, requirements.txt
└── README.md
```

The data (the guidelines' `data/`) sits at the repo root: `snapshots/` for inputs, `out/` for outputs and `published/` for the live block. It's git-ignored and versioned by DVC, because it's several GB and has real names in it.

### 4.2 README documentation

[Data-Pipeline/README.md](../README.md) covers setting up the environment, running the pipeline with and without Airflow, what each folder and module does, and DVC. It also has a "Demo for TAs" section.

### 4.3 Reproducibility

The README's steps set up and run the pipeline on another machine. The pipeline never reads the clock after `collect`: ids are content hashes, output is sorted, and every setting that changes a row is hashed into the run id. So the same snapshot gives the same bytes:

- The 24 output files of `c5118b019d642a9b` match their frozen hashes (`tests/test_golden.py`), and collect rebuilds the same run_id from the pinned SHAs, ticket pulls and `meridian.toml`.
- The weekly rebuild matched the update byte for byte.
- CI checks a small fixture's hashes on Linux and Windows on every push.

**One file for a TA: [reproduce.py](../reproduce.py).** It needs only Python 3.11+ and git. Pick one option.

**Option 1: quick check (about 2 minutes, no data needed).** Download the script into an empty folder and run it. It clones the repo, makes a virtual environment from `requirements.txt`, runs every unit test and the fixture demo, and prints a PASS/FAIL summary.

```
curl -O https://raw.githubusercontent.com/Itsme-aniketghosh/Meridian/main/Data-Pipeline/reproduce.py
python3 reproduce.py
```

**Option 2: the real team run (about 30 minutes more, 6 GB of disk).** The only input that isn't on GitHub is the raw Jira and GitLab ticket pulls of Oct 1, which a fresh pull today can't give again. They're one zip, `meridian-raw-973531f7fd4cfd5e.zip` (145 MB), on **[Google Drive](https://drive.google.com/file/d/1jq8gMWAQpmECNXrX2Su3I-xw7bwvu0tL/view?usp=drive_link)**.

1. Clone the repo.
2. Download the zip from the Drive link into `Meridian/Data-Pipeline/shared-snapshot/`. Keep its name, and don't unzip it: the script does.
3. Run the script from the folder you cloned into.

```
git clone https://github.com/Itsme-aniketghosh/Meridian.git
# download the zip from the Drive link into Meridian/Data-Pipeline/shared-snapshot/
python3 Meridian/Data-Pipeline/reproduce.py
```

That's all the TA does by hand. The script finds the zip, checks its sha256 and unzips it into `snapshots/`. Then it clones the 22 code repos from GitHub at their pinned SHAs, runs every task in the order the DAG does, and prints a PASS/FAIL summary. It checks that collect gave `run_id=c5118b019d642a9b` and that the 24 outputs match `golden/c5118b019d642a9b.json`. Run natively, `commit_file` can differ by git version, and the script says so if it does. The Airflow run in Docker is in the [README](../README.md).

Underneath, `python run_demo.py short` runs in about 10 seconds with no private data and no Docker, one guideline item at a time on the fixture. `python run_demo.py long` runs everything, Airflow included. Both check free RAM and disk before heavy steps and skip with a reason rather than crash.

### 4.4 Code style

One module per part, each with a docstring saying what it reads and writes. flake8 (PEP 8, lines up to 120 characters, set in `setup.cfg`) is clean and runs in CI.

### 4.5 Error handling and logging

- A part that can't go on stops with the reason and a non-zero exit code: a missing input, a snapshot not at its pinned commit, a bad row, a corrupt file.
- Airflow times the task out, retries it twice five minutes apart, then sends an alert.
- Parts are idempotent, so a retry is safe. `publish` refuses a partial run and refuses to roll the live block back to an older run.
- A trigger's run id is checked before it reaches any shell command.

We tested this by breaking things on purpose: 27 injected faults and 42 scenario tests. [Appendix D](appendix-testing.md) lists what broke and what we fixed.

## 5. Evaluation criteria

| # | Criterion | Where | Status |
|---|---|---|---|
| 1 | Proper documentation | §4.1, §4.2, [README](../README.md) | ✅ |
| 2 | Modular syntax and code | §2.2, §4.4 | ✅ |
| 3 | Pipeline orchestration (Airflow DAGs) | §2.4, [A](appendix-airflow.md) | ✅ |
| 4 | Tracking and logging | §2.6, §4.5 | ✅ Python logging in every part, a log per task try in Airflow, and alerts that reach the team's Slack |
| 5 | Data version control (DVC) | §2.5 | 🟡 Partial: `dvc.yaml` and `dvc.lock` pin every stage, but the remote is a placeholder, so `dvc pull` doesn't work. The team zip and `reproduce.py` stand in for it (§4.3) |
| 6 | Pipeline flow optimization | §2.9, [A](appendix-airflow.md) | ✅ |
| 7 | Schema and statistics generation | §2.7, [B](appendix-quality.md) | ✅ |
| 8 | Anomaly detection and alert generation | §2.8, [B](appendix-quality.md) | ✅ Detection is live and tested; alerts reach the team's Slack ([B §3](appendix-quality.md#3-anomalies), with a screenshot). Email is tested against a local SMTP server |
| 9 | Bias detection and mitigation | §3, [BIAS.md](../BIAS.md) | ✅ Detection on 133 slices. Five mitigations measured, none adopted; flags ship instead (§3.3) |
| 10 | Test modules | §2.3, [B](appendix-quality.md) | ✅ |
| 11 | Reproducibility | §4.3, [D](appendix-testing.md) | ✅ Same run_id and same bytes from the pinned inputs; `reproduce.py` checks both |
| 12 | Error handling and logging | §4.5, [D](appendix-testing.md) | ✅ |

What still falls short:

- Alerts have reached only local test servers: the team's Slack webhook isn't set in `.env` yet. The DVC remote is a placeholder.
- The cross-team features only show their full value on the fixture, because no public dataset has both tickets and tracked code (§2.2). With one run there's no burndown yet either.
- Two weaker signals missed bars we wrote down first ([notes/PASS-BARS-2026-10-26.md](../notes/PASS-BARS-2026-10-26.md)): a "bug with no reproduction" check, and timing in the GitLab cross-team rule (precision 0.25 against 0.50 by model labels; 2 of 10 on the 50 model-drafted, person-reviewed rows).

## Appendices

- [A. Airflow run and Gantt](appendix-airflow.md)
- [B. Data quality and bias](appendix-quality.md): tests, schema, statistics, anomalies, GX, bias
- [C. The real data, feature by feature](appendix-real-data.md)
- [D. Testing the pipeline against itself](appendix-testing.md)
