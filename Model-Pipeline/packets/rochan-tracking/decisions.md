# Rochan: decisions

Each decision has its test and the rule that picks, set before the run. Results go under each one after the run, with N. Runs are tagged `decision=rochan-<n>` in MLflow. Decisions 1–7 and 9–11 are added on the day each is due (packet dates).

All timings below: one MacBook (Apple silicon), Python 3.11.15, `mlflow==3.18.0`, local SQLite store.

## Tracking choices

Not in the packet's table, but each one changes how fast or how safely everyone logs, so each was measured.

### T1. How `log_llm_call` stores calls

Options:
- **a. one MLflow run per call**, nested under the agent's run
- **b. append one row per call** to a table in the run (`mlflow.log_table`)
- **c. keep rows in memory**, write them once as one table when the run ends (and every 500 calls)

Test: 200 calls of the same shape, each way, into a fresh store. Time per call.

Rule: the fastest option that still keeps one row per call, readable later as a table.

Result (Nov 5, N = 200 calls each):

| Option | Per call | 200 calls |
|---|---|---|
| a. run per call | 7.4 ms | 1.48 s |
| b. append per call | 12.3 ms, and it grows with the table: each append rereads it | 2.46 s |
| c. in memory, one write | 0.06 ms | 0.012 s |

Picked: **c**. About 120 times faster than a run per call, and the UI shows one run per agent batch instead of thousands. What it costs: rows not yet written are lost if the process is killed. The run's `with` writes them on the way out, even on an exception (`test_a_failed_run_keeps_its_calls`), an exit hook writes them for runs ended without `with`, and no more than 500 rows are ever held.

### T2. Parallel writers on one SQLite file

Question: SQLite locks the file while writing. Do parallel processes (Optuna workers, agent batches) fail with "database is locked"?

Test: 8 processes, 25 runs each through `tracking.start_run`, all at once, into one store.

Rule: if any run fails, document "log from the parent process only". If none does, parallel processes can log directly.

Result (Nov 5): 200 of 200 runs written, 0 errors, 3.5 s. Picked: **log directly**. Threads in one process work too: 400 calls from 8 threads land as 400 rows (`test_calls_from_worker_threads`).

## 8. MLflow store

Options: **file store** (`mlruns/`), **SQLite** (`sqlite:///mlflow.db`).

Test: on each store, in separate processes: register two versions of `defect-risk`, set `champion` on v2 and `previous` on v1, read both back from a new process; roll back (`champion` to v1, drop `previous`), read back again. The check script was written before it ran.

Rule: the store where every step passes with no extra settings. If both pass, SQLite (Contract 3's choice).

Result (Nov 5, `mlflow==3.18.0`):

| Store | Register, aliases | Read in a new process | Rollback, read again |
|---|---|---|---|
| File store, as installed | refused: "the filesystem tracking backend is in maintenance mode", needs `MLFLOW_ALLOW_FILE_STORE=true` | refused | refused |
| File store, with that setting | pass | pass | pass |
| SQLite | pass (0.04 s) | pass | pass |

Picked: **SQLite**. The packet's reason, that the file store can't hold aliases reliably, isn't what we found: with the opt-out set, it held them. The real reason is that MLflow 3.18 refuses the file store unless every laptop and CI sets an opt-out flag, and it gets no more updates. `tracking.py` defaults to `sqlite:///<repo root>/mlflow.db`.
