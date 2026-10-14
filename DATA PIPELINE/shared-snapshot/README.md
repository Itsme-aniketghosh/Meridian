# Shared snapshot · temporary

**Temporary.** This folder exists only until Utkarsh's GCS bucket is ready (`snapshot_bucket` in [meridian.toml](../meridian.toml)). Then collect uploads and downloads the raw pulls itself, and this folder gets deleted.

## Why this exists

Everyone has to work on the same run, or our tables won't join. The `run_id` proves two people have the same data, but it can't make the data:

| Part of the snapshot | How you get it |
|---|---|
| Code: 18 repos at pinned SHAs | Collect clones them from GitHub. A SHA is the same code everywhere |
| Tickets: raw Jira (Spark) and GitLab pulls | **Only from the zip.** A fresh pull today gets different tickets, so a different `run_id` |

## The current team run

| | |
|---|---|
| run_id | `973531f7fd4cfd5e` |
| snapshot_at | `2026-10-01T01:23:03Z` |
| status | `partial`: 4 django_packages refs are still `TBD-day4` (expected) |
| zip | `meridian-raw-973531f7fd4cfd5e.zip`, 145 MB |
| sha256 | `d422ba26c976beeca953e8831e0f8f0eaad339a0359cdbd6fe4b9ea6c8f2d523` |

**The zip is not in git.** It holds raw tickets with real names ([CSV-POLICY.md](../CSV-POLICY.md): raw data never goes in git), and it's over GitHub's 100 MB limit. Get it from Vishwa on the team WhatsApp group, and keep it within the team.

## Get the run · step by step

You need Python 3.11+, git and about 2 GB free.

**1. Get the latest code**

```
cd <your Meridian repo>
git checkout main
git pull
```

**2. Put the zip in this folder** (`DATA PIPELINE/shared-snapshot/`). git ignores it here.

**3. Check the download.** It must print the sha256 above.

```
shasum -a 256 "DATA PIPELINE/shared-snapshot/meridian-raw-973531f7fd4cfd5e.zip"                 # Mac / Linux
certutil -hashfile "DATA PIPELINE\shared-snapshot\meridian-raw-973531f7fd4cfd5e.zip" SHA256      # Windows
```

**4. Unzip into `snapshots/`,** from the repo root. Use the terminal, not double-click.

```
unzip "DATA PIPELINE/shared-snapshot/meridian-raw-973531f7fd4cfd5e.zip" -d snapshots             # Mac / Linux
mkdir snapshots & tar -xf "DATA PIPELINE\shared-snapshot\meridian-raw-973531f7fd4cfd5e.zip" -C snapshots   # Windows
```

You should now have `snapshots/_cache/jira/SPARK/` and `snapshots/_cache/gitlab/`, each with a hidden `.pull.json`. Without `.pull.json`, collect pulls again.

**5. Build the snapshot**

```
cd "DATA PIPELINE"
python3 -m meridian.collect --run-id 973531f7fd4cfd5e --config meridian.toml
```

The first time, it clones the 18 repos (about 1.6 GB). It does not contact Jira or GitLab.

**6. Check the last line.** It must say `run_id=973531f7fd4cfd5e`. If it says anything else, or fails, stop and tell Vishwa. Don't use that run.

**7. Build the history tables**

```
python3 -m meridian.history --run-id 973531f7fd4cfd5e --config meridian.toml
```

Expect 134,107 `git_commit` and 833,331 `commit_file` rows.

**Ticket tables (D4)** (`ticket`, `ticket_event`, `ticket_link`, `commit_ticket`) are on the `vishwa-collect` branch for now. To get them, run step 7 on that branch. Expect 74,015 tickets and 42,191 `commit_ticket` rows.

## Rules

- **Never use `--run-id new`.** That makes your own run, not the team one.
- **Pass `--run-id 973531f7fd4cfd5e` to your own part too,** so every table carries the same run.
- Your data lands in `snapshots/973531f7fd4cfd5e/` and `out/973531f7fd4cfd5e/`. Both are git-ignored.

## When the run changes

Around D4, Rachna's 4 package refs and Utkarsh's bucket settings land in `meridian.toml`. That gives a new `run_id`, and Vishwa posts it.

- **Keep the zip.** Those changes don't touch the Jira or GitLab pulls, so the same zip works for the next run.
- When a new `run_id` is posted: `git pull`, redo steps 5–7 with the new ID, then rerun your own part with it. No code changes are needed.
- This table gets updated with the new `run_id`.

## When GCS is ready

Collect reads and writes the bucket directly, and nobody shares a zip. Then this folder is deleted.
