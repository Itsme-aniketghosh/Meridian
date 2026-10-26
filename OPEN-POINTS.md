# Open points · step 1 · Oct 24

Everything still open before graph data block v1 freezes, by owner. Team run `70dd1dabfed544b8`. Pulled from [PENDING-TASKS](Data-Pipeline/notes/PENDING-TASKS.md), [PIPELINE-PLAN](Data-Pipeline/notes/PIPELINE-PLAN.md), the KB [testing plan](Data-Pipeline/KNOWLEDGE-BASE.md#testing-plan), [JOIN](Data-Pipeline/JOIN.md), [RESOLVER](Data-Pipeline/RESOLVER.md) and [GRAPH](Data-Pipeline/GRAPH.md). Tick a box when it's done, with the number it gave.

🔴 = blocks someone else.

## Aniket · graph

- [x] Rerun resolve after the Windows `array.py` fix, on Python 3.11: 2,293 edges, byte-identical to the 3.12 run before the fix (sha256 `8c6b9450d25c2fdb`)
- [ ] Find the 5 Django edges between this 2,293 and RESOLVER.md's 2,288, with whoever wrote 2,288 (Python version and the fix are ruled out)
- [x] The full test run with `MERIDIAN_RUN_ID=70dd1dabfed544b8`: 178 of 178 OK, 0 skipped, Python 3.11, Windows (6 min)
- [ ] Open the `aniket-graph` PR to `main`
- [x] OK the writer change: `call_site` carries earlier runs' versions (JOIN.md, Flags). Reviewed: it allows earlier run_ids in `call_site` only, never one known after `snapshot_at`, and both cases have tests
- [ ] Decide with Utkarsh what graph precision (≥ 0.90) is measured against. Proposal in GRAPH.md: fixtures plus the Spark collisions; the GitLab gold set scores ticket-level chains later
- [x] 50 Spark collision pairs, generic names dropped first, model read: hunk headers 0.64, the new inside rule 1.00 (32 of 32), 62 pairs left, 12 both open (GRAPH.md, `labels/spark_collision_pairs_50.csv`)
- [ ] Human check of those 50: fill `human_verdict` (the rule was written after reading this sample)
- [ ] First end-to-end run from Airflow, with Utkarsh (D8)
- [x] One-page handoff note for stall rules, Jev and views (D10): [HANDOFF.md](Data-Pipeline/notes/HANDOFF.md)

## MLOps course submission (branch `mlops-pipeline`, [Data-Pipeline/README.md](Data-Pipeline/README.md))

- [ ] Aniket: run the DAG in Airflow (`docker compose up -d --build` in Data-Pipeline/, about 4 GB free memory), run the Airflow half of `tests/test_dag.py` in the image, and save the Gantt chart of one run for the report
- [ ] Utkarsh: a real DVC remote (the GCS bucket `.dvc/config` already names), then `dvc push` the raw pulls and run 70dd's outputs
- [ ] Team: the owner-rule mitigation in [BIAS.md](Data-Pipeline/BIAS.md), a person's team from 730 days instead of 180 (accuracy 0.373 → 0.458). A new run_id
- [ ] Team: add `apache/spark-docker` and `apache/spark-kubernetes-operator` to the snapshot, so Kubernetes and Docker tickets can map to code (BIAS.md). A new run_id
- [ ] Vishwa: 289 Spark tickets dated 0010–0012 come that way from Apache Jira; decide whether tickets.py should flag them (validate warns today)

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
- [ ] GCP names `image` and `snapshot_bucket` in settings.toml
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
