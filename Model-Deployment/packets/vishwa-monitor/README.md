# Vishwa: drift and decay, and the product front

> Decide, by rules written first, when the model needs retraining. Then build what people see at the ML Expo on Dec 8: the views on Cloud Run and a front that shows Meridian working, live.

| | |
|---|---|
| Steps | S5 drift and decay · S9 views on Cloud Run · S11 the product front (with Rachna) · S10 fallback-rate report. About 4.5 days. S6 moved to Rochan; C2 moves after the expo |
| Code | `Model-Deployment/monitor/`, `Data-Pipeline/meridian/views.py` and the views' image, the front's pages |
| Waits on | nothing for S5 (it starts on the 2025–26 commits). Rochan's Cloud Run service style (Nov 22) for S9. Rachna's `/score` (Nov 22) for "score a push" |
| Hands to | Utkarsh: the D2 report and its `retrain` flag. Rochan: drift metrics for the dashboard. Aniket: the drift result, the front, the demo stories |

```mermaid
flowchart LR
  data["2025–26 commits, new blocks"] --> me["Vishwa"]
  rachna["Rachna: /score"] --> me
  me --> d2["D2 report, retrain flag: Utkarsh"]
  me --> views["views + front on Cloud Run: everyone, the expo"]
  me --> demo["demo stories: Aniket"]
```

| Date | Due |
|---|---|
| Thu Nov 19 | **S5 drift on the 7,238 commits from 2025–26** against train. Recorded before tuning ✓ (#139) |
| Fri Nov 20 | Thresholds checked on real data (PLAN §5), signed by the team, on val 2023 only |
| Sat Nov 21 | Decay on the newest matured window against lines × files. The D2 report's shape |
| Sun Nov 22 | S9: the views' image; a test that no person's name renders |
| Mon Nov 23 | `--simulate` (every commit 10× larger) crosses the threshold. Check-in |
| Tue Nov 24 | **S9 views deployed on Cloud Run**, read-only, from the GCS block |
| Wed Nov 25 | S11: the front page with its three doors |
| Thu Nov 26 | Integration |
| Fri Nov 27 | S11: the live system panel (model in production, last deploy, last drift report, Slack, Monitoring link). S10: fallback-rate report |
| Sat Nov 28 | S11: polish on a projector and a laptop; the demo stories wired with Aniket |
| Sun Nov 29 | S5 decay final; the front's no-names test on the real block |
| Sat Dec 5, Mon Dec 7 | Expo rehearsals |
| **Tue Dec 8** | **ML Expo** |

## 1. Drift and decay (S5)

- [ ] Drift: PSI per feature and the drift tests, the last 30 days' commits against train. Flag: PSI ≥ 0.2 on `la`, `nf`, `prior_bug_fixes`, `nd` or `ld`, or ≥ 50% of features drifted
- [ ] Decay: the newest calendar quarter whose 365-day window has closed with ≥ 30 positives; model vs lines × files, paired bootstrap. Flag: interval lower bound < 0, or PR-AUC > 0.05 under the last window
- [ ] D2 report to GCS (D2's exact field names); custom metrics for Rochan's dashboard
- [ ] Tests: a planted shift flags, a clean sample doesn't
- [ ] With Utkarsh: is the drop in `prior_bug_fixes` real, or fixes not yet found at the snapshot?

## 2. The views on Cloud Run (S9)

- [ ] phase 2's `meridian.views` in an image, reading the block from the private bucket, read-only
- [ ] **No person's name renders on any page**: a test over every page on the real block (the block has real names)
- [ ] Public, max 2 instances, like the endpoint; one warm instance for the expo window (Rochan sets it)

## 3. The product front (S11, PLAN §16)

- [ ] **Front page:** one line on what Meridian does, the run_id and snapshot, the model version, three doors
- [ ] **A stalled team:** stall reasons (auditor-ranked), the orchestrator's plan, the writer's text with an LLM or template badge, what the walkers found upstream
- [ ] **Score a push:** Rachna's page, calling `/score`
- [ ] **The system, live:** the model in production, last deploy, last D2 report and why it did or didn't retrain, Slack's last messages, the Monitoring link, the "what broke and how we fixed it" board (Aniket's text)
- [ ] Standard-library HTML, as the views are today; one look on every page; readable on a projector

## 4. Fallback-rate report (S10)

- [ ] The LLM bias slice report on every refresh, posting to Slack when a slice is flagged

## Done when

- [ ] The 2025–26 drift result is recorded, and the demo's trigger is decided from it
- [ ] A planted shift retrains; a clean week doesn't
- [ ] The views and the front run on Cloud Run with no name on any page
- [ ] Every door works in rehearsal 1 (Dec 5)
- [ ] C2 (the gate's line lists): after the expo
