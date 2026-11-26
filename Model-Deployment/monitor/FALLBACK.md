# S10: the fallback-rate slice report (`monitor/fallback.py`)

Built **Wed Nov 25, 2026**: `python -m monitor.fallback` (PLAN §15, ↻4 Agents). The refresh job runs it after the push, on the new block, and posts to Slack when a slice is flagged. The rule is phase 2's (`model/llm_bias.py`, decision 9), unchanged: one text per (agent, facts_hash), judged at N ≥ 30 (`model.toml` [bias] min_n), flagged when the whole Wilson interval is more than 0.10 above overall. A flag in `[llm_bias] accepted` is reported but doesn't post.

| Data | LLM texts | Fell back | Judged slices | Flagged | Posts |
|---|---|---|---|---|---|
| Nov 14's live calls (`Model-Pipeline/reports/llm_bias_live/live_calls.jsonl`) | 224 | 19 (0.085) | 4 | none | no |
| Block `70dd1dab…`, the writer with no LLM (`--db`) | 156 | 0 | 3 | none | no |
| The #6921 bug, replayed (`tests/test_fallback.py`): `stall_reason` 41 of 68 | 204 | 41 (0.201) | 4 | `task=stall_reason` | yes |

Reports: `reports/fallback_live_nov14.json`, `reports/fallback_70dd.json`.

- **The rule misses small slices.** On Nov 14, 19 of 20 `walk` texts fell back. At N = 20 that slice isn't judged, so it doesn't post. It's listed in the report, and the rule stays phase 2's.
- **In the job, the writer runs with no LLM.** The image has no LLM client and no key, so the committed cache (`Data-Pipeline/llm_cache/`, now in the image) answers. A cache miss ships the template with no LLM attempt, which isn't a fallback and isn't counted. The cache was written for the multi-repo block, so a Spark-only block's new facts mostly miss, and the report will often say "no LLM texts". Measuring every refresh means calling Gemini Flash on the misses in the job. That needs a key, and it costs money, so it's for the team to decide.
- A failed report warns in the D5 line and the refresh still succeeds. It never changes the block, the D2 report or the retrain decision.
