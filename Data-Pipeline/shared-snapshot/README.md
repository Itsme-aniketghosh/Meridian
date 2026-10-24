# Shared snapshot · temporary

**Temporary.** This folder exists only until Utkarsh's GCS bucket is ready (`snapshot_bucket` in [settings.toml](../settings.toml)). Then collect uploads and downloads the raw pulls itself, and this folder gets deleted.

## Why this exists

Everyone has to work on the same run, or our tables won't join. The `run_id` proves two people have the same data, but it can't make the data:

| Part of the snapshot | How you get it |
|---|---|
| Code: 22 repos at pinned SHAs | Collect clones them from GitHub. A SHA is the same code everywhere |
| Tickets: raw Jira (Spark) and GitLab pulls | **Only from the zip.** A fresh pull today gets different tickets, so a different `run_id` |

## The current team run

| | |
|---|---|
| run_id | `70dd1dabfed544b8` |
| snapshot_at | `2026-10-01T01:23:03Z` |
| status | `complete`: all 22 repos pinned. Built 2026-10-23 after dynamic-config ([FIXES.md](../FIXES.md)). Replaces `973531f7fd4cfd5e` |
| zip | `meridian-raw-973531f7fd4cfd5e.zip`, 145 MB · [Google Drive](https://drive.google.com/file/d/1jq8gMWAQpmECNXrX2Su3I-xw7bwvu0tL/view?usp=drive_link) (team only) |
| sha256 | `d422ba26c976beeca953e8831e0f8f0eaad339a0359cdbd6fe4b9ea6c8f2d523` |

**The zip is not in git.** It holds raw tickets with real names ([CSV-POLICY.md](../CSV-POLICY.md): raw data never goes in git), and it's over GitHub's 100 MB limit. Download it from **[Google Drive](https://drive.google.com/file/d/1jq8gMWAQpmECNXrX2Su3I-xw7bwvu0tL/view?usp=drive_link)**. Access is restricted to the team: if it says you need access, ask Vishwa. Keep it within the team, and don't reshare it.

## Get the run · step by step

You need Python 3.11+, git and about 2 GB free.

**1. Get the latest code**

```
cd <your Meridian repo>
git checkout main
git pull
```

**2. Download the zip** from the [Drive link](https://drive.google.com/file/d/1jq8gMWAQpmECNXrX2Su3I-xw7bwvu0tL/view?usp=drive_link) and put it in this folder (`Data-Pipeline/shared-snapshot/`). git ignores it here.

**3. Check the download.** It must print the sha256 above.

```
shasum -a 256 "Data-Pipeline/shared-snapshot/meridian-raw-973531f7fd4cfd5e.zip"                 # Mac / Linux
certutil -hashfile "Data-Pipeline\shared-snapshot\meridian-raw-973531f7fd4cfd5e.zip" SHA256      # Windows
```

**4. Unzip into `snapshots/`,** from the repo root. Use the terminal, not double-click.

```
unzip "Data-Pipeline/shared-snapshot/meridian-raw-973531f7fd4cfd5e.zip" -d snapshots             # Mac / Linux
mkdir snapshots & tar -xf "Data-Pipeline\shared-snapshot\meridian-raw-973531f7fd4cfd5e.zip" -C snapshots   # Windows
```

You should now have `snapshots/_cache/jira/SPARK/` and `snapshots/_cache/gitlab/`, each with a hidden `.pull.json`. Without `.pull.json`, collect pulls again.

**5. Build the snapshot**

```
cd "Data-Pipeline"
python3 -m meridian.collect --run-id 70dd1dabfed544b8 --config meridian.toml
```

The first time, it clones the 22 repos (about 2.5 GB). It does not contact Jira or GitLab.

**6. Check the last line.** It must say `run_id=70dd1dabfed544b8`. If it says anything else, or fails, stop and tell Vishwa. Don't use that run.

**7. Build the history tables**

```
python3 -m meridian.history --run-id 70dd1dabfed544b8 --config meridian.toml
```

This writes the commit tables (D3) and the ticket tables (D4) in one go. Expect:

| Table | Rows |
|---|---:|
| `git_commit` | 139,228 |
| `commit_file` | 849,643 |
| `ticket` | 74,015 (59,491 Spark, 14,524 GitLab) |
| `ticket_event` | 235,252 |
| `ticket_link` | 61,615 |
| `commit_ticket` | 42,191 |

The log should also say `94.0% from the subject` for the last 1,000 Spark commits, and `3 dead keys`.

## Rules

- **Never use `--run-id new`.** That makes your own run, not the team one.
- **Pass `--run-id 70dd1dabfed544b8` to your own part too,** so every table carries the same run.
- Your data lands in `snapshots/70dd1dabfed544b8/` and `out/70dd1dabfed544b8/`. Both are git-ignored.

## When the run changes

Around D4, Rachna's 4 package refs land in `meridian.toml`. That gives a new `run_id`, and Vishwa posts it. Utkarsh's bucket settings are in `settings.toml`, which doesn't change the `run_id`.

- **Keep the zip.** Those changes don't touch the Jira or GitLab pulls, so the same zip works for the next run.
- When a new `run_id` is posted: `git pull`, redo steps 5–7 with the new ID, then rerun your own part with it. No code changes are needed.
- This table gets updated with the new `run_id`.

## When GCS is ready

Collect reads and writes the bucket directly, and nobody shares a zip. Then this folder is deleted.
