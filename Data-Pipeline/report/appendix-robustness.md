# Appendix F: scenario tests · does the pipeline hold up?

Unit tests check each part on its own fixture. This appendix is about the whole pipeline. We ran it under the situations it will meet: new pushes, nothing new, renames, broken files, a tampered table, a failing validation, another machine, another git, Docker. Each scenario's answer was written down **before** the run, then compared with what happened.

The scenarios were run in five rounds:

| Round | Where | How |
|---|---|---|
| 1. Update DAG | a fixture repo (`acme/shop`, a stub `django.utils.translation`), Airflow 2.10.5 | `airflow dags test meridian_update`, one scenario per run |
| 2. Matching and attribution | the same fixture: one run, nine single-purpose commits, a second run | the update DAG, then `changes.json` and `call_site` checked commit by commit |
| 3. Rebuild, compare, publish | the fixture, with tampering and a failing validation | `meridian_rebuild` and the publish gate |
| 4. The real team run | a copy of run `70dd1dabfed544b8` (1,788,242 rows) | both DAGs on the real data, the golden hashes as the answer key |
| 5. Docker on a Mac | the real data, `docker compose up`, LocalExecutor | the handover's runs: [Appendix A](appendix-airflow.md) |

Each of rounds 1–4 was run by an independent agent, read-only on the repo, in its own copy of the code and data. A failure became a fix with a regression test, and the scenario was run again.

**Result: 42 scenarios.** 24 passed first time. 13 found a bug: each is fixed, has a regression test, and passes now. 4 keep the data right but credit the change report to an older commit (still open, below). 1, the golden hashes under Docker's git, was a machine difference, settled by recording the hash from the pipeline image.

## 1. The update DAG (fixture)

| Scenario | Expected | Result |
|---|---|---|
| First run on an empty data root | 5 call sites (2 imports, 3 calls), each `effective_sha` = the commit that wrote it, published, `changes.json` = +5 under 2 commits | ✅ exactly that |
| A click with nothing new | `anything_new` exits 99, nothing downstream runs, `latest.json` unchanged | ✅ |
| **A push, then a click** | a new run_id, one new call site credited to the push's commit | ❌ → ✅ The push was invisible for 24 h: `status` counted commits in the local mirror without fetching. **Fixed:** status fetches a followed branch first (`[status] fetch`). Rerun through Airflow: seen, new run, credited to its commit |
| A push that removes a call | a `removed` version, dated and credited to the removing commit | ✅ |
| **A forced run** (`{"run_id": …}` or `{"force": true}`) | runs even with nothing new | ❌ → ✅ The gate ignored the trigger. **Fixed:** a trigger naming a run skips `anything_new` |
| The same run's parts run twice | byte-identical, all 29 output files | ✅ |
| A run after a run | snapshot_at moves forward, run_id changes, prev_run = the run published before | ✅ |
| A forced rerun of the published run | keeps its own prev_run, never matched against itself | ✅ red team fix; checked on the rendered DAG templates (`test_templates_name_one_run_and_never_it_as_its_prev`), not in a live Airflow run |

## 2. Matching and per-commit attribution (fixture, nine commits between two runs)

| Commit | Expected | Result |
|---|---|---|
| C1 adds a call | a new site and edge, `effective_sha` / `introduced_sha` = C1 | ✅ |
| C2 deletes a call | `removed`, `effective_sha` = C2 | ✅ |
| C3 inserts lines above calls | same call_site_id, line moved, a new version | ✅ ID · 🟡 credited to the line's original commit, not C3 |
| C4 moves a function below another | same call_site_id | ✅ ID · 🟡 its edge shows as removed and added |
| C5 renames a file | same IDs, new path | ✅ ID · 🟡 credited to the original commit |
| C6 duplicates an identical call line | the original keeps its ID, the copy gets a new one, credited to C6 | ✅ |
| C7 splits a file | the moved site keeps its ID at its new path | ✅ |
| **C8 breaks a file (syntax error)** | its sites stay present, nothing removed | ❌ → ✅ Its import site was **matched to an identical line in another file**: the fallback match ran before the parse-failure check. **Fixed** in `join.match`, with a test that fails on the old code |
| C9 adds a call in a shared helper | blast radius grows; the new site and edges under C9 | ✅ site and depth arithmetic · 🟡 the callers' edges are credited to the callers' last commits |
| A third run with no change | 0 new versions, empty `changes.json` | ✅ |
| The second run, again | byte-identical outputs and report | ✅ |

## 3. Rebuild, compare and the publish gate (fixture)

| Scenario | Expected | Result |
|---|---|---|
| **Weekly rebuild after the branch moved** | the rebuild replays the published run, 0 differences | ❌ → ✅ collect took a new clock time and the branch's new tip: "inputs now give" another run. **Fixed:** collect asked for an existing run replays its SHAs and snapshot_at. Rerun: 0 differences, nothing republished |
| A tampered `call_site.jsonl` in the main root | compare exits 3, alerts, names the table, the part (`join`) and the rows, and publishes the rebuild | ✅ |
| **The next update after that correction** | builds on the corrected run | ❌ → ✅ The rebuild was published but the main `out/` kept the bad rows, so the next update carried them forward. **Fixed** (red team): publishing a rebuild copies its `out/` over the update's |
| validate fails (coverage floor above 1) | validate exits non-zero, publish never runs, the last good block stays live, an alert | ✅ |
| Caches on for updates, off for the rebuild | 0 differences; an imported module's change re-resolves its importers | ✅ "7 of 9 reused, 2 resolved", 0 differences |
| **A repo with no Jira tickets** | bias gives no ticket slices, the rebuild reaches compare | ❌ → ✅ bias crashed on an empty frame. **Fixed**, with a test |
| **The cleanup step after an earlier rebuild** | links the published prev run | ❌ → ✅ `ln -sfn` onto last week's real folder linked inside it and silently reused old data. **Fixed** (red team): removed first |
| **A rebuild data folder misconfigured as the main one** | refused | ❌ → ✅ The cleanup step would `rm -rf` the live run's outputs. **Fixed:** the DAG refuses to build that command (`test_fresh_refuses_the_main_data_root`) |
| An update publishes during a rebuild | the rebuild keeps its run | ❌ → ✅ Each task re-read `latest.json`. **Fixed** (red team): `fresh` names the run once, the rest read it through xcom |
| A rebuild of an older run while a newer one is live | refused, never a rollback | ✅ (red team fix) |

## 4. The real team run (a copy of 70dd, both DAGs)

| Scenario | Expected | Result |
|---|---|---|
| Update DAG on a fresh data root | every output = its golden hash, validate 0 errors, published | ✅ 24 of 24 golden files, 0 errors, 1 known warning (the 289 tickets dated 0010–0012) |
| A second click straight away | stops in about 2 s, nothing rewritten | ✅ |
| Weekly rebuild | 0 differences, bias runs, nothing republished | ✅ 0 differences |
| Caches on, cold then warm | byte-identical | ✅ 24 of 24 both times; history 312 s → 74 s warm |
| All tests with the team data | pass | ✅ |
| The weekly GX audit | the same findings as validate | ✅ 285 expectations, 1 finding (the 289 dates), 11 s |

## 5. Other machines (Windows, Docker on a Mac)

| Scenario | Expected | Result |
|---|---|---|
| **Windows vs Mac, the resolver** | the same call edges | ❌ → ✅ Windows found 5 extra Django edges: Jedi walked folders in the file system's order (NTFS by name, APFS by hash). **Fixed:** one order on every OS. Byte-identical on both now |
| **Windows checkout, DVC** | the same code hashes | ❌ → ✅ CRLF line endings changed all 13 code hashes. **Fixed:** `.gitattributes` (LF everywhere), `dvc.lock` regenerated on Mac |
| **The update DAG in Docker, on data collected on the host** | runs | ❌ → ✅ history and resolve failed in 1 s on every try: each checkout pointed at its mirror by an **absolute host path**, which doesn't exist under Docker's `/opt/meridian` mount. This is also Windows' "failed after 1 s" in Appendix A. **Fixed:** the link is relative, and collect repairs an older snapshot's (`test_checkouts_work_wherever_the_data_root_is_mounted`) |
| **A non-default `AIRFLOW_UID`** (what Linux users set) | the stack starts | ❌ → ✅ The init container bypassed the image's entrypoint, which registers the user, so Airflow refused to start. **Fixed:** init runs through `/entrypoint`. Checked: `AIRFLOW_UID=501 docker compose run airflow-init` migrates and creates the admin |
| The update DAG in Docker, end to end | every task succeeds, published | ✅ 1,268 s, every task on its first try ([Appendix A](appendix-airflow.md)) |
| **The golden hashes under Docker's git** | 24 of 24 | 🟡 → ✅ 23 of 24 at first. `commit_file` differs by 9 of 849,643 rows: git 2.50 (Mac) and 2.39 (the image) detect renames of three small Django template files differently. Not our code. The golden is defined under the pipeline image (CONTRACTS.md: one image), so `commit_file`'s hash was recorded again from the Docker run (golden and `dvc.lock`): 24 of 24 |
| The weekly rebuild in Docker | 0 differences; GX Data Docs published | ✅ compare: 0 differences, publish skipped as designed; GX 308 expectations, 0 failed; bias 136 slices judged, 32 flagged, as on the update ([Appendix A](appendix-airflow.md)) |

## What the bugs had in common

- **Paths and order that depend on the machine:** the absolute alternates path, Jedi's folder order, CRLF, and git's rename detection. Each one is invisible on the machine that made the data. The fixes make the data depend only on its inputs: relative links, one sort order, LF, and one pinned image.
- **A check that read stale state:** status without a fetch, the gate without the trigger, the rebuild re-reading `latest.json`, the cleanup step linking into last week's folder.
- **A fallback that was too eager:** a broken file's sites taking another file's identical line.

## Still open (none changes a row of data)

- **The change report credits a moved, shifted or renamed line to the commit that last wrote its text, not the one that moved it** (C3, C5). `effective_sha` comes from blame of the line, so the data is consistent, but "what this push changed" reads as an older commit.
- **Edges added by a call inside a shared helper are credited to the callers' last commits** (C9), and a function moved within a file shows its edge as removed and added (C4). Per-push blast radius belongs to the push check on PRs, before merge.
- Per-slice bias flags aren't corrected for testing 136 slices at once (BIAS.md).

## Rerun it

```
python -m unittest discover -s tests -t .                                   # 287 tests, from Data-Pipeline/
MERIDIAN_RUN_ID=70dd1dabfed544b8 python -m unittest tests.test_golden -v    # the golden check, with the team data
python run_demo.py short                                                    # the fixture end to end, no data, no Docker
```

The fixture scenarios of rounds 1–3 are scripts in the scratch runs, not in the repo. Each bug they found has a regression test in `tests/`, named in the tables above.
