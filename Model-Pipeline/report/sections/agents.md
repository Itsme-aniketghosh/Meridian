# The fact gate, the writer, the cache and the views

Everything a person reads in Meridian comes from the block's facts. The rules decide the facts; an LLM may only write them as prose. The fact gate enforces that: every number, path, line, ticket key, SHA, date and team name in a text must be one of its facts, and a text with any miss is replaced whole by its template. It is never patched (06-dead-ideas).
- The writer agent turns facts into text through the gate.
- The committed cache makes every rerun free and identical.
- The views show the result.

Every decision below had its test and its rule written before its run ([decisions.md](../../../docs/model/agents/decisions.md)), with one exception, noted under Limits.

This section covers the guideline row shared here (PLAN §12): the note on pretrained models, which asks for validation, bias detection and tracking for the LLMs. The writer's part of it is below. The gate is the validation, the LLM bias check slices its fallback rate, and every call is tracked in MLflow. The diff drafter's part is in the data section, Jev's in the model section, and the LLM bias results in the bias section.

## The fact gate

**What counts as a fact (decision 1).** The test set is 200 clean texts and 200 with one planted error from the fixture `acme/shop`: 40 each of a wrong number, a made-up path, a swapped team, a fake ticket key, and a real fact from another task (`tests/fact_gate_corpus.py`, seed 5). The rule: the smallest option that catches 100% with at most 5% false rejects (05-test).

| Option (N = 200 + 200) | Caught | False rejects |
|---|---:|---:|
| Numbers and dates only | 62 | 0 |
| Plus paths, SHAs and ticket keys | 160 | 0 |
| **Plus team names** | **200** | **10 (5.0%)** |

Only the team-name check catches the swapped teams, so it was picked. **Number matching (decision 2)**, on the same texts: exact matching rejects 40 of 200 clean texts ("1,526", "71%"), matching formats 24, and allowing rounding 10, all at 200 of 200 caught. Rounding was picked, and it applies only to a number written with `%` or decimals, so a bare whole number must equal a fact. `python -m tests.fact_gate_corpus` gives both tables.

**A team name used as a word (decision 11).** All 10 false rejects were one sentence, "across the platform", where `platform` is a word, not the team. Over 10 seeds (N = 2,000 + 2,000), the strict gate averaged 4.95% false rejects and went over 5% in 3 seeds. Reading a team name right after an article as a word, unless "team", "'s" or "owns" follows, caught **2,000 of 2,000 with 0 false rejects**, and is the gate's rule today. The third option, checking a team only next to "team", let 351 swapped teams through. CI checks the bar on all 10 seeds.

The lead's review found ways around the gate before any LLM ran: teams and people defaulting to empty, a percent matching a count ("4%" from 4 bug fixes), units (`14d`), upper-case SHAs. Each is now a test (`tests/test_fact_gate.py`, `Bypasses`). No person is ever a fact: a fact holding an email or a known name raises an error, and a text naming one always fails.

## The gate on real LLM text

We wrote the 400 texts ourselves, so decision 5 measured the gate on real model output: the writer's 204 tasks on block 70dd1dab, on four models (816 answers). Every text the gate rejected was read by hand next to its facts.

**The gate was too strict, not too loose.** It rejected 27 texts, and **23 of them were right (85%, against a 5% bar)**: 21 numbers and 2 paths that follow from the facts but aren't spelled out ("11 sites in config/urls.py", counted from the site list). None was a team. The 4 it caught were real errors: "44 other files" when there are 35, the date 2016-13-05, a wrong path, and a site list with wrong lines. Loosening the gate was ruled out, because decision 1 shows a looser gate lets a swapped team through. So each cause was fixed where it starts, one decision at a time, and re-gated from the cache at $0:

| Gemini 3.8 Flash on 70dd1dab, N = 204 | Pass rate | What changed |
|---|---:|---|
| As run (decision 5) | 80.4% | |
| Decision 5b | 98.0% | A git author named "Nothing" made "nothing blocks them" name a person. Function words aren't names. `used` may name inner fields |
| **Decision 5c** | **100.0% (204 / 204)** | File totals and "done when 0" are now facts in the writer's facts. The gate is unchanged |

Each step kept every text judged wrong rejected (7 after 5b, all 11 after 5c), and the planted-error sweep stayed at 2,000 of 2,000. The other models end at 96.1% (Scout), 92.2% (Gemma) and 91.7% (Maverick).

**On the live run's block (bb6b5f4e)**, the LLM bias check found consumer texts falling back 41 of 68 times. All 477 misses were gap 24's GitHub fix keys (`#6921`), read as numbers. #122 checks a `#N` key as a ticket, like `SPARK-N`. The review re-gated every cached answer on both builds of the 70dd snapshot (1,128 LLM texts and 612 templates): **consumer texts went from 27 to 68 passing, with 0 regressions in any task kind**. The new test fails with the fix reverted, and the 200 planted errors are still caught. The final reruns confirm it: **205 LLM texts and 19 templates** where there had been 164 and 60, at 0 calls (`reports/final/RUN-LOG.md`, steps 9–11).

## The writer and its model

The writer has four tasks: ticket rewrites, drafts for untracked call sites, stall reasons, and the consumer view's text. Its answer is JSON `{"text", "used"}`. Bad JSON or a gate miss moves one model up the ladder, then ships the template. A task whose facts name a person is never written.

**The model (gap 1, PR #77).** The 204 real tasks on block 70dd1dab ran on every model the project could reach, under GAPS.md's rule: the cheapest model within 2 points of the best pass rate.

| Model, N = 204 | Pass rate | Cost | Per accepted text | p95 latency |
|---|---:|---:|---:|---:|
| **Gemini 3.8 Flash** | **80.4%** | $0.48 | $0.0029 | 12 s |
| Llama 4 Maverick | 74.0% | $0.05 | $0.0004 | 2.3 s |
| Gemma 4 26B A4B | 65.2% | $0.03 | $0.0003 | 13.7 s |
| Llama 4 Scout | 54.4% | $0.04 | $0.0003 | 2.5 s |

No model was within 2 points of Gemini Flash, so it writes. The pick held after 5b and 5c: Gemini stayed the best by 3.9 points or more. Claude and Mistral weren't run. Both are Marketplace products on Vertex, which credit-only billing can't buy. The whole writer comparison cost under $1 and has run from the cache since.

**Gemini's thinking was set low.** At the default, Flash spent 3,064 tokens a task, and 3 of 10 replies ran out of room before the JSON.

## The cache

An answer is cached under sha256(model, prompt version, facts), one JSON file per entry, committed in `Data-Pipeline/llm_cache/`. The prompt version hashes the system prompt, the task prompt, the frame and the facts format, so an edit to any of them is a new key, and the cache never serves text written for other facts. Every run gates each cached reply again. A reply that names a person is never stored: its entry keeps a redacted marker, so a rerun still makes 0 calls, and a test scans the committed cache for names. With `--strict-cache`, a miss stops the run instead of shipping the template. A cache miss planted on a draft PR turned CI red on Linux and Windows (the bias section).

**It works:** the final product runs on bb6b5f4e made **0 LLM calls for $0**, with no GCP project set, and the cache held 1,353 entries before and after (`reports/final/RUN-LOG.md`). The live run itself, the only one that paid, was 247 calls for $1.10.

## Tracking and bias for the LLM

Every writer answer, cached or called, goes through `model.tracking.log_llm_call` into MLflow. Each record holds the agent, model, prompt version, tokens, cost, cache hit, the gate's verdict, the route and what shipped. Task, repo and team are recorded for slicing, never a person, and never the prompt or the text. A template that ships with no LLM attempt isn't a call and isn't logged, so the gate rates are the LLM's alone. The live run's 224 records are committed in `reports/llm_bias_live/live_calls.jsonl`. The LLM bias check slices the gate's fallback rate on them by task kind, repo and team. After #122, consumer texts fall back 0 of 68 and no slice is flagged (the bias section).

## The context builder, switched on (gap 16)

The context builder, from the bias section, keeps each team's riskiest files, the ones holding 90% of its bug fixes, and folds the rest into counts. Its rule, written first: switch it on if it keeps at least 90% of bug fixes and beats a random choice of the same number of files by 10 points. On bb6b5f4e it keeps **96.4% of bug fixes with 127 of 367 files (35%)**, against **72.7%** for random (N = 68 consumer tasks, 36 cut; `reports/context_cut.json`). Switching it on was this section's call, since it changes every consumer cache key, and #111 did it (decision 6 for gap 16).

The cut files are counted, never named ("3 call sites in 2 other files"), and the totals stay totals, so every text stays true. Before the PR, on the same snapshot ($0):
- The 136 tasks that aren't consumer tasks kept identical facts.
- All 68 consumer tasks add up.
- No template names a cut file.
- The gate flags no new template.

An independent review of every cache key found two side effects, both in the PR. The orchestrator's plans don't change. Its walkers now run on 2 more stalls, because `graph_walk` takes a team's first consumer task across every repo and symbol, which was already true for 25 of 68 stalls on main. Jev's keys change too, which costs only on a `--jev` run. The consumer facts are 46% shorter in characters (the bias section's preview), so each call is cheaper.

## The views

`python -m meridian.views` serves the four screens of 02-product (staff, owner, consumer, tickets) and the push page, read-only, on the standard library like `tests/dashboard.py` (decision 7). Each page shows `run_id`, `snapshot_at` and coverage. With the LLM off, a text is the cached reply if it passes the gate again, otherwise the template.

**Real where the tables have rows, the fixture otherwise (decision 8).** The measure is the rows each view's default page returns, logged on every start. On 70dd1dab: staff 251, owner 3, consumer 28, tickets 1, push 0. So the push page came from the fixture, under a banner that says so, until the data side's push rows on bb6b5f4e (#109).

An independent review found 13 problems before the lead's review. In one, 9 parallel clients got 27 wrong answers and 54 errors in 120 requests, because threads shared one connection. Each fix has a test, and **undoing any of the 13 fixes fails a test**. The no-writes test is an SQLite authorizer that refuses any write to the block. Startup on the real block took about 46 s, 35 s of it one query, `stall_competing_load`. That query lives in KNOWLEDGE-BASE.md, so its owner fixed it. The fix counts only the last 70 days' commits, giving the same rows on both real blocks in 9.1 s instead of 43.9 s (commit 0931add).

## Limits

- **Numbers with letters glued on** ("6921a", "SPARK-12a") weren't checked until Nov 15 (gap 26). Now such a token passes only if a fact spells it that way. None of the 1,346 cached LLM answers has one, so no result here changes.
- **Line lists without their path are rejected.** 14 right texts from Scout and Maverick write "formsets.py at lines 9, 344 and 351". A line is a fact only next to its path. Reading "path at lines a, b" is a gate change, left for later.
- **Other things the gate doesn't check:** a year alone when a fact date has that year, hashes under 7 characters, `1e3` and `1_526`, and spelling variants ("fulfilment").
- **The walkers' texts mostly fall back.** 19 of 20 walk texts become templates, each naming a team its facts don't hold. N = 20 is too few to judge, and the fix is in the walkers' facts, not the gate.
- **Consumer text has no suggestion row.** `suggestion.kind` has no value for it, and adding one is a contract change. It reaches the consumer view only.
- **Not measured as their own tests:** decisions 3 (the ladder's start), 4 (facts as JSON or bullets), 9 (a local model) and 5 in README's sense (what the template adds). The model comparison ran one model at a time, so the ladder's start was never compared. The prompt format stayed JSON.
- **Decisions 1 and 2's rules and results are in one commit** (5147d54), so git can't show the rule came first. Every later decision's rule was committed before its run.
- **Decision numbers:** decisions.md's decision 6 is gap 16's switch-on. README's decision 6 (cache scope) is answered by the cache section above, with no separate test.
