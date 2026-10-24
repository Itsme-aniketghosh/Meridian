# Running the tests

## Once: a Python 3.11 environment

```
conda create -y -n meridian python=3.11
conda activate meridian
pip install -r requirements.txt          # from the repo root
```

3.11 is the team's version (tree-sitter won't build on 3.14). Other versions may pass the tests and still give different bytes.

## Every test that needs no team data

From `DATA PIPELINE/`, about a minute:

```
cd "DATA PIPELINE"
python -m unittest discover -s tests -t .
```

Expect `Ran 179 tests ... OK (skipped=6)`. The 6 skipped ones need the team run.

## Every test, the team run included

Needs the team run rebuilt first (`snapshots/70dd1dabfed544b8/` and `out/70dd1dabfed544b8/`, see [shared-snapshot/README.md](DATA%20PIPELINE/shared-snapshot/README.md)). About 6 minutes, and close other apps: it resolves Django and the 20 packages again.

```
set MERIDIAN_RUN_ID=70dd1dabfed544b8                      # cmd
$env:MERIDIAN_RUN_ID = "70dd1dabfed544b8"                 # PowerShell
export MERIDIAN_RUN_ID=70dd1dabfed544b8                   # Mac, Linux

python -m unittest discover -s tests -t .
```

Expect `Ran 179 tests ... OK`, none skipped (178 on Windows, Python 3.11, Oct 24, before the collision test). The extra 6 check the Django answer keys (542 of 542 alias calls, 265 of 265 edit lines and 2 in `code.sample`) and the packages' precision and recall.

## One part

```
python -m unittest tests.test_graph -v
```

One file per part: `test_collect`, `test_history`, `test_tickets`, `test_szz`, `test_scan`, `test_resolve`, `test_owners`, `test_join`, `test_graph`, plus `test_kb` (the knowledge base's queries), `test_dashboard`, `test_gold`, `test_check_csv`.

## Seeing the data

```
python -m tests.demo --run-id 70dd1dabfed544b8 --config meridian.toml        # each part's tables, in the terminal
python -m tests.dashboard --run-id 70dd1dabfed544b8 --config meridian.toml   # -> out/70dd1dabfed544b8/dashboard.html
```

The demo runs any part whose tables aren't there yet. Don't start it while another pipeline run is going: both would write the same files.
