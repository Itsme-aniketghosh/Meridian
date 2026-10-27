# Shared snapshot (temporary)

This folder goes away once the team's GCS bucket is ready and collect moves the raw pulls itself.

Everyone has to work on the same run, or our tables won't join. Collect can clone the code anywhere, since a SHA is the same code everywhere. Tickets are different: a fresh pull today gets other tickets and a different `run_id`, so they come only from the zip.

## The team run

| | |
|---|---|
| run_id | `c5118b019d642a9b` (replaces v1 `70dd1dabfed544b8` on 2026-10-27: same snapshot and zip, new `meridian.toml`) |
| snapshot_at | `2026-10-01T01:23:03Z` |
| status | `complete`: all 22 repos pinned. Built 2026-10-23, replacing `973531f7fd4cfd5e` |
| zip | `meridian-raw-973531f7fd4cfd5e.zip`, 145 MB, [Google Drive](https://drive.google.com/file/d/1jq8gMWAQpmECNXrX2Su3I-xw7bwvu0tL/view?usp=drive_link) |
| sha256 | `d422ba26c976beeca953e8831e0f8f0eaad339a0359cdbd6fe4b9ea6c8f2d523` |

The zip holds real names ([CSV-POLICY.md](../CSV-POLICY.md)), so it's not in git. Ask the team for the link, and don't share it outside the team.

## Get the run, step by step

You need Python 3.11, git and about 5 GB free.

1. Get the latest code.

   ```
   cd <your Meridian repo>
   git checkout main
   git pull
   ```

2. Download the zip into this folder (`Data-Pipeline/shared-snapshot/`). Git ignores it here.

3. Check it. This must print the sha256 above.

   ```
   shasum -a 256 "Data-Pipeline/shared-snapshot/meridian-raw-973531f7fd4cfd5e.zip"                 # Mac / Linux
   certutil -hashfile "Data-Pipeline\shared-snapshot\meridian-raw-973531f7fd4cfd5e.zip" SHA256      # Windows
   ```

4. Unzip it into `snapshots/` from the repo root.

   ```
   unzip "Data-Pipeline/shared-snapshot/meridian-raw-973531f7fd4cfd5e.zip" -d snapshots             # Mac / Linux
   mkdir snapshots & tar -xf "Data-Pipeline\shared-snapshot\meridian-raw-973531f7fd4cfd5e.zip" -C snapshots   # Windows
   ```

   `snapshots/_cache/jira/SPARK/` and `snapshots/_cache/gitlab/` should each have a hidden `.pull.json`. Without it, collect pulls again.

5. Build the snapshot. The first time, it clones the 22 repos (about 2.5 GB), without contacting Jira or GitLab.

   ```
   cd "Data-Pipeline"
   python3 -m meridian.collect --run-id c5118b019d642a9b --config meridian.toml
   ```

6. The last line must say `run_id=c5118b019d642a9b`. If it says anything else, or fails, stop and tell the team.

7. Build the commit and ticket tables.

   ```
   python3 -m meridian.history --run-id c5118b019d642a9b --config meridian.toml
   ```

   Expect these rows, and a log line with `94.0% from the subject` for the last 1,000 Spark commits and `3 dead keys`.

   | Table | Rows |
   |---|---:|
   | `git_commit` | 139,228 |
   | `commit_file` | 849,643 |
   | `ticket` | 74,015 (59,491 Spark, 14,524 GitLab) |
   | `ticket_event` | 235,252 |
   | `ticket_link` | 61,615 |
   | `commit_ticket` | 42,191 |

Pass `--run-id c5118b019d642a9b` to every part, never `--run-id new`, which makes a run of your own. The rest is in [the pipeline README](../README.md).

## What collect keeps between runs

- `snapshots/_cache/repos/<owner>__<name>.git`: one mirror per repo, cloned once and then fetched (`mirrors` in [settings.toml](../settings.toml)). Every snapshot's checkout borrows its objects, so don't move or delete it.
- `snapshots/_cache/jira/SPARK/` and `snapshots/_cache/gitlab/`: the base pull, plus a `delta-<time>/` folder per later pull of changed tickets. Collect pulls a delta only once some repo's `ref` names a branch, which the team run doesn't.

## When the run changes

Only a change to `meridian.toml` makes a new `run_id`, and the team posts it here. Keep the zip, since the pulls don't change. Then `git pull`, redo steps 5 to 7 with the new ID, and rerun your own part.
