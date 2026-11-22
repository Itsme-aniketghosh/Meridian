# C1 walker facts: run log, Sat Nov 21 2026

GAPS 27 / PLAN §14 C1. Branch `utkarsh-c1-walker-facts`, cut from main `549d0b3`. Python 3.11.17 (`.venv`), macOS. Steps 1–5: **0 LLM calls, $0** (the re-gate's cache refuses writes). Step 6: **20 live calls, $0.0456**, approved by Utkarsh in chat on Nov 21.

## Summary

| # | What | Verdict |
|---|---|---|
| 1 | Find why 19 of 20 walk texts fall back | Done. **Not the cause gap 27 names** (see below) |
| 2 | Fix: the reason labels go into the walk's facts as values (`walkers.Walk.facts()`, `reasons`) | Done |
| 3 | Re-gate the cached walk replies against the new facts, $0 | **PASS: 1/20 → 20/20** |
| 4 | Nothing else moves: plan, other tasks, templates | PASS |
| 5 | Tests: Data-Pipeline suite, the gate's 200 + 200 pass bar, flake8 | PASS: 737 tests (27 skipped), 200 of 200 planted errors caught, flake8 clean |
| 6 | Live rerun of the 20 walk tasks (new cache keys) | **PASS: 20 calls, $0.0456; 224 LLM texts, 0 templates** (were 205 and 19) |
| 7 | Strict-cache replay after it | **PASS: 0 calls, $0**, the same 224 and 0 |

## 1. The cause

Gap 27 says each walk text "names a team its facts don't hold", and the fix it proposes is to put the visited teams into the facts. The cached replies show otherwise:

- **All 19 misses are the same token, `("team", "Test")`.** It comes from the reason label "Test gap". Django's synthetic team `tests` makes "test" and "tests" team names, and "Test" at the start of a phrase is read as a team ([`fact_gate.py`](../../../Data-Pipeline/meridian/fact_gate.py), decision 11's article rule).
- The gate already lets a reason label through when the facts hold it as a string. It quotes fact strings word for word ("text that quotes a fact word for word says nothing new"). That's why the 68 stall_reason texts pass with "Test gap": their facts are `{"reason": "Test gap"}`.
- In a walk's facts, the labels are only **keys**: `found = {team: {label: rows}}`. The gate reads values and never keys, so "Test gap" in a walk text wasn't a fact.
- **The teams were never missing.** Each walk visited only its stalled team, which is in `team` and `walked`. On this block every walk stays at one team: `blocked_by` has **0 rows** on `bb6b5f4e`, so there's no upstream edge to follow.

## 2. The fix

`walkers.Walk.facts()` adds `"reasons": sorted({label for each team's found})`, with its source. `found`, `walked`, `path` and the writer's template are unchanged, and **the gate isn't touched**.

## 3. Re-gate at $0

```
cd Data-Pipeline
PYTHONPATH=. python ../Model-Pipeline/reports/c1-walkers/regate.py ../Model-Pipeline/reports/c1-walkers/regate.json
```

2026-11-22T00:27:41Z, 19.7 s. Block `out/bb6b5f4e32a4bee7/meridian.sqlite` md5 `b4c12d94956b4715df6dfa979a9a22b8`. Its `file_bug_fixes`, `call_site`, `bug_inducing` and `commit_file` md5s (`a98e5d40`, `bf428a2f`, `682abebd`, `8a6dc499`) match Rochan's rebuild ([`final/RUN-LOG.md`](../final/RUN-LOG.md) step 8).

The script replays the orchestrator from the committed cache (LLM off, cache read-only). It looks up each walk task's cached `gemini-flash` reply under its **old** facts (without `reasons`), then gates it against the old and the new facts.

```
plans 68: {'orchestrator': 49, 'rules': 19}, walkers for 20
written {'ticket_draft': 68, 'stall_reason': 68, 'consumer': 68, 'walk': 20}; non-walk tasks by source {('ticket_draft', 'llm'): 68, ('stall_reason', 'llm'): 68, ('consumer', 'llm'): 68}
walk tasks 20, old reply cached 20
gate passes before (old facts) 1/20, after (new facts) 20/20
templates the gate flags: 0/20; new keys already cached: 0
misses after: Counter()
```

- **Before: 1/20**, the same as the phase 2 report (1 LLM text, 19 templates). contrib.gis is the one that passed.
- **After: 20/20.**
- The plan is the same as RUN-LOG step 11's: 49 planned by the orchestrator, 19 by the rules, walkers for 20. All 204 other texts still pass on their cached replies.
- Per task: [`regate.json`](regate.json).

## 4. Why a live rerun was needed

The LLM cache is keyed on the task's facts, and `reasons` changes them. The 20 walk tasks have **new keys with no cached reply** ("new keys already cached: 0"). Until those are filled:

- A `--strict-cache` replay of the orchestrator on `bb6b5f4e` (RUN-LOG step 11) stops at the first walk task with `CacheMiss`. No CI job runs it: `ci.yml` and `model.yml` don't call the orchestrator.
- Without `--strict-cache`, the walks ship their templates. The templates pass the gate (0 of 20 flagged).

The re-gate shows the old replies would pass under the new facts. The live rerun writes the cache entries under the new keys and measures what the model writes now:

```
cd Data-Pipeline && MERIDIAN_GCP_PROJECT=<Utkarsh's project> python -m meridian.orchestrator --run-id bb6b5f4e32a4bee7 --config meridian.toml --llm --models gemini-flash --workers 8
```

Every other task hits the cache. Expect about 20 calls, roughly $0.10 at #113's rate (247 calls, $1.10). After it, the phase 2 report's "205 LLM texts and 19 templates" and "walk is 1 LLM text and 19 templates" change, and so do gap 27's row, `docs/02-product.md`'s line and REPORT.md's known-issues line. They change in this PR, with the date.

## 5. Live rerun: PASS

Utkarsh's project (`project-4e32b2ab-…-b3a`, the one used for phase 2's Vertex runs), application-default credentials. Spend cap `--max-usd 1`.

```
cd Data-Pipeline
MERIDIAN_GCP_PROJECT=<Utkarsh's project> python -m meridian.orchestrator --run-id bb6b5f4e32a4bee7 --config meridian.toml --llm --models gemini-flash --workers 8 --max-usd 1
```

2026-11-22T00:30:34Z to 00:31:04Z, 30 s. Output in [`live.log`](live.log):

```
68 stalled teams planned: {'orchestrator': 49, 'rules': 19}; the graph walkers ran for 20 (their tool failed; gap 17)
  49 orchestrator
  6 refused: not the JSON asked for
  13 refused: the reason says something not in the facts
steps written: {'ticket_draft': 68, 'stall_reason': 68, 'consumer': 68, 'walk': 20}, 224 LLM texts, 0 templates
20 calls, $0.0456 (orchestrator $0.0000)
```

- **The 20 calls are the walk tasks** under their new keys. Every other task and the orchestrator's 49 plans came from the cache. The plan is the same as before, refusals included.
- **The cache went from 1,353 to 1,373 entries.** The 20 new files are all `gemini-flash`, prompt version `f8a1f6a831cb`, with no redacted reply and no email in any reply. They're committed with this PR, so every later replay is $0.
- **Walk texts: 20 of 20 pass the gate** (1 of 20 before). An example reply: "The walked teams are graphql-python/graphene-django. The path is empty. The reasons found at graphql-python/graphene-django are Never notified, Orphaned owner, and Test gap."

## 6. Strict-cache replay: PASS

```
python -m meridian.orchestrator --run-id bb6b5f4e32a4bee7 --config meridian.toml --llm --models gemini-flash --strict-cache --workers 8
```

19.4 s: the same plan, 224 LLM texts, 0 templates, **0 calls, $0.0000**.

## Numbers changed (Nov 21)

| Where | Was | Now |
|---|---|---|
| `Model-Pipeline/report/REPORT.md` §1, the product rerun row, the spend row, §11 | 205 LLM texts, 19 templates; 19 of 20 walk texts fall back | 224 LLM texts, 0 templates; all 20 pass; +20 calls, $0.05 |
| `Model-Pipeline/report/architecture.html` | 205 LLM texts; 19 of 20 fall back | 224; all 20 pass |
| `Model-Pipeline/GAPS.md` gap 27 | open | fixed, with the real cause |
| `docs/02-product.md` known issues | open | fixed |
| `Model-Deployment/PLAN.md` C1 | open | ✓ |

Left as written: the dated logs in `Model-Pipeline/packets/` and `reports/final/RUN-LOG.md`, which record what was true on their dates. Chaitali's LLM bias check (`reports/llm_bias_live/`) still counts the walks as 1 LLM text and 19 templates. Rerunning it from the cache is $0, and it's hers to rerun.

## 7. Tests

- `python -m unittest discover -s tests -t .` from `Data-Pipeline/`: Ran 737 tests in 63 s, OK (skipped=27). The error text in the output is printed by tests of failure paths.
- `tests.test_fact_gate`: `test_every_injected_error_is_caught` passes (200 of 200), and the corpus is unchanged.
- New in `tests/test_walkers.py`:
  - `reasons` is the labels of `found`.
  - A walk text quoting "Test gap" passes with `tests` among the known teams.
  - The same text fails on the old facts with exactly `("team", "Test")`.
  - A swapped team (`checkout`) still fails.
  - A walk that finds nothing has no reasons.
- `flake8 meridian dags tests run_demo.py check_csv.py labels` (CI's): clean.

## For the front sprint

The packet's Nov 27–28 item is "the demo's stalled-team story from the block (a cause upstream, found by the walkers)". **`bb6b5f4e` can't give one**: `blocked_by` is empty (0 rows; `call_edge` has 2,288, `chain` 43), so every walk stays at its own team. The fixture block (`tests/test_kb.py`) has one: fulfillment blocked by platform's test gap. Pick that, or find a block where `blocked_by` has rows, before Nov 27.
