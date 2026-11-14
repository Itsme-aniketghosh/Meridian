# Final product runs: full log (Nov 14, 03:19–03:37 UTC)

Phase 5 of `packets/aniket-lead/README.md` §6: the champion push, the product on the real block, run 2, the fixture, the charts and the billing check. Each step has its command, what it printed and a PASS / FAIL / WARN / BLOCKED / SKIPPED verdict. The raw output of every command is in `logs/`, with local paths shortened to `<repo>`, `<scratch>` and `~`.

**Who, what and where:**
- **Run by:** Utkarsh, on his Mac (macOS, Apple silicon), with the repo's `.venv` (Python 3.11). Aniket asked him to take these runs (Nov 13).
- **Code:** `main` at `844a7214b203d1a5a2395be821a0c7e081931c60` (Merge #119), on branch `final-runs`. No code changed.
- **Blocks:** the product block is `bb6b5f4e32a4bee7` (`MERIDIAN_DATA=~/meridian-block3`). Run 2 is under `~/meridian-run2`. The charts come from `c5118b019d642a9b`. The blocks hold real names, so none of them is in git.
- **LLM guard:** `MERIDIAN_GCP_PROJECT` was unset for every product command. Any real model call would have stopped the run with "no GCP project", and `--strict-cache` stops it on a cache miss.

## Summary

| # | Step | Verdict |
|---|---|---|
| 1 | Champion to Artifact Registry | **BLOCKED**: 403 on Rochan's project |
| 2 | Product commands on bb6b5f4e (8 commands) | **PASS**: 0 LLM calls, $0; outputs identical to the committed ones |
| 3 | Run 2 | **PASS**: 0 errors, 9 warnings (8 as before, +1 expected); `test_run2` 4/4; new run_id `7b745b4430bbac75` |
| 4 | Fixture: `run_demo.py short` | **PASS**: 10/10 in 14 s |
| 5 | Charts | **PASS** for data charts (6 files updated) and bias charts (byte-identical). **SKIPPED**: sensitivity (needs a retrain) and llm_bias (waits on Chaitali) |
| 6 | Billing page | **BLOCKED**: same project as step 1; Utkarsh's own project checked instead, and it's clean |
| 7 | Golden md5s | not done, as asked: 2024's are in #119; `llm_bias.json` waits on Chaitali |

## Step 1: champion to Artifact Registry — BLOCKED

`model.toml [registry] project = "project-448b7a64-b5c5-43d3-893"` is Rochan's course-credit project. The signed-in account can't read it:
```
gcloud artifacts repositories list --project=project-448b7a64-b5c5-43d3-893 --location=us-east1
ERROR: (gcloud.artifacts.repositories.list) 403 PERMISSION_DENIED: Permission 'artifactregistry.repositories.list' denied
gcloud artifacts versions list ... --repository=meridian-models --package=defect-risk
ERROR: 403 PERMISSION_DENIED: Permission 'artifactregistry.versions.list' denied
```
`python -m model.registry --push` wasn't run, since it would fail at the same point. Pointing `[registry] project` at another project would change config on main, which was ruled out.

**Next:** Rochan runs `cd Model-Pipeline && python -m model.registry --push` and checks that version `85b4684ae33697e771ba4defa5cc4cb5618bcd6a` holds `model.joblib` with md5 `2a0c9cc656b43fc4e471c70223c4b4ca`. The alternative is for him to give Utkarsh's account Artifact Registry Writer and Billing Viewer on that project, so Utkarsh can run it. Locally, `registry/champion.json` already names that version and md5, and `md5 registry/model.joblib` = `2a0c9cc656b43fc4e471c70223c4b4ca`.

## Step 2: the product on bb6b5f4e32a4bee7 — PASS

LLM cache: 1,353 entries before and after. `git status` afterwards showed no tracked file changed by these commands.

| Command (`--run-id bb6b5f4e32a4bee7`) | Key output | Verdict |
|---|---|---|
| `meridian.context --out <scratch>` | 68 consumer tasks, 36 cut: files 127/367, sites 495/1,526; bug fixes covered 0.964 (random order 0.727). **Byte-identical to `reports/context_cut.json`** (md5 `60b507d7…`) | PASS |
| `meridian.diffs --strict-cache --out <scratch>` | 538 suggestion rows; Django 265 of 267 lines drafted (2 refused: not Python). drf 182/182 and dj-rest-auth 51/51 (style `path_if_plain`). 7 packages 0 drafted, `style_unknown`: no owner-migrated file at the parent (decision 15 waits). 0 by the LLM | PASS |
| `meridian.agents --quiet` (LLM off) | consumer, stall_reason and ticket_draft: 68 texts each, all templates; 41 consumer templates the gate flags; 0 skipped for naming a person | PASS |
| `meridian.orchestrator --llm --models gemini-flash --strict-cache --workers 8` | 68 stalled teams: 49 orchestrator, 19 rules (6 not the JSON asked for, 13 reason not in the facts); walkers ran for 20; 164 LLM texts, 60 templates; **0 calls, $0.0000**. Same as the #113 rerun | PASS |
| `meridian.auditor --out <scratch>` | stall-v0 champion, stall-v1 challenger, both Elo 1500 on 0 live matches (no users yet); 52 deprecation-marked functions, none with a caller in `call_edge` (gap G3) | PASS |
| `meridian.replay` (dry run; `--apply` was done earlier) | 19 of 21 pushes checked (2 over the scope limit), 7 alerts, all P9, median 0 per push (meets 05-test's ≤ 1). Same as the earlier `--apply` | PASS |
| `meridian.p9_precision --out <scratch>` | 60 Spark val pushes; P9 precision 0.333 on 45 alerts (Wilson 0.21–0.48), under the bar (≥ 30 alerts at ≥ 0.80); the table's lines × files > 2000: 634 flags, 0.358; 0 disagreements. **Byte-identical to `reports/p9_precision.json`** (md5 `d28e879a…`). 6 min 57 s | PASS |
| `meridian.views --path /` | home page JSON: owner, ticket and consumer links | PASS |

**Note:** `replay` and `p9_precision` (without `--apply`) still rewrite `out/bb6b5f4e32a4bee7/push_alert.jsonl` in the block. The block's sqlite isn't touched.

## Step 3: run 2 — PASS (with expected differences)

```
MERIDIAN_DATA=~/meridian-run2: collect --run-id new --config meridian.next.toml -> run_id=7b745b4430bbac75
history, scan, resolve, owners, join --prev-run c5118b019d642a9b, graph, validate --prev-run c5118b019d642a9b
1,787,418 rows in 30 tables · 0 errors · 9 warnings   (984 s total; history 863 s)
MERIDIAN_RUN2=~/meridian-run2/out/7b745b4430bbac75 python -m unittest tests.test_run2 -v   -> Ran 4 tests, OK
```
- **Why a new run_id (not `5bc7deda64fcfa0a`):** b87dece (gap 24) added `fix_message` to `meridian.next.toml`, and the run_id hashes the config. `test_run2`'s config check still passes.
- **Answer keys:** `test_run2` checks removed lines per repo (Django 265 of 267, packages 1,205 of 1,236), Django keeping only the legacy-alias test, and every removed row known at run 2's snapshot. All pass.
- **WARN, 9 validate warnings.** 8 match the first run 2: 289 invalid ticket dates, `resolution.resolved_to` 358 of 1,776 missing, and drift in `call_edge`, `call_edge.callee_repo`, `call_site`, `candidate`, `candidate.file_kind` and `resolution`. These are expected when every repo moves to its migration commit. The 9th is new: `file_bug_fixes` goes from 1,492 to 3,218 rows. That's gap 24 counting the packages' fixes from commit subjects, as intended. The alert was only logged: the Slack and SMTP variables were unset.
- **call_site: 3,029 rows vs 3,025.** The 4 extra are `django-import-export` `import_export/admin.py` lines 6, 93, 96 and 368, still present. Their `bug_fixes` is now 3 instead of 0 (gap 24), so join records them again at run 2's snapshot. No removal count changes.

## Step 4: the fixture — PASS
```
python run_demo.py short      (from Data-Pipeline/)
10 passed, 0 skipped, 0 failed   (14 s)
```
The 10 steps: environment, DAGs, DVC, the pipeline end to end (148 rows in 22 tables), fixture bytes = golden, schema and statistics, anomalies and alerts (exit 4, 4 anomalies, alert delivered to local Slack and SMTP), bias slices (14, 0 flagged), KNOWLEDGE-BASE product situations, and where to look next.

## Step 5: charts

| Charts | Command | Result | Verdict |
|---|---|---|---|
| Data-pipeline appendix (`Data-Pipeline/report/figures/`) | `python report/figures_scenarios.py --db ../out/c5118b019d642a9b/meridian.sqlite`; `python report/figures_quality.py ../out/c5118b019d642a9b`; `python report/gantt.py` | 21 + quality charts + gantt written. 5 PNGs and `scenarios.json` changed: see below | PASS, report text to update |
| Model bias (`reports/bias_*.png`, `bias_report.json`) | `python -m model.bias` | byte-identical to the committed files. MLflow run `d68d62de850b4d58902dead051b9ecdc` | PASS |
| 2024 (`reports/2024/*.png`) | made in the 2024 run (#119) | not rerun: 2024 is scored once | n/a |
| Sensitivity (`shap_*`, `sweep_*`, `hparam_*`, `permutation_importance`) | `python -m model.sensitivity` | exit 1: `reports/model.joblib not found: run python -m model.train --save-model first`. The judged model (candidate.json md5 `a817afba…`) isn't in git, and remaking it means a retrain on frozen code. The charts stay as committed on Nov 9 (4a5b153) | SKIPPED |
| `llm_bias_match.png` | `python -m model.llm_bias` | waits on Chaitali's result, as is `llm_bias.json` | SKIPPED |

**Why the data charts changed.** The committed `figures/` were drawn on Oct 25–26 from the v1 block `70dd1dabfed544b8`. `figures_scenarios.py` accepts only c5118 or v1, so the product block can't be drawn without a code change. Now they're drawn from c5118, the team run the report describes. Changed numbers in `scenarios.json` (timings ignored):
- **Mapped Spark tickets:** 36,138 → 36,198. This matches REPORT.md §2's v1 → c5118 note.
- **Mapped by stack trace:** 2,524 → 2,584 (`mapping`) and 4,435 → 4,529 (`spark_full`). Unmapped tickets: 23,353 → 23,293.
- **Open mapped tickets:** 621 → 633.
- **`file_bug_fixes_risky`:** 180 → 172.
- **P9:** flagged inducing 2,912 → 2,902; inducing 9,004 → 8,912.
- **`stall_test_gap`:** 196 → 200 package modules. **`Data-Pipeline/report/appendix-real-data.md:255` still says 196.**
- **Labels:** `spark_collisions` and `gitlab_blocks` `human_checked` 0 → 50.
- **One id change:** a 16-hex id in `stall_orphaned_owner` row 9 (`f6c71962…` → `8ed86ff6…`).

## Step 6: billing — BLOCKED (own project checked instead)
- **Rochan's project** (the registry's): the billing page and resources can't be seen from this account, for the same 403 as step 1. Someone with access needs to confirm that only `meridian-models` exists.
- **Utkarsh's project** (`project-4e32b2ab-…-b3a`, used for the Vertex LLM runs):
  - no storage buckets;
  - the Artifact Registry, Compute and Cloud SQL APIs aren't even enabled;
  - so nothing there bills except the Vertex calls already logged: gap 15, 247 calls, $1.10, plus the small gap 2 runs.
  - `gcloud billing projects describe` hit a rate limit (429) and wasn't retried.

## What's left for others
- **Rochan (or anyone with access):** the registry push and version check (step 1), and the billing page (step 6).
- **Report owners:**
  - `appendix-real-data.md:255`, 196 → 200;
  - any appendix figure caption that quotes v1 numbers (36,138 and the others above).
- **Chaitali:** `llm_bias.json` and its chart, then its golden md5.
- **Aniket:** whether the sensitivity charts need a `--save-model` retrain, or stay as the Nov 9 charts of the same model.

## Reruns after the gate fix (#122): Nov 14, 06:10–06:32 UTC

Aniket's go-ahead once #122 merged: rebuild `bb6b5f4e` and check its run_id and Utkarsh's three md5s, then the 0-call reruns on main `33ed598`, LLM bias first, then the agents and the orchestrator.

**Who, what and where:**
- **Run by:** Rochan, on a Mac (macOS, Apple silicon), with `Data-Pipeline/.venv` and `Model-Pipeline/.venv` (Python 3.11).
- **Code:** `main` at `33ed598` (Merge #122), a clean checkout, with Chaitali's `llm_bias_live.py` from #121's head (`ab96b22`). #121 merged during the rebuild (`6b466cd`). It added that same script and changed no other code, so these runs hold for main at `6b466cd` too.
- **Block:** `bb6b5f4e32a4bee7`, rebuilt under `MERIDIAN_DATA=~/meridian-block3` with the steps in `packets/utkarsh-data/gaps.md`. It holds real names, so it isn't in git, and it isn't published.
- **LLM guard:** as in step 2. No GCP project and no Slack or SMTP settings in the environment, and `--strict-cache` on.

| # | Step | Verdict |
|---|---|---|
| 8 | Rebuild `bb6b5f4e` | **PASS**: run_id `bb6b5f4e32a4bee7`, Utkarsh's three md5s match, validate 0 errors and 2 warnings |
| 9 | LLM bias on the live run | **PASS**: 205 LLM texts and 19 templates; consumer falls back 0 of 68 (was 41); `llm_bias.json` passes and is byte-identical to #121's |
| 10 | `meridian.agents --quiet` | **PASS**: 0 consumer templates the gate flags (was 41) |
| 11 | `meridian.orchestrator --strict-cache` | **PASS**: 205 LLM texts and 19 templates (was 164 and 60); **0 calls, $0.0000** |

LLM cache: 1,353 entries before and after. `git status` afterwards showed no tracked file changed. Raw output: `logs/13_rebuild_bb6b5f4e.log` to `logs/17_orchestrator.log`.

### Step 8: rebuild bb6b5f4e — PASS

`gaps.md`'s commands: `collect --run-id new`, then history, scan, resolve, owners, join, graph, validate and context. 20 minutes, 15.6 of them in history.
- **run_id:** collect printed `bb6b5f4e32a4bee7`.
- **md5s** of `out/bb6b5f4e32a4bee7/…`:
  - `file_bug_fixes.jsonl` `a98e5d40…`, `call_site.jsonl` `bf428a2f…` and `bug_inducing.jsonl` `682abebd…` match;
  - so does `commit_file.jsonl` (`8a6dc499…`), which `gaps.md` allows to differ.
- **validate:** 1,790,126 rows in 30 tables, 0 errors and 2 warnings. They're the same two as in `gaps.md`:
  - 289 Spark ticket dates in the years 0010–0012;
  - 24.0% of `call_site.bug_fixes` outside 0..0.

  The alert was only logged.
- **context:**
  - 68 consumer tasks, 36 cut (files 127 of 367, sites 495 of 1,526);
  - bug fixes covered 0.964, against 0.727 for a random order;
  - `reports/context_cut.json` is byte-identical to the committed one (`60b507d7…`).
- **No scan, resolve or SZZ cache:** this machine's data root holds only the repos, Jira and GitLab caches, so those three steps were computed from scratch. The md5s show they give the same bytes.

### Step 9: LLM bias on the live run — PASS

```
cd Data-Pipeline && MERIDIAN_DATA=~/meridian-block3 python ../Model-Pipeline/packets/chaitali-bias/llm_bias_live.py --run-id bb6b5f4e32a4bee7 --calls ../Model-Pipeline/reports/llm_bias_live/live_calls.jsonl
cd Model-Pipeline && python -m model.llm_bias --calls reports/llm_bias_live/live_calls.jsonl --ship gemini-flash --out reports/llm_bias_live
```
- **The replay** (18 s, 0 calls):
  - 68 stalls: 49 planned by the orchestrator and 19 by the rules, with walkers for 20;
  - **205 LLM texts and 19 templates:** consumer, stall_reason and ticket_draft are all LLM (68 each), and walk is 1 LLM text and 19 templates.
- **`model.llm_bias`:** passes, with 0 open flags. Diff match flags oscar (0.328, N = 247) and `(other)` (0.287, N = 488), both accepted, as before.
- **Against #121** (Chaitali's rerun after #122):
  - `llm_bias.json` (`3e6539f1…`) and `llm_bias_match.png` (`62ea68c2…`) are byte-identical;
  - `live_calls.jsonl` has the same 224 rows. Only their order (8 workers) and `latency_ms` (how long a cache read took here) differ.

### Step 10: agents, LLM off — PASS

`python -m meridian.agents --run-id bb6b5f4e32a4bee7 --config meridian.toml --out <scratch>/out/agents --quiet` (11 s):
- consumer, stall_reason and ticket_draft: 68 texts each, all templates;
- **0 templates the gate flags,** where step 2 had 41 consumer templates;
- 0 skipped for naming a person.

### Step 11: orchestrator — PASS

`python -m meridian.orchestrator --run-id bb6b5f4e32a4bee7 --config meridian.toml --llm --models gemini-flash --strict-cache --workers 8` (16 s):
- **The plan is the same as in step 2:**
  - 68 stalled teams: 49 orchestrator and 19 rules (6 not the JSON asked for, 13 reason not in the facts);
  - walkers for 20.
- **205 LLM texts and 19 templates,** where step 2 had 164 and 60. The 41 consumer texts the gate refused now pass on their cached replies.
- **0 calls, $0.0000.**

### The golden md5 and step 5's appendix numbers

- **`golden/2024.json`** adds `reports/llm_bias_live/llm_bias.json` (md5 `3e6539f1d717fd31679b3b49c6cecacd`, 25,282 bytes). It's the file #121 added, and main's copy has that md5.
- **`Data-Pipeline/report/appendix-real-data.md`** now quotes the redrawn charts:
  - 200 package modules with no test imports (was 196);
  - 633 open mapped tickets (was 621);
  - its "Which run" note says the charts are from c5118 since Nov 14.
