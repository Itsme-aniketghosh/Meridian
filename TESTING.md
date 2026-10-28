# Running the tests

## Once: a Python 3.11 environment

```
conda create -y -n meridian python=3.11
conda activate meridian
pip install -r requirements.txt          # from the repo root
```

Use 3.11: tree-sitter won't build on 3.14, and other versions may pass and still give different bytes.

## Every test that needs no team data

About a minute:

```
cd "Data-Pipeline"
python -m unittest discover -s tests -t .
```

Expect `Ran 342 tests ... OK (skipped=19)` (Windows, Python 3.11, Oct 27): 11 skipped need the team run, 5 need Airflow (`test_dag.Dag`), 2 need Great Expectations (`test_gx.Audit`), and 1 runs off Windows only (`test_resolve.ReexportLoop`, a re-export loop overflows Windows' C stack), so Mac and Linux skip 18. CI runs this on Linux and Windows for every pull request and push to main, after flake8 at 120 characters (`Data-Pipeline/setup.cfg`). The Airflow and GX tests run in their own jobs, and `test_pushcheck` runs with only the packages `push-check.yml` installs ([ci.yml](.github/workflows/ci.yml)).

## Every test, the team run included

First rebuild the team run ([shared-snapshot/README.md](Data-Pipeline/shared-snapshot/README.md)). This takes about 6 minutes and resolves Django and the 20 packages again, so close other apps.

```
set MERIDIAN_RUN_ID=c5118b019d642a9b                      # cmd
$env:MERIDIAN_RUN_ID = "c5118b019d642a9b"                 # PowerShell
export MERIDIAN_RUN_ID=c5118b019d642a9b                   # Mac, Linux

python -m unittest discover -s tests -t .
```

Expect `Ran 342 tests ... OK (skipped=7)` on Mac and Linux, 8 on Windows. The extra 11 check the Django answer keys (542 of 542 alias calls, 265 of 265 edit lines), the packages' precision and recall, the golden files, and every real call edge.

## The golden files

The team run is `c5118b019d642a9b` (v1 was `70dd1dabfed544b8`, kept as `golden/70dd1dabfed544b8.json`). `tests/test_golden.py` has two halves:

1. The fixture, everywhere. The sha256 of all 25 `.jsonl` files and of every SQLite table's rows must equal `Data-Pipeline/golden/fixture.json`. Rows, not the `.sqlite` file, whose header holds the SQLite version.
2. The team run, only with `MERIDIAN_RUN_ID` set and `out/c5118b019d642a9b/` present. The md5 and size of all 24 `.jsonl` outputs must equal `Data-Pipeline/golden/c5118b019d642a9b.json` (`run.jsonl` holds a per-machine `image_digest`, so it's left out). Never in CI: the team data is private.

The golden values come from the pipeline's Docker image, and on Oct 27 all 24 matched. One known difference: a git other than the image's 2.39 can see a few renames differently in `commit_file` (9 of 849,643 rows with git 2.50).

To change the fixture's bytes on purpose, run `python -m tests.test_golden --update` from `Data-Pipeline/` and say why in the commit. The team-run file is never regenerated: a contract change means a new `run_id` and a new golden file.

## Airflow

`test_dag` checks both DAGs against `dags/pipeline_spec.py`. Its 5 `Dag` tests load them with Airflow's DagBag, so they need the Docker image (CI's `airflow` job). By hand, from `Data-Pipeline/` with Docker running:

```
docker compose build airflow-scheduler
docker compose run --rm --no-deps -e CONNECTION_CHECK_MAX_COUNT=0 -w /opt/meridian/Data-Pipeline airflow-scheduler python -m unittest tests.test_dag -v
```

No database is needed. The image build needs about 3 GB of free memory.

## One part

```
python -m unittest tests.test_graph -v
```

Each part has its own file (`test_collect`, `test_history`, `test_scan`, and so on), and `test_publish` also covers compare and status. A few cut across parts: `test_kb` (the knowledge base's queries), `test_cache` (a cache on gives the same bytes as off), `test_validate.Alert` (an alert really reaches a local Slack-style webhook and SMTP server, from `tests/alert_sinks.py`), `test_dag`, `test_pushcheck` (P1–P9) and `test_golden`.

## Seeing the data

```
python -m tests.demo --run-id c5118b019d642a9b --config meridian.toml        # each part's tables, in the terminal
python -m tests.dashboard --run-id c5118b019d642a9b --config meridian.toml   # -> out/c5118b019d642a9b/dashboard.html
```

The demo runs any part whose tables are missing, so don't start it during another pipeline run.
