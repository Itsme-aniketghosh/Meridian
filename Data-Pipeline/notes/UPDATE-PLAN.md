# Keeping the graph up to date · plan

Draft from a team discussion. Built so far: the Phase 3 caches, all switched off (Phases, below). Numbers (#1–#18) are the items to confirm.

## Now vs where we want to be

| | Now | Want |
|---|---|---|
| What a run reads | Fixed commits pinned in `meridian.toml`, one frozen snapshot | Each repo's main branch, resolved to a commit when collect runs |
| "Now" | `snapshot_at` fixed per dataset | `snapshot_at` = each run's pull time |
| Collect | Full pull (Jira, GitLab, clones) | Only what changed since the last run, from a stored cursor |
| When it runs | Nightly DAG, full recompute (~12 min here) | On request, daily, and a weekly full rebuild |
| Per push | Nothing | Push check on PRs (P1–P9), read-only |
| Publishing | Every finished run | Only runs that pass validate |
| Blast-radius changes | Not tracked | Per run: what changed, and which commit caused it |

## Agreed

1. **Keep ingesting continuously.** Update only what changed, not everything each time.
2. **Weekly full rebuild (Sunday).** Compare with the updates: 0 differences, otherwise alert and publish the full rebuild.
3. **Update on request.** A developer clicks "Update graph"; one run picks up every push since the last update.
4. **Batch pushes.** Pushes that arrive before or during a run go into the next one. None are skipped.
5. **Attribute changes per commit, even when batched.** Store the commit that introduced each call site and each call edge (blame), and write a per-run report: what changed, and which commit caused it.

## To confirm

6. **Daily automatic update**, mainly for ticket changes (they happen without pushes). Keeps the graph fresh if nobody clicks.
7. **One run at a time.** Each run matches against the previous one. *(The DAG already has `max_active_runs = 1`.)*
8. **Inside a run, changed repos in parallel**, plus repos that depend on them (a Django change re-resolves the packages). Then one short merge: join → graph → validate → publish.
9. **Never split one repo into pieces.** The resolver needs the whole repo.
10. **Publish only if validate passes**, else the last good block stays live. *(`settings.toml` has the switch; the publish step isn't built.)*
11. **Per-push blast radius lives in the push check** on PRs, before merge.
12. **Show staleness before the click:** "data 2 days old · 14 new pushes · Update". A click during a run doesn't start a second one.
13. **Build in phases** (see Phases, below).

## Contract changes (after the v1 freeze, with the team's OK)

14. Repos follow their main branch, resolved to a commit at collect time.
15. `snapshot_at` moves forward to each run's pull time.
16. Collect pulls only what changed since the last run, from a stored cursor. Each changed ticket comes with its **full changelog** (the rollback to `snapshot_at` replays it), and deleted tickets and links are noticed too.
17. Schema: `call_site.effective_sha` (join already blames each line, so the SHA is there), plus the commit that introduced each `call_edge`.
18. Storage: one permanent mirror per repo, plus a small manifest per run.

## DAGs

**Built (Phase 1 orchestration):** `meridian_update` and `meridian_rebuild` in `dags/meridian_dag.py`, from `dags/pipeline_spec.py`; `meridian.status`, `meridian.publish`, `meridian.compare`; the dashboard's staleness line and Update graph button; collect's mirrors and cursors (#14–#18, `meridian.collect`), `meridian.report` and the push check (`meridian.pushcheck`). Not yet: parallel repos (#8). Each DAG recomputes every part for now.

| DAG | Runs | Does |
|---|---|---|
| `meridian_update` **(built)** | the "Update graph" click, daily, later on pushes (Phase 2) | 1. **Anything new?** New commits or ticket changes since the last published run; if none, stop (so repeat clicks cost nothing). 2. Collect the changed repos and tickets. 3. Per changed repo, in parallel: history, scan → resolve (one task per repo, Airflow's dynamic task mapping). 4. owners → join → graph → validate → publish → change report |
| `meridian_rebuild` **(built)** | Sunday | The same parts on every repo from scratch, then compare with the last update: 0 differences, else alert and publish the rebuild. Also runs bias and the full statistics |
| push check | every PR to a watched repo (GitHub Actions, not Airflow) | Changed files + their importers against the last published block. P1–P9 as one comment. Read-only |

- **One run at a time:** `max_active_runs = 1` (built, both DAGs). A click during a run queues one run; when it starts, it takes every push up to then (#4). A click while one is already queued adds nothing (built: `meridian.status --update`), and the "anything new?" step drops a queued run with nothing to do (built: exit 99 skips the rest).
- **Publish** = copy `meridian.sqlite` and `dvc push`, only after validate passes (#10). Views always read the last published block. *(Built: `published/<run_id>.sqlite` and `published/latest.json`, both atomic; refused on any data error; `dvc commit` + `push` when `[dvc] push`.)*

## Faster

Times now (Oct 23–24), **on a Windows machine**: history 430 s, resolve 295 s, validate 165 s, scan 140 s, everything else under a minute each. On a Mac the same run takes history ~300 s, resolve ~74 s, validate ~19 s, scan ~6 s (about 7 minutes in all), so part of the Windows time is the file system and Docker's folder mount, not the pipeline.

| Change | Gain (estimated) | Phase |
|---|---|---|
| Skip repos with no new commits: reuse their last rows (code parts only, see caveat 1) | most daily runs touch a few of 22 repos | 1 |
| Repos in parallel inside a run (#8), with a worker cap: resolve uses ~1 GB per repo | a run takes as long as its slowest repo, not the sum | 1 |
| Repo mirrors kept between runs: `git fetch`, not clone (#18) | collect from ~1 min to seconds | 1 |
| Ticket pulls since a cursor (#16), not all 59k, each with its full changelog (caveat 3) | Jira from a full pull to the day's changes | 1 |
| **Built (Oct 24).** validate builds `meridian.sqlite` once (the dashboard reuses it) and runs one stats query per table, not per column | Estimated 165 s → ~30 s; measured on a copy of 70dd's block (Windows, 0.3–0.5 GB free): statistics 14–41 s per column, 15–43 s per table, the same `stats.json`, so **no gain**. The time is counting distinct values of long text columns (`ticket.description` alone 19 s), not reading tables. The dashboard saves its own ~1 min writer rebuild | 1 |
| Bias and full statistics weekly (in the rebuild), not on every update. The quick checks (rows, formats, ranges, coverage floors) still run on every update: they decide publish | −35 s per update | 1 |
| **Built (Oct 24), off by default:** `docker-compose.volumes.yaml`. Keep `snapshots/` and `out/` on a Docker volume, not the Windows/Mac folder. Mount the volumes at those same paths in the repo: `dvc.yaml` reads `../out` and `../snapshots` | git and file reads several times faster in Docker (not timed yet) | 1 |
| Scan cached per file, by git blob SHA. Resolve cached per file too, but also redone for every file that imports a changed module (caveat 2). **Built, off by default**: `[cache] scan` and `[cache] resolve` in `settings.toml`. Resolve records every repo file Jedi reads for a file (imports and re-exports however deep, stub lookups) and redoes the file when any of them changes; a push that adds or deletes a file redoes its repo; `call_edge` is rebuilt every run, not cached | scan + resolve from ~7 min to seconds on a typical push (resolve: less, its `call_edge` questions are still all asked) | 3 |
| SZZ on new fix commits only; keep earlier `bug_inducing` rows (blame of past commits never changes). Recount `file_bug_fixes`: its 365-day window moves. **Built, off by default**: `[cache] szz`. The fix's ticket `key` is recomputed too | history from ~7 min to seconds | 3 |

**Caveats.** Each one is a way an update could go quietly wrong. The Sunday rebuild would catch it, but only days later:

1. **"No new commits" skips only the code parts** (scan, resolve, blame). Owners and tickets still run on every update: the 180-day ownership window moves every day, and tickets change without pushes. And a **Django change re-runs every package**, because the packages resolve against the Django checkout (`[resolver] env = "snapshot"`).
2. **A file's resolution depends on other files**: what it imports and re-exports, and Django for the packages. Call edges reach 3 calls deep, across files. So a resolve cache keyed on a file's own content isn't enough: when a module changes, redo every file that imports it (`module_import`, read in reverse), and the callers within `call_edge_depth` of it.
3. **Cursor pulls must keep what the rollback needs.** Each changed ticket comes with its whole changelog, not just the latest fields, and a ticket or link deleted since the last pull is noticed, not silently kept.

## Phases

**Phase 0 · before `mlops-pipeline` merges.** The fixes below: four done; the `manifest.json` dependency, the one-machine rule for `dvc.lock` and the Windows gap still open.

**Phase 1 · runs on request, daily, and a weekly rebuild.** Each run still recomputes every part, but only for repos that changed.
- Contract changes #14–#18 (one new team run_id).
- `meridian_update` and `meridian_rebuild`; the "anything new?" step; publish only after validate (#10).
- The per-run change report: what changed, and which commit caused it (#5, #17).
- Staleness and the "Update graph" button in the tool (#12).
- The Phase 1 speed-ups in the table above.
- Done when: a click picks up every push since the last update, a Sunday rebuild shows 0 differences, and a failed validate leaves the last good block live.

**Phase 2 · pushes.**
- The push check on PRs (#11): GitHub Actions, read-only, P1–P9 as one comment, base = the last published block.
- Optional: a merged push starts `meridian_update` itself (still one run at a time, batched).
- Done when: a PR gets its comment in under 5 minutes (05-test), and a burst of pushes makes one run, not many.

**Phase 3 · update only what changed, inside each part.** Once runs get slow. Built, every switch off (`settings.toml` `[cache]`); `tests/test_cache.py` checks on fixtures that each cache writes the same bytes as no cache, and that after one change (for resolve, in an imported module) it equals a run from scratch.
- Scan and resolve cached per file, by git blob SHA.
- SZZ on new fix commits only.
- To switch on: set the three to true for updates, keep them off for the Sunday rebuild, and compare.
- Done when: the Sunday rebuild still equals the updates (0 differences), and a typical update takes seconds.

## Fixes on `mlops-pipeline` before it merges

- [x] Add `.gitattributes` (LF line endings) and regenerate `dvc.lock` on Mac/Linux: Windows line endings change the hashes. Done: 13 code and config hashes were Windows-only; `dvc status` is clean on Mac. On Windows, refresh the checkout once so files come out LF (below)
- [x] Add `dvc-gs` to `requirements.txt` (for the `gcs` remote). Done: `dvc-gs==3.1.0`, `gcsfs==2026.10.1`; `dvc version` lists gs
- [x] Add `.DS_Store` and `Thumbs.db` to `.dvcignore`. Done: the raw-pull hashes now match on Mac and Windows
- [ ] Find the Windows-vs-Mac difference in `resolution` and `call_edge` (2,293 vs 2,288 edges). Needs both machines' files. The regenerated `dvc.lock` records the Mac values: `resolution`, `call_edge`, `blast_radius`, `stats.json` differ from Windows, and `run.jsonl` by design (`image_digest`)
  - Checked on Windows after the LF refresh: no code or config file shows changed any more. Mac's `call_edge.jsonl` is 662,303 bytes ≈ **2,288** edges, as RESOLVER.md; Windows has 2,293. `resolution.jsonl` has the same 3,739 rows on both but 48 bytes differ, so a few values, not rows. The gap is Windows only. Next: send the Mac's `resolution.jsonl` and `call_edge.jsonl` and diff them against Windows
- [ ] **`run.jsonl` makes stages look changed on every other machine.** Its `image_digest` differs by machine on purpose, and history, scan, validate and bias depend on it in `dvc.yaml`, so `dvc repro` would rerun history and scan for nothing. Fix: depend on `../snapshots/${pipeline.run_id}/manifest.json` instead (the same run, no machine fingerprint), then regenerate `dvc.lock` once on Mac/Linux
- [ ] **One machine kind writes `dvc.lock`.** Until the Windows gap is fixed, a `dvc commit` on Windows overwrites the Mac values and back. Rule: `dvc.lock` is committed from Linux or Mac only (the Docker image, or a Mac)
- [x] The Windows refresh command below used `&&`, which Windows PowerShell 5.1 doesn't have. Fixed: one command per line

On Windows, after pulling this, commit or stash your work first, then once (cmd or PowerShell):

```
git rm -r --cached -q .
git reset --hard
```

so every file is checked out with LF.

## Sign-off before building

**Sign-off needed on DAGs, Faster and Phases (and #6–#18) before anything there is built.** Tick one line, or write what to change next to it:

- [ ] DAGs: `meridian_update`, `meridian_rebuild`, the push check in GitHub Actions
- [ ] Faster: the Phase 1 speed-ups, and the Phase 3 ones for later
- [ ] Phases 0–3 and their "done when"
- [ ] #6–#13 confirmed
- [ ] #14–#18 agreed (contract changes, a new team run_id)

## Next

Once signed off, this becomes one design record for the team, and Phase 0, then Phase 1, get built.
