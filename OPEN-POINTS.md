# Open points · step 1 · Oct 24

Everything still open before graph data block v1 freezes, by owner. Team run `70dd1dabfed544b8`. Pulled from [PENDING-TASKS](DATA%20PIPELINE/PENDING-TASKS.md), [PIPELINE-PLAN](DATA%20PIPELINE/PIPELINE-PLAN.md), the KB [testing plan](DATA%20PIPELINE/KNOWLEDGE-BASE.md#testing-plan), [JOIN](DATA%20PIPELINE/JOIN.md), [RESOLVER](DATA%20PIPELINE/RESOLVER.md) and [GRAPH](DATA%20PIPELINE/GRAPH.md). Tick a box when it's done, with the number it gave.

🔴 = blocks someone else.

## Aniket · graph

- [ ] Rerun resolve and graph after the Windows `array.py` fix; `call_edge` should be 2,288 as in RESOLVER.md (2,293 before, on Python 3.12)
- [ ] The full test run with `MERIDIAN_RUN_ID=70dd1dabfed544b8`
- [ ] Open the `aniket-graph` PR to `main`
- [ ] OK the writer change: `call_site` carries earlier runs' versions (JOIN.md, Flags)
- [ ] Decide with Utkarsh what graph precision (≥ 0.90) is measured against: the GitLab gold set is ticket-level, and GitLab has no Python, so graph can't produce those rows
- [ ] Hand-check 50 Spark collision pairs, generic names dropped first: on this run no ticket maps to a call site, so graph has none to check
- [ ] First end-to-end run from Airflow, with Utkarsh (D8)
- [ ] One-page handoff note for stall rules, Jev and views (D10)

## Utkarsh · join, orchestration

- [ ] 🔴 Gold-set precision bar: the decision above, with Aniket
- [ ] Accept the 21,810 Spark files with no owner (2,785 of 3,048 people have no team). Already counted in `coverage` as `files_owned` 22,060 / 43,870
- [ ] Hand-check a sample of `ticket_map` `path` and `stack_trace` rows for the 0.90 bar
- [ ] A second real run with `--prev-run`, once there's a later snapshot
- [ ] CONTRACTS.md: a line for join's new `--prev-run` argument
- [ ] Pin SQLite 3.50.4 in the image: the writer's same-bytes check holds only on it (machines here have 3.53)
- [ ] Real parts in the Airflow DAG (D8), schedule and rebuild check (D9), freeze v1 with the golden test in CI (D10)
- [ ] CI: GitHub Actions on Ubuntu and Windows, Python 3.11, `python -m unittest discover -s tests -t .`
- [ ] Cross-machine check: the same run on Windows and Mac, every `out/<run_id>/*.jsonl` sha256 equal. Never done; the `array.py` bug was Windows-only
- [ ] GCP names `image` and `snapshot_bucket` in infra.toml
- [ ] 03-data updated to match the contracts once v1 is frozen
- [ ] Outside the pipeline: missing surnames in the scoping doc, license checks, timeline against course deadlines

## Rochan · resolver

- [ ] Review the Windows fix in `meridian/resolve.py`: a submodule is searched for only in its package's folders, so Django's two `array.py` files are no longer taken for the builtin `array` (commit `4aac4ca`, fixture in `tests/test_resolve.py`)
- [ ] Hand-check 20 `receiver_from` rows on Django (0 on this run: worth knowing if that is right)
- [ ] Later: the depth for `blocked_by` (may need 5), with `[resolver] call_edge_depth`

## Chaitali · owners, gold set

- [ ] 🔴 Human spot-check of 10 waits in the GitLab gold set: `human_verdict` filled, ≥ 9 of 10 hold (0 of 262 today). Graph scoring waits on it
- [ ] Hand-check the 20 biggest identity groups (`owners.py --top 20`) on this run, result written down

## Vishwa · collect, history

- [ ] D5: run `week1/pair3/defects4j/make_labels.py` and `ticket_key_check.sh` once: same 130 labels and 812 of 854 keyed
- [ ] Rollback spot check of 20 Spark tickets against the Jira UI (20 of 20)

## Rachna · scanner

- [ ] Hand-check 20 rows each of the new columns on Django: `in_except`, `module_import`, `deprecation` (20 of 20)

## Not assigned yet

- [ ] Rules stage (5): `order_of_work` in KNOWLEDGE-BASE.md never ends on a `blocked_by` cycle (its recursive `UNION ALL` has no guard)
- [ ] Tracking (stage 6): `suggestion`, `outcome`, `push_alert` move into `schema.sql` when the first suggestion goes out (KB gap G1)
- [ ] Dropped first if behind: Django replay error ≤ 1%, method-call benchmark, component-level owner test, Defects4J bisection, 50 Django pushes, GitLab Jul–Dec 2024 link pass
