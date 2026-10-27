# Appendix D: What we test and how

| Level | What it checks | How | Result |
|---|---|---|---|
| Unit tests | each part on its own fixture, edge cases included | `python -m unittest discover -s tests -t .`, in CI on Linux and Windows | 339 pass; without the team data 19 are skipped by design on Windows, 18 on Mac and Linux ([B §1](appendix-quality.md#1-tests)) |
| Answer keys | the block against hand-checked labels | `MERIDIAN_RUN_ID=c5118b019d642a9b`, the team data | Django 265/265 lines, 542/542 aliases, packages' precision and recall |
| Golden | the same inputs give the same bytes | md5 of the 24 output files vs the known-good sums in `golden/` | 24 of 24 |
| Rebuild | the incremental update equals a run from scratch | `meridian_rebuild` in Airflow, `compare` | 0 differences ([A](appendix-airflow.md)) |
| Scenarios | 42 situations the pipeline will meet, each answer written down before the run | 5 rounds, below | 24 passed first time, 13 bugs found and fixed, 4 open, 1 machine difference |
| Fault injection | 27 faults: missing, empty, corrupt and wrong-typed tables, bad webhooks, an unwritable disk | a fixture run per fault | 4 bugs found, all fixed with tests; a failed run never publishes |
| Held-out replay | the push check on real commits it was never tuned on | 21 migration commits, then 9 later Django commits, bars set first ([notes](../notes/PASS-BARS-2026-10-26.md)) | P1 4/4 and 2/2 true |

The scenario rounds:
1. The update DAG (Airflow's task graph) on a fixture: a click with nothing new, a push, a forced run, the same parts run twice.
2. Matching across nine single-purpose commits: inserts, moves, renames, duplicates, splits, a broken file.
3. Rebuild and publish: a tampered table, a failing validation, caches on and off (caches on, cold then warm: all 24 outputs byte-identical, and a warm `history` drops from 312 s to 74 s natively), a rollback attempt.
4. Both DAGs on the real team run, with the golden hashes as the answer key.
5. Other machines: Windows, and Docker on a Mac.

An independent agent ran each of rounds 1–4 in its own copy of the code and data.

Every bug we found is fixed, with a regression test in `tests/`. They fell into three groups:
- Paths and order that depend on the machine: an absolute git path, Jedi's folder order, CRLF line endings, git's rename detection.
- Checks reading stale state: `status` without a fetch, a gate ignoring the trigger, a rebuild re-reading `latest.json`.
- A fallback that was too eager: a broken file's sites taking another file's identical line.

## Still open

- The change report credits a moved or renamed line to the commit that last wrote its text, not the one that moved it. The data stays consistent.
- `blocked_by`, P4 and chains run on fixtures only. No tracked symbol is called through an object on real data.
- The bias flags aren't corrected for 136 tests at once. Under Bonferroni (the 5% error rate divided by the number of tests), 26 of 32 hold.
- No real Slack or email alert has gone out yet.
