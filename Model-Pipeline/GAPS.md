# Gaps: what's open, how we close it, who owns it

Written Nov 11. Scope is the architecture only: nothing here deploys anything. Nothing here retrains the model or touches 2024 either. The one exception is the P9 rule (gap 4), which has to be written down before the 2024 run.

Everyone works in parallel. The one wait is the LLM client (gap 1), about an hour. Until it lands, build against the fake `llm` the tests already use.

## The gaps

| # | Problem | Fix | Owner | Done when | When |
|---|---|---|---|---|---|
| 1 | **The LLM has never run live.** The code has no LLM client, only the fakes in tests. LLM bias has N = 0 | One client function `llm(model, prompt)` that returns reply, tokens and cost, as the fake does. The agents already take it ([agents.py](../Data-Pipeline/meridian/agents.py) `write`, `diffs.py`). Then run the writer on the real block's 204 tasks and commit the cache | **Vishwa** (client, writer). **Rochan** sets up model access first | Client merged by midday. Per model: gate pass rate, valid JSON, cost per accepted text, p95 latency. The gate's wrong-rejection rate on real text (vishwa decision 5) | Nov 11 |
| 2 | **Only Claude models were ever considered.** 04-architecture names sizes (small, mid, large), not vendors, and nobody compared providers | Run every agent on the same tasks with Claude, Gemini, the open models (Llama, Mistral, Gemma) and Phi-4-mini locally. Pass models through `ladder=`, so `LADDER`, and every run_id with it, stays as it is until the winners are picked | **Vishwa** writer, **Utkarsh** diffs, **Rachna** Jev | Each agent's ladder is picked by the rule below | Nov 11–12 |
| 3 | **Package diffs at 49.5%** (bar 90%). Each migration chose its own `url()` style, so no codemod rule gets close | Run the LLM path on the 624 package lines the codemod didn't match. Diffs that pass become same-shape examples, so the codemod learns each owner's style | **Utkarsh** | Match rate against the answer key per model. `diffs.md` decision 11 updated | Nov 11–12 |
| 4 | **P9 doesn't use the model.** The best cutoff reaches precision 0.70 on 30 val alerts, against a 0.80 bar | On val only: alert when the model **and** lines × files both flag a commit (rachna decision 15) | **Rachna** | It reaches 0.80 on 30 alerts and is written down before the 2024 run. Otherwise lines × files stays and the report says 0.70 | Nov 11, before 2024 |
| 5 | **No real push rows.** `/push` shows the fixture | Replay the 21 migration commits through the push check (decision 15) | **Rachna** | Real `push_alert` rows. `/push` drops the fixture banner | Nov 11–12 |
| 6 | **The model misses small commits.** Recall 0 of 51 on Q2, 5 size flags accepted | No retrain. SZZ blames the lines a fix changed, so small commits may just be thinly labelled. An LLM audits 50 Q2 labels ("is this commit really bug-inducing?"), and 20 of its answers get hand-checked | **Chaitali** | A label-noise estimate for Q2 in the bias section. It's never used as training labels | Nov 12–13 |
| 7 | **LLM bias has nothing to judge** | Run `llm_bias` on gap 1's texts once they land | **Chaitali** | `llm_bias.json` with N > 0 | Nov 12 |
| 8 | **The champion isn't in git.** CI made it, but `Model-Pipeline/registry/` is empty, so CI and P9 can't see it | Commit the CI champion's `champion.json` and `model.joblib` to `registry/` | **Rochan** | P9 reads the committed champion | Nov 11 |
| 9 | **The auditor's promotion guard is loose.** It promotes a no-better policy by luck 87–96% of the time in simulation | Sweep stricter guards (more matches, a bigger Elo margin) in the simulation and keep the loosest one with ≤ 5% false promotions | **Rochan** | Guard settings and the false-promotion rate in his decisions | Nov 12 if there's time, else later |
| 10 | **Views take ~96 s to start** | Time each query at startup and build the slow ones into tables when the block is written | **Vishwa** | Startup under 10 s | After Nov 15. For the demo, start them early |
| 11 | **No spend limit on the LLM runs** | A $150 budget alert. Review and merge each owner's PR | **Aniket** | Alert set before gap 1 starts. Go or no-go for 2024 after gap 4 | Nov 11 |

## The model comparison

Models: `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`, Gemini Flash, Gemini Pro, Mistral Medium, Llama 4 Maverick, Llama 4 Scout, Gemma 4 31B, and Phi-4-mini (local through Ollama `phi4-mini`, Jev only). Use pay-per-token models only: anything that needs its own GPU server is out.

| Agent | Tasks | Scored on | Rule, written before the run |
|---|---|---|---|
| Writer | 204 tasks on the real block | Gate pass rate, valid JSON, cost per accepted text, p95 latency | The cheapest model within 2 points of the best pass rate wins its rung of the ladder |
| Diff drafter | 624 package lines | Match with the real after-line, and it applies, parses and resolves | Same rule. The bar stays 90% |
| Jev | 60 labelled tasks: 30 to write the prompt, 30 held out (decision 19) | Correct picks on the held-out 30, cost, p95 latency on a CPU runner | The cheapest model within 3 of the 30 tasks of the best (decisions 13, 14, 21). Phi or a 7–8B local model needs p95 inside CI's budget |

Equal results go to the open-weights model.

## Cost

About **$70** in total, with the alert at $150.

| Owner | Work | Est. |
|---|---|---:|
| Utkarsh | diffs, every model | $49 |
| Vishwa | writer, every model | $16 |
| Rachna | Jev | $3 |
| Chaitali | Q2 audit, one strong model | $2 |
| Rochan, Aniket | | $0 |

Assumed per task: writer 3,000 tokens in and 800 out, diff 3,000 and 600, Jev 2,000 and 200, audit 6,000 and 500, plus 30% for retries. Prices are list rates as of Nov 11. Claude: haiku $0.10 / $0.50, sonnet $2 / $10, opus $4 / $20 per 1M tokens in / out. The Gemini Flash price is introductory and ends Dec 31. The open-model rates come from third-party listings, so check them before spending. Each owner measures 10 real prompts before the full run, and runs the large models only on tasks the small ones fail.
