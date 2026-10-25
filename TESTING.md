# Running the tests

## Once: a Python 3.11 environment

```
conda create -y -n meridian python=3.11
conda activate meridian
pip install -r requirements.txt          # from the repo root
```

3.11 is the team's version (tree-sitter won't build on 3.14). Other versions may pass the tests and still give different bytes.

## Every test that needs no team data

From `Data-Pipeline/`, about a minute:

```
cd "Data-Pipeline"
python -m unittest discover -s tests -t .
```

Expect `Ran 248 tests ... OK (skipped=10)` (Windows, Python 3.11, Oct 24). 7 skipped ones need the team run, 3 need Airflow (`test_dag`'s DagBag half). CI runs exactly this on every push and pull request, on Linux and Windows, and runs the 3 Airflow ones in the Docker image (`.github/workflows/ci.yml`, below).

## Every test, the team run included

Needs the team run rebuilt first (`snapshots/70dd1dabfed544b8/` and `out/70dd1dabfed544b8/`, see [shared-snapshot/README.md](Data-Pipeline/shared-snapshot/README.md)). About 6 minutes, and close other apps: it resolves Django and the 20 packages again.

```
set MERIDIAN_RUN_ID=70dd1dabfed544b8                      # cmd
$env:MERIDIAN_RUN_ID = "70dd1dabfed544b8"                 # PowerShell
export MERIDIAN_RUN_ID=70dd1dabfed544b8                   # Mac, Linux

python -m unittest discover -s tests -t .
```

Expect `Ran 248 tests ... OK (skipped=3)`, only the Airflow ones skipped. The extra 7 check the Django answer keys (542 of 542 alias calls, 265 of 265 edit lines and 2 in `code.sample`, no tracked name lost to parse failures), the packages' precision and recall, and the v1 golden files (below). The golden one checks the files on disk: on Windows, files made before the resolver fix (63851a7) fail on resolution, call_edge and blast_radius until resolve and graph are rerun with v1 code. Code from v1.2 on (schema.sql) writes other bytes by design: the next team run_id gets its own golden file.

## Freeze v1

v1 is frozen on run `70dd1dabfed544b8`: no contract change before this passes. `tests/test_golden.py`, two halves:

1. **Fixture, everywhere (CI too).** The KB fixture through `graph` and the writer: the sha256 of all 25 `.jsonl` files and of every SQLite table's rows must equal `Data-Pipeline/golden/fixture.json`. Rows, not the `.sqlite` file: its header holds the SQLite version.
2. **Team run, with `MERIDIAN_RUN_ID=70dd1dabfed544b8` and `out/70dd1dabfed544b8/` present.** The md5 and size of all 24 `.jsonl` outputs must equal `Data-Pipeline/golden/70dd1dabfed544b8.json`, which holds `dvc.lock`'s Mac values. `run.jsonl` is left out: its `image_digest` differs per machine by design. Every mismatching file is listed. It never runs in CI: the team data is private.

On Windows (Oct 24) 21 of 24 match; `resolution`, `call_edge` and `blast_radius` don't (the resolver's Windows gap, UPDATE-PLAN.md).

To change the fixture's bytes on purpose: `python -m tests.test_golden --update` (from `Data-Pipeline/`), and say why in the commit. The team-run file is never regenerated: a contract change means a new team `run_id` and a new golden file.

## Airflow

`test_dag` checks both DAGs against `dags/pipeline_spec.py` everywhere. Its last 3 tests load them with Airflow's own DagBag, so they need the Docker image. CI's `airflow` job runs them on every push. By hand, from `Data-Pipeline/`, with Docker running:

```
docker compose build airflow-scheduler
docker compose run --rm --no-deps -e CONNECTION_CHECK_MAX_COUNT=0 -w /opt/meridian/Data-Pipeline airflow-scheduler python -m unittest tests.test_dag -v
```

No database is needed: DagBag only parses the files. The image build needs about 3 GB of free memory.

## One part

```
python -m unittest tests.test_graph -v
```

One file per part: `test_collect` (branches, ticket deltas, deleted tickets, mirrors), `test_history`, `test_tickets`, `test_szz`, `test_scan`, `test_resolve`, `test_owners`, `test_join`, `test_graph`, `test_validate`, `test_bias`, `test_report` (the per-run change report), `test_publish` (publish, compare, status).

And across parts: `test_kb` (the knowledge base's queries), `test_cache` (Phase 3: each cache on equals off, and an update equals a run from scratch), `test_dag` (both DAGs follow the spec), `test_pushcheck` (P1–P9 on a fixture block), `test_dashboard`, `test_gold`, `test_golden`, `test_check_csv`.

## Seeing the data

```
python -m tests.demo --run-id 70dd1dabfed544b8 --config meridian.toml        # each part's tables, in the terminal
python -m tests.dashboard --run-id 70dd1dabfed544b8 --config meridian.toml   # -> out/70dd1dabfed544b8/dashboard.html
```

The demo runs any part whose tables aren't there yet. Don't start it while another pipeline run is going: both would write the same files.
