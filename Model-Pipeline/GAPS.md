# Gaps: what's open, how we close it, who owns it

Written Nov 11. Scope is the architecture only: nothing here deploys anything. Nothing here retrains the model or touches 2024 either. The one exception is the P9 rule (gap 4), which has to be written down before the 2024 run.

**Deadline: everything is done by the end of Thu Nov 12.** Whatever isn't goes into the report as an open gap. Everyone works in parallel. The one wait is the LLM client (gap 1), about an hour. Until it lands, build against the fake `llm` the tests already use.

## LLM first, then switch to ML once it clears a high bar

Where a trained model isn't good enough yet, an LLM does the job now, and every answer it gives is checked and saved. The saved answers that pass the checks become training examples for the cheaper model. Once the cheaper model clears the bar, we switch to it, and the LLM stays as the fallback for the cases it can't handle.

| Component | Now | Switch to | The bar for switching | Fallback after the switch |
|---|---|---|---|---|---|
| Diff drafter (packages) | LLM on the lines the codemod doesn't match | The codemod, learned from the LLM diffs that passed | ≥ 90% match with the real after-line, on repos the codemod didn't learn from | LLM for lines the codemod refuses |
| Jev (router) | The hosted model that wins the comparison | Phi-4-mini locally, or a small classifier trained on the saved picks | Within 3 of the 30 held-out tasks of the hosted model, with p95 inside CI's budget | Hosted model, then the rules |
| Writer | LLM, with a template when the gate rejects | No ML switch: writing text stays an LLM job. We switch down to the cheapest model that passes | Within 2 points of the best gate pass rate | Template |
| P9 defect risk | **Lines × files, not an LLM** | The champion model | Precision ≥ 0.80 on 30 alerts, and beats lines × files with its interval above 0 | Lines × files |

Every switch follows four rules:
1. The bar is written before anything is measured.
2. It's measured on data the cheaper model never saw.
3. It's checked again on every run, and if the model drops below the bar we switch back.
4. The report shows both numbers.

Why P9 never uses an LLM:
- It runs on every push, and an LLM would cost money on each one.
- Its answer would change from run to run.
- It would be deciding a fact (is this commit risky?), and in this architecture LLMs write text and code, never facts ([06-dead-ideas](../docs/06-dead-ideas.md)).

The same goes for the defect labels: the LLM may audit them (gap 6), but it never writes them.

## The gaps

✓ done · ◐ in progress · blank: not started. Updated Nov 11.

| # | Done | Problem | Fix | Owner | Done when | When |
|---|---|---|---|---|---|---|
| 1 | ✓ | **The LLM has never run live.** The code has no LLM client, only the fakes in tests. LLM bias has N = 0 | One client function `llm(model, prompt)` that returns reply, tokens and cost, as the fake does. The agents already take it ([agents.py](../Data-Pipeline/meridian/agents.py) `write`, `diffs.py`). Then run the writer on the real block's 204 tasks and commit the cache | **Vishwa** (client, writer). **Rochan** sets up model access first | Client merged by midday. Per model: gate pass rate, valid JSON, cost per accepted text, p95 latency. The gate's wrong-rejection rate on real text (vishwa decision 5). Client merged (PR #71). Live run merged (PR #77): 204 real tasks on 4 models, 816 answers cached. The gate wrongly rejects 23 of 27 real texts (85%, bar 5%): gap 23 | Nov 11 |
| 2 | ◐ writer done | **Only Claude models were ever considered.** 04-architecture names sizes (small, mid, large), not vendors, and nobody compared providers | Run every agent on the same tasks with Claude, Gemini, the open models (Llama, Mistral, Gemma) and Phi-4-mini locally. Pass models through `ladder=`, so `LADDER`, and every run_id with it, stays as it is until the winners are picked | **Vishwa** writer, **Utkarsh** diffs, **Rachna** Jev | Each agent's ladder is picked by the rule below. `agents --llm --models` and `writer_compare.py` merged (PR #71). Writer picked (PR #77): **Gemini 3.8 Flash**, 80.4% pass, $0.48 for 204 tasks (Llama 4 Maverick 74.0%, Gemma 4 65.2%, Llama 4 Scout 54.4%). Claude and Mistral can't run: they're Marketplace products, which our credit-only billing can't buy. Diffs and Jev still to compare | Nov 11–12 |
| 3 | ◐ 73.2% | **Package diffs below the 90% bar.** 49.5% with one `url()` style for everyone | Learn each owner's style from their own lines and draft whole statements (decisions 13–15, PR #67). The LLM path can try only the 20 lines the codemod refuses, at most 1.6 points, so it isn't what closes the bar | **Utkarsh** | 90% on the files the drafter wasn't shown. Now 73.2% (655 of 895) | Nov 12 |
| 4 | ✓ | **P9 doesn't use the model.** The best cutoff reaches precision 0.70 on 30 val alerts, against a 0.80 bar | On val only: alert when the model **and** lines × files both flag a commit (rachna decision 15) | **Rachna** | It reaches 0.80 on 30 alerts and is written down before the 2024 run. Otherwise lines × files stays and the report says 0.70. Result (PR #73): the AND rule reaches 0.619 on 42 val alerts, so it isn't adopted and P9 stays lines × files | Nov 11, before 2024 |
| 5 |  | **No real push rows.** `/push` shows the fixture | Replay the 21 migration commits through the push check (decision 15) | **Rachna** | Real `push_alert` rows. `/push` drops the fixture banner | Nov 11–12 |
| 6 | ◐ script | **The model misses small commits.** Recall 0 of 51 on Q2, 5 size flags accepted | No retrain. SZZ blames the lines a fix changed, so small commits may just be thinly labelled. An LLM audits 50 Q2 labels ("is this commit really bug-inducing?"), and 20 of its answers get hand-checked | **Chaitali** | A label-noise estimate for Q2 in the bias section. It's never used as training labels. Script and blind 20-commit sheet merged (PR #78, decision 11); next the hand check, then the run. Run it on Gemini: Claude isn't reachable on our billing | Nov 12 |
| 7 |  | **LLM bias has nothing to judge** | Run `llm_bias` on gap 1's texts once they land (they have: PR #77's cache) | **Chaitali** | `llm_bias.json` with N > 0 | Nov 12 |
| 8 | ✓ | **The champion isn't in git.** CI made it, but `Model-Pipeline/registry/` is empty, so CI and P9 can't see it | Commit the CI champion's `champion.json` and `model.joblib` to `registry/` | **Rochan** | P9 reads the committed champion. Done (PR #74); the gate also re-scores a champion judged on another table (decision 7) | Nov 11 |
| 9 | ✓ | **The auditor's promotion guard is loose.** It promotes a no-better policy by luck 87–96% of the time in simulation | Sweep stricter guards (more matches, a bigger Elo margin) in the simulation and keep the loosest one with ≤ 5% false promotions | **Rochan** | Guard settings and the false-promotion rate in his decisions. Done (PR #75, decision 12): a luck test on top of 04 §9's guard, 3.7% / 4.0% luck promotions within 201 / 600 matches (was 87% / 96%); a policy 200 Elo better is still promoted, at match 69 instead of 45 | Nov 12 |
| 10 | ✓ | **Views take ~96 s to start** | Time each query at startup and build the slow ones into tables when the block is written | **Vishwa** | Startup under 10 s. Done (PR #72): six indexes, 16.8 s → 7.0 s. Old blocks: `writer --index` | Nov 12 |
| 11 | ◐ deferred | **No spend limit on the LLM runs** | For now everyone runs on their own GCP credits, which cap what they can spend (credit-only, no card), and the client caps each run at $25 (PR #71). A shared budget alert comes later | **Aniket** | A budget alert once we share a billing account | Later |
| 15 |  | **No orchestrator.** The agent cycle runs with the rules in its place | A best-in-class model (the comparison's strongest; on our billing that's Gemini 3.1 Pro, since Claude needs a billing account that can buy Marketplace products) reads each stalled ticket with its context, plans, spawns auditors with exploration policies and hands subtasks to Jev. It runs per stalled ticket, never per push | **Aniket** | One stalled ticket on the real block goes orchestrator → auditors → Jev → writer → fact gate, cached | Nov 12 |
| 16 | ◐ built | **No context builder.** Agents get every fact | Rank each stalled team's sites by risk (the champion's scores and each file's bug-fix history) and cut where 90% of the likely problems are covered | **Chaitali** | The cut and how much context it saves, on the real block. Built (PR #78, `meridian/context.py`), not switched on: on the real block no stalled file has bug-fix history (only apache/spark has any), so there's nothing to rank. Waits on gap 24 | Nov 12 |
| 17 |  | **No graph walker.** If the context misses the cause, nothing looks further | An agent that queries the block itself (KNOWLEDGE-BASE.md queries, read-only) and returns what it finds as facts for the writer | **Utkarsh** (owns the graph) | On a fixture stall whose cause is outside the context, the walker finds it | Nov 12 |
| 18 | ✓ | **The retrain loop, the recall models and `--jev` aren't merged** | Review and merge PR #68. Its only CI failure was flake8, now fixed | **Aniket** | Merged (PR #68) | Nov 11 |
| 19 | ✓ | **Recall-first models** | `model.recall`: all three kinds tuned to flag the fewest commits for 90% recall, class weighting searched | **Aniket**; Rachna notes it in her decisions | Best: logreg 46.9% against the champion's 48.6%, interval −4.6 to +3.0 points: a tie. The ceiling is the data | Nov 11 |
| 20 | ✓ | **The agent cycle wasn't one command** | `meridian.agents --jev`: stalls → served policy → Jev → writer → fact gate | **Aniket** | Without an LLM the rows are byte-identical to the rules (tested, PR #68) | Nov 11 |
| 21 | ✓ | **PASS-BARS §2 had no data sign-off** | Signed | **Utkarsh** | PR #67 merged | Nov 11 |
| 22 |  | **No policy writer.** The only challenger is the hand-written v1, so exploring has little to try | An agent (coding mid → large, already in Jev's table) writes a new stall policy as code from the champion's losses in the match log. Policy tests must pass. A person reviews and merges it, then it explores: shadow on every stall, served ε of the time, judged by Elo. It replaces the champion (the exploit choice) only through the guard. **Starts only after gap 9:** every new challenger is another chance for a lucky promotion while the guard is loose | **Rochan** | Guard at ≤ 5% false promotions first (met, gap 9, PR #75). Then one LLM-written policy merged by a person and playing matches | After Nov 15 |
| 23 |  | **The fact gate rejects good real text.** 23 of 27 rejections were wrong (85%, bar 5%): numbers and paths that follow from the facts but aren't copied verbatim (vishwa decision 5, PR #77). Decision 5b fixed only the side causes (a person named "Nothing", nested names in `used`) | Let the gate accept a number or path it can derive from the facts (a sum, a count, a path's parts), by a rule written before the run. It must still catch the 200 planted errors | **Vishwa** | False rejects ≤ 5% on the cached real texts, 200 of 200 planted errors still caught. Then rerun `writer_compare` from the cache (0 calls) | Nov 12 |
| 24 |  | **No bug-fix history for the package repos.** `file_bug_fixes` covers apache/spark only, and every stalled call site is in a Django-ecosystem repo, so the context builder has nothing to rank (PR #78) | Run the existing bug-fix history step on the package repos when the block is built | **Utkarsh** (owns the graph) | `file_bug_fixes` rows for the package repos; then Chaitali reruns `meridian.context` | Nov 12 |

## The 2024 test run waits for the code freeze

2024 is the held-out test year (`test_allowed = false` in model.toml). It's scored once, and any change judged on it afterwards is contaminated, so it runs only when the code is frozen (decided Nov 11, Aniket). What it will score, as decided so far:

| Piece | Frozen as | Decided by |
|---|---|---|
| Champion | The forest, val PR-AUC 0.407 | Gap 8 (PR #74): the committed `registry/champion.json` |
| P9 | Lines × files | Gap 4: the model-and-size rule reached 0.619, under 0.80 |
| Training window | 2015–22 | Gap 12, decision 5 |
| Alert threshold | None | Decision 3: no cutoff met the precision bar |

Until the freeze, anything that could change these (gap 13's cleaner SZZ, say) is measured on val only. After it, the test runs once and is reported as it comes out. `model.refresh` refuses to run while the test is open.

## Why the model tops out: it's the data

The forest scores ROC-AUC 0.826 and PR-AUC 0.407 on val, which is 3.4× chance at an 11.8% positive rate. Accuracy is 88.5%, against 88.2% for always saying "no bug", which is why we don't report accuracy. Three model families land within 0.004 ROC-AUC of each other, and tuning flattened. Two checks point at the data instead:

| Check | Result |
|---|---|
| Learning curve, tuned forest on val (Nov 11) | 2022 only 0.360 · 2019–22 0.366 · 2017–22 0.381 · 2015–22 0.407 PR-AUC. A random 25% of 2015–22 (6.5k rows) gets 0.389, beating 2017–22 (16.7k rows). More rows of the same data add little (+0.018 from 25% to 100%). The old years help because their labels are complete (38% of 2015 commits blamed, 9% of 2022) |
| SZZ against the 130 Defects4J + Fonte labels (KNOWLEDGE-BASE.md) | SZZ picks the right bug-inducing commit 44% of the time (churn 18%, latest 16%). Most positive labels point at the wrong commit |

So the labels are noisy, recent labels are incomplete, and the features never see the code. The champion doesn't change before the 2024 run. What we do:

| # | Done | Fix | Owner | Done when | When |
|---|---|---|---|---|---|
| 12 | ✓ | Fill decision 5 (training window) from the learning curve above: 2015–22 stays, by its written rule | **Rachna** | Decision 5's row filled in, paired bootstrap 2015–22 against 2019–22. Done (PR #73): 2019–22 is −0.041 [−0.059, −0.022], so 2015–22 stays | Nov 12 |
| 13 |  | A cleaner SZZ variant: skip whitespace, comment and pure-rename lines when blaming. Measure it on the 130 Defects4J labels, then on val with the same forest. It's measured only and doesn't replace the champion | **Utkarsh** | The pick rate against 44%, and val PR-AUC against 0.407 | Nov 12 |
| 14 |  | The report says the ceiling plainly: accuracy is meaningless here, 3.4× chance, and SZZ is right 44% of the time | **Chaitali** (bias section), **Rachna** (model section) | Both sections carry these numbers | Nov 12 |

For the report as next steps, not built now: features from the diff itself (a small local code-embedding model, fixed version, cached per commit, $0 per push), and a commit-intent feature (fix, refactor, feature, formatting) by "LLM first, then ML". An LLM never writes the labels or scores a push.

## The model comparison

Models: `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`, Gemini Flash, Gemini Pro, Mistral Medium, Llama 4 Maverick, Llama 4 Scout, Gemma 4 31B, and Phi-4-mini (local through Ollama `phi4-mini`, Jev only). Use pay-per-token models only: anything that needs its own GPU server is out.

| Agent | Tasks | Scored on | Rule, written before the run |
|---|---|---|---|
| Writer | 204 tasks on the real block | Gate pass rate, valid JSON, cost per accepted text, p95 latency | The cheapest model within 2 points of the best pass rate wins its rung of the ladder |
| Diff drafter | the 20 package lines the codemod refuses (decision 15) | Match with the real after-line, and it applies, parses and resolves | Same rule. The bar stays 90% |
| Jev | 60 labelled tasks: 30 to write the prompt, 30 held out (decision 19) | Correct picks on the held-out 30, cost, p95 latency on a CPU runner | The cheapest model within 3 of the 30 tasks of the best (decisions 13, 14, 21). Phi or a 7–8B local model needs p95 inside CI's budget |

Equal results go to the open-weights model.

## Cost

About **$23** in total, with the alert at $150. It was $70 before PR #67 showed the LLM can only try 20 package lines, not 624.

| Owner | Work | Est. |
|---|---|---:|
| Utkarsh | diffs, every model | $2 |
| Vishwa | writer, every model | $16 |
| Rachna | Jev | $3 |
| Chaitali | Q2 audit, one strong model | $2 |
| Rochan, Aniket | | $0 |

Assumed per task: writer 3,000 tokens in and 800 out, diff 3,000 and 600, Jev 2,000 and 200, audit 6,000 and 500, plus 30% for retries. Prices are list rates as of Nov 11. Claude: haiku $0.10 / $0.50, sonnet $2 / $10, opus $4 / $20 per 1M tokens in / out. The Gemini Flash price is introductory and ends Dec 31. The open-model rates come from third-party listings, so check them before spending. Each owner measures 10 real prompts before the full run, and runs the large models only on tasks the small ones fail.
