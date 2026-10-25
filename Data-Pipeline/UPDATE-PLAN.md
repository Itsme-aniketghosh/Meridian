# Keeping the graph up to date · plan

Draft from Aniket's discussion with Utkarsh. Nothing here is built yet. Numbers (#1–#18) are the items to confirm.

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

## Contract changes (after the v1 freeze, Aniket's OK)

14. Repos follow their main branch, resolved to a commit at collect time.
15. `snapshot_at` moves forward to each run's pull time.
16. Collect pulls only what changed since the last run, from a stored cursor.
17. Schema: `call_site.effective_sha` (join already blames each line, so the SHA is there), plus the commit that introduced each `call_edge`.
18. Storage: one permanent mirror per repo, plus a small manifest per run.

## DAGs

Today there is one DAG that recomputes everything. Proposed:

| DAG | Runs | Does |
|---|---|---|
| `meridian_update` | the "Update graph" click, daily, later on pushes (Phase 2) | 1. **Anything new?** New commits or ticket changes since the last published run; if none, stop (so repeat clicks cost nothing). 2. Collect the changed repos and tickets. 3. Per changed repo, in parallel: history, scan → resolve (one task per repo, Airflow's dynamic task mapping). 4. owners → join → graph → validate → publish → change report |
| `meridian_rebuild` | Sunday | The same parts on every repo from scratch, then compare with the last update: 0 differences, else alert and publish the rebuild. Also runs bias and the full statistics |
| push check | every PR to a watched repo (GitHub Actions, not Airflow) | Changed files + their importers against the last published block. P1–P9 as one comment. Read-only |

- **One run at a time:** `max_active_runs = 1` (already set). A click during a run queues one run; when it starts, it takes every push up to then (#4). The "anything new?" step drops a second queued click.
- **Publish** = copy `meridian.sqlite` and `dvc push`, only after validate passes (#10). Views always read the last published block.

## Faster

Times now (Oct 23–24): history 430 s, resolve 295 s, validate 165 s, scan 140 s, everything else under a minute each.

| Change | Gain (estimated) | Phase |
|---|---|---|
| Skip repos with no new commits: reuse their last rows | most daily runs touch a few of 22 repos | 1 |
| Repos in parallel inside a run (#8) | a run takes as long as its slowest repo, not the sum | 1 |
| Repo mirrors kept between runs: `git fetch`, not clone (#18) | collect from ~1 min to seconds | 1 |
| Ticket pulls since a cursor (#16), not all 59k | Jira from a full pull to the day's changes | 1 |
| validate builds `meridian.sqlite` once (the dashboard reuses it) and runs one stats query per table, not per column | 165 s → ~30 s | 1 |
| Bias and full statistics weekly (in the rebuild), not on every update | −35 s per update | 1 |
| Keep `snapshots/` and `out/` on a Docker volume, not the Windows/Mac folder | git and file reads several times faster in Docker | 1 |
| Scan and resolve cached per file, by git blob SHA: only changed files are parsed | scan + resolve from ~7 min to seconds on a typical push | 3 |
| SZZ on new fix commits only; keep earlier `bug_inducing` rows | history from ~7 min to seconds | 3 |

## Phases

**Phase 0 · before `mlops-pipeline` merges.** The four fixes below.

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

**Phase 3 · update only what changed, inside each part.** Once runs get slow.
- Scan and resolve cached per file, by git blob SHA.
- SZZ on new fix commits only.
- Done when: the Sunday rebuild still equals the updates (0 differences), and a typical update takes seconds.

## Fixes on `mlops-pipeline` before it merges

- [x] Add `.gitattributes` (LF line endings) and regenerate `dvc.lock` on Mac/Linux: Windows line endings change the hashes. Done: 13 code and config hashes were Windows-only; `dvc status` is clean on Mac. On Windows, refresh the checkout once so files come out LF (below)
- [x] Add `dvc-gs` to `requirements.txt` (for the `gcs` remote). Done: `dvc-gs==3.1.0`, `gcsfs==2026.10.1`; `dvc version` lists gs
- [x] Add `.DS_Store` and `Thumbs.db` to `.dvcignore`. Done: the raw-pull hashes now match on Mac and Windows
- [ ] Find the Windows-vs-Mac difference in `resolution` and `call_edge` (2,293 vs 2,288 edges). Needs both machines' files. The regenerated `dvc.lock` records the Mac values: `resolution`, `call_edge`, `blast_radius`, `stats.json` differ from Windows, and `run.jsonl` by design (`image_digest`)

On Windows, after pulling this, commit or stash your work first, then once: `git rm -r --cached -q . && git reset --hard`, so every file is checked out with LF.

## Next

Confirm #6–#13 and agree #14–#18 with Aniket, then this becomes one design record for the team.
