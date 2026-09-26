# Dead ideas

| Idea | Why dead | Revive if |
|---|---|---|
| Trained stall model | No public cross-team migration history | A customer gives completed internal migrations |
| Trained ETA model | Same reason | Same |
| Fragility from production incidents | Public data has bug reports, not incidents | A customer connects an incident tracker |
| Calibrated accuracy for stall reasons | Too few stalls in 10 weeks | N ≥ 30 recorded verdicts |
| Probabilities on stall reasons | Uncalibrated numbers look precise and aren't | Same |
| Promoting a policy on one migration or a few matches | Overfits. Elo is noise at small N | Never. ≥ 30 matches and a 50-point lead (04) |
| Unseeded random exploration | Breaks determinism | Never. Seed = hash(run_id, task_id) |
| The LLM choosing or running policies online | Not deterministic | Never. It only proposes code for human review |
| Auto-promoting LLM-written policies | Unreviewed code in the decision path | Never |
| Removal commit as ground truth | Holds about 0 call sites (62.8% tests, 29.9% definition) | Never |
| `index_together` as the parser benchmark | Its references are tests and implementation, not usage | Use it only on the cross-repo corpus |
| Burndown as proof the resolver is needed | Grep gives the same 279 | Never. Use blast radius and name collisions instead |
| tree-sitter alone | No name resolution | Never |
| Incremental-only parsing | A change in one file changes how another resolves | Never. Weekly full rebuild |
| Kafka for building the ticket join | 62% of commits have ticket keys | Use it for robustness tests only |
| Raw "Incomplete" as the vague-ticket label | 3,214 of 3,359 are bulk-closed | Never. Use the 145 real ones |
| Jira links as ground truth for chains | 5% of recent tickets are linked, none with "Blocks" | Never |
| Blocking inferred from imports | Floods the view with false blocks | Never. Receiver rule only (04) |
| Chains for tickets with no code signal | Nothing to join on | The ticket gets rewritten to name code |
| Embeddings or fuzzy matching of tickets to code | Not deterministic, not grounded | Only for the unclustered intake list, if needed |
| The LLM deciding owners, order, reasons, or flags | Not deterministic | Never |
| Stripping bad numbers from LLM text | Leaves broken sentences | Never. Fall back to the template |
| Rewrites that add intent, repro steps, or priority | Not in the map. Ask instead | Never |
| Designing solutions for cross-team features or bugs | Can't be grounded | Owners and order only |
| Predicting that a chain will stall work | No outcome data | Same as the stall model |
| Pulling from the open internet (Reddit, X, HN, Stack Overflow) for internal APIs | The problems aren't there | The API is public |
| One ticket per call site | 52 tickets nobody reads | Never |
| "Super engineer" shown on screen | Staff engineers won't trust a tool that claims to be their superior | Never |
| Claiming tools can't do internal APIs | OpenRewrite does | Never |
| Argo on Kubernetes | An always-on controller for a pipeline that runs 20 minutes a day | Pipeline runs continuously |
| Applying changes, opening PRs, filing tickets | Not earned yet | Suggestions get applied unchanged at a high rate |
