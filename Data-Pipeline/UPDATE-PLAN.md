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
13. **Build in phases:**
    - Phase 1: on request + daily + weekly rebuild, all as a full recompute.
    - Phase 2: optional push-triggered runs.
    - Phase 3: update only what changed per part, once runs get slow.

## Contract changes (after the v1 freeze, Aniket's OK)

14. Repos follow their main branch, resolved to a commit at collect time.
15. `snapshot_at` moves forward to each run's pull time.
16. Collect pulls only what changed since the last run, from a stored cursor.
17. Schema: `call_site.effective_sha` (join already blames each line, so the SHA is there), plus the commit that introduced each `call_edge`.
18. Storage: one permanent mirror per repo, plus a small manifest per run.

## Fixes on `mlops-pipeline` before it merges

- [ ] Add `.gitattributes` (LF line endings) and regenerate `dvc.lock` on Mac/Linux: Windows line endings change the hashes.
- [ ] Add `dvc-gs` to `requirements.txt` (for the `gcs` remote).
- [ ] Add `.DS_Store` and `Thumbs.db` to `.dvcignore`.
- [ ] Find the Windows-vs-Mac difference in `resolution` and `call_edge` (2,293 vs 2,288 edges). Needs both machines' files.

## Next

Confirm #6–#13 and agree #14–#18 with Aniket, then this becomes one design record for the team.
