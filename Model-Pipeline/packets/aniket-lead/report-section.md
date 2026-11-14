# Integration, faults, the go and the final runs (Aniket)

Until the code freeze I reviewed and merged. After it I put the model and the product together, broke them on purpose, gave the go for 2024, and had the final runs made. I also built the orchestrator and wired in the graph walkers (GAPS 15, 17). Every number below has its N and the file it comes from.

## Which block runs what

Two builds of the same snapshot (2026-10-01T01:23:03Z, the same pinned commits and tickets) were in play, and one rule decided which serves what (GAPS.md, "Which block", Nov 13):
- **The model** (feature table, champion, 2024) stays on `c5118b019d642a9b`, the team run that the whole model pipeline was tuned and judged on.
- **The product** (context builder, agents, orchestrator, push rows, final product runs) runs on `bb6b5f4e32a4bee7`, Utkarsh's build with gap 24's package bug fixes.

Against c5118, 21 of 25 tables are the same bytes, `bug_inducing` and Spark's bug-fix rows among them, so moving the model would have changed nothing and risked the night before 2024. Nobody runs `publish`, so `published/latest.json` stays on c5118 and `model.refresh` never starts a retrain. Both blocks hold real names, so neither is in git.

## Simulated faults

Phase 1 broke the pipeline on purpose, and the report was stronger for it. Phase 2 does the same: 21 faults, each a script in `tests/faults/` with its expected result written in the script before it ran. `python -m tests.faults.table` runs them all and prints this table. It ran on frozen main (`freeze-nov13`, 978af89) for the go, and again on main `57adef0` on Nov 14 (91 s): **21 of 21 behave.**

| # | Fault | Expected | Result |
|---|---|---|---|
| 1 | Shuffled labels | PR-AUC near the positive rate; gate fails; Slack alert | Behaves (1 test) |
| 2 | A size-only model | Gate fails, or passes only if it really beats lines × files | Behaves (2) |
| 3 | A feature column missing | `train.py` fails with a clear message | Behaves (1) |
| 4 | A column's type changed | `data.py` or `train.py` refuses it | Behaves (2) |
| 5 | Feature table from the wrong `run_id` | Refused, or tagged so it can't be mistaken | Behaves (2) |
| 6 | The same config twice | Same predictions, same metrics | Behaves (1) |
| 7 | A model worse than the champion | Registered, not promoted, not pushed | Behaves (1) |
| 8 | Better only within seed noise | Not promoted | Behaves (2) |
| 9 | Bias fails, PR-AUC better | Gate fails | Behaves (1) |
| 10 | Rollback | `champion` back to `previous`, locally and in Artifact Registry | Behaves (1) |
| 11 | No GCP credentials | Everything runs; the push is skipped and says so | Behaves (2) |
| 12 | Slack webhook broken | Gate still decides; the error is logged | Behaves (2) |
| 13 | A PR from a fork | No secrets; stops before the push | Behaves (3) |
| 14 | `--split test` before the go | Refused | Behaves (3) |
| 15 | LLM off (no key, empty cache) | Every view renders with templates | Behaves (1) |
| 16 | An LLM text with a made-up number | The gate replaces it with the template; the fallback rate counts it | Behaves (1) |
| 17 | Jev down | Rules pick; the run completes | Behaves (2) |
| 18 | A cache entry deleted | That one task calls the LLM again; everything else is a hit | Behaves (1) |
| 19 | A fact containing an email | Refused before any prompt is built | Behaves (1) |
| 20 | Auditor replay with a future call-site row | The row is ignored | Behaves (2) |
| 21 | A broken champion file in the push check | P9 falls back to lines × files and says so | Behaves (1) |

Two didn't behave the first time, and both were fixed before the freeze:
- **Fault 4.** `train.py` now refuses any column that isn't a number, and names it.
- **Fault 17.** An unreachable Jev now falls back to the rules pick.

Fault 20's one known gap is stated in its script: ticket maps learned later than the stall aren't filtered by `known_at` (auditor decisions, Nov 10). Faults 7, 8, 10 and 20 are Rochan's; the rest are split between PR #100 (data side) and PR #102 (model side).

## The go, and 2024 once

The bar for the go was set in advance: all 21 faults behave, CI is green on main, and the pass bars are unchanged since Nov 6. All three held. The code froze on Nov 13 at about 8 pm, tagged `freeze-nov13` on 978af89, and I gave the go.

Rochan couldn't be reached, so Utkarsh ran 2024 on my go-ahead, with me watching live (`reports/2024/RUN-LOG.md`):
- **When and where:** Nov 14 at 01:57 UTC, on branch `run-2024` at `df12728`. The only change from the freeze is `test_allowed = true`.
- **Before scoring:** the feature table's md5 (`9c0ab220…`) and the champion's (`2a0c9cc6…`) were checked against the frozen ones, and the champion's val PR-AUC of 0.4067 was confirmed before any 2024 row was read.
- **One run:** `evaluate` ran once. Its outputs' md5s are in `golden/2024.json`, and `test_allowed` is back to `false` on main.

The result is in Rachna's section. **The model ships:** on N = 3,762 commits, forest PR-AUC 0.268 against lines × files' 0.214, paired +0.054 [+0.028, +0.081].

## The final runs

Utkarsh made the first final runs on Nov 14, 03:19–03:37 UTC, on main `844a721` with no code changed. Rochan made the reruns after the gate fix, 06:10–06:32 UTC on `33ed598`. Every command, its output and its verdict are in `reports/final/RUN-LOG.md`, with raw logs in `reports/final/logs/`. One guard covered every product command: `MERIDIAN_GCP_PROJECT` was unset and `--strict-cache` was on, so any real model call would have stopped the run.

| Run | Result |
|---|---|
| Product on bb6b5f4e, 8 commands (context, diffs, agents, orchestrator, auditor, replay, P9 precision, views) | **0 LLM calls, $0.** LLM cache 1,353 entries before and after. `context_cut.json` and `p9_precision.json` byte-identical to the committed files |
| Run 2 (`7b745b4430bbac75`) | 1,787,418 rows in 30 tables, 0 errors. `test_run2` 4 of 4. 9 warnings: 8 as in the first run 2, and 1 expected, from gap 24's new package bug fixes. New run_id because gap 24 changed the run-2 config, which the run_id hashes |
| Fixture, `run_demo.py short` | 10 of 10 steps in 14 s |
| Rebuild of bb6b5f4e (Rochan) | Same run_id; Utkarsh's three md5s match; validate 0 errors, 2 known warnings |
| Champion in Artifact Registry | Pushed by Rochan: version `85b4684…` holds `model.joblib` md5 `2a0c9cc6…`, the committed champion |
| Billing | Rochan's project holds only `meridian-models` (Rochan, Nov 14). Utkarsh's project, used for the Vertex LLM runs, has no storage buckets, and its Artifact Registry, Compute and Cloud SQL APIs aren't enabled |
| Charts | Data-pipeline appendix redrawn from c5118 (it had been drawn from v1). Bias charts byte-identical. 2024 charts made once in the 2024 run |

**Golden md5s** of the feature table, the champion, 2024's metrics and scores, the bias report and both LLM bias files are in `golden/2024.json`, as phase 1 did.

## The orchestrator and the graph walkers (GAPS 15, 17)

**What the orchestrator does.** For each stalled team it reads the stall's own reasons and makes a plan:
- It works through one closed list: identify the problems, pick steps from a menu of 4, finish. It always ends.
- A planner answer that isn't the JSON asked for, or that says something not in the facts, is replaced by the rules plan.
- It runs per stalled ticket, never per push.

**When the walkers run.** When the context builder gives nothing usable, the graph walkers query the code graph directly. Code decides that the tool failed, never the model: the tool errors or times out, gives nothing usable, or its text fails the fact gate twice. The walkers then run only the named KNOWLEDGE-BASE.md queries, never SQL written on the fly, and follow `blocked_by` to the teams upstream. Their limits:
- the block is opened read-only;
- each query is capped in time and rows;
- each stall is capped at 3 teams, 2 hops and 24 queries;
- every result is cached by run_id.

On the fixture, with the context builder made to fail, they go from fulfillment to platform and find platform's test gap.

**The live run** (Utkarsh, Nov 13, block bb6b5f4e, `packets/utkarsh-data/gaps.md`). Gemini 3.1 Pro planned and Gemini 3.8 Flash wrote, the writer comparison's pick (Claude can't be bought on credit-only billing). In all, 247 calls cost $1.10 ($0.66 of it the orchestrator) and took 4.7 minutes:
- **Plans:** 68 stalled teams, 49 planned by the model and 19 by the rules. Of those 19, 6 answers weren't the JSON asked for, and 13 gave a reason not in the facts.
- **Walkers:** they ran for 20 stalls.
- **Texts:** 164 LLM texts and 60 templates.
- **Rerun:** from the committed cache with no GCP access, it makes **0 calls** and gives the same plans and texts.

**The gate fix after the freeze (#122).** Chaitali's LLM bias check found that consumer texts fell back to templates 41 of 68 times. Every one cited one of gap 24's GitHub fix keys (`#6921`), and the gate read the key as a number the facts don't hold. I fixed it after 2024, since the gate isn't on the model's path:
- **The fix:** a `#N` key is now checked as a ticket, like `SPARK-N`. A key the facts don't hold, its digits written as a count, and a count written as a key are all still refused.
- **Re-gating the cache** ($0): Vishwa re-gated every cached answer, and consumer texts went from 27 to 68 passing, with 0 regressions and 200 of 200 planted errors still caught.
- **0-call reruns:** Rochan's orchestrator rerun writes 205 LLM texts and 19 templates, against 164 and 60 before. Chaitali's consumer rate went to 0 of 68 fallbacks, byte for byte the same in both their reruns.

## Limits, stated plainly

- **The walkers' texts mostly fall back.** 19 of the 20 walk texts become templates, each because it names a team the gate doesn't find in its facts. With N = 20 that can't be judged, but a walker's finding reaches the team today as a template, not prose.
- **Two gaps in the gate, both left for after the freeze:**
  - a number with letters glued on (`6921a`) isn't checked (on main before #122 too);
  - 14 correct line lists written without their path still fail (gap 23).
- **The sensitivity charts** (SHAP, sweeps, permutation importance) were drawn on Nov 9 from forest `a817afba` (val 0.4058), not from the champion file (0.4067). Redrawing them needs a retrain on frozen code, which wasn't done.
- **The auditor has no live matches yet.** Its champion and challenger both sit at Elo 1500, because the product has no users.

## Next

- Phase 3 deploys the product, which starts the auditor's live matches.
- Make the walkers' facts carry the team names they write, so their texts can pass the gate.
- Fix `6921a` and gap 23's path-and-lines in the gate, each with planted-error tests like #122's.
