# Dead ideas

| Idea | Why dead | Revive if |
|---|---|---|
| Trained stall or ETA model | No public migration history | A customer gives one |
| Probabilities on stall reasons | Too few stalls to calibrate | N ≥ 30 verdicts |
| Fragility from incidents | Public data has bugs, not incidents | Incident tracker connected |
| Promoting a policy on few matches | Elo is noise at small N | Never |
| Unseeded exploration | Breaks determinism | Never |
| LLM choosing policies or deciding anything | Not deterministic | Never |
| Auto-promoting LLM-written policies | Unreviewed code decides | Never |
| Removal commit as ground truth | ~0 call sites in it | Never |
| `index_together` benchmark | Refs are tests, not usage | Cross-repo only |
| Burndown as resolver proof | Grep gets 279 too | Never |
| tree-sitter alone | No name resolution | Never |
| Incremental-only parsing | Resolution crosses files | Never |
| Kafka for the ticket join | 62% keyed | Robustness tests |
| Raw "Incomplete" as vague label | 3,214 of 3,359 bulk-closed | Never |
| Jira links as chain truth | 5% linked, 0 "Blocks" | Never |
| Blocks from imports | Floods false blocks | Never |
| Fuzzy ticket ↔ code matching | Not grounded | Unclustered intake only |
| Stripping bad LLM numbers | Broken sentences | Never |
| Rewrites adding intent or priority | Not in the map | Never |
| Designing cross-team features | Can't be grounded | Never |
| Open-internet intake for internal APIs | Problems aren't there | API is public |
| One ticket per call site | Nobody reads 52 | Never |
| "Super engineer" on screen | Staff engineers won't trust it | Never |
| Argo on Kubernetes | Always-on for a 20-min job | Continuous pipeline |
| Blocking merges on push | One false block, uninstalled | Precision ≥ 0.95 for 3 months |
| LLM on every push | Costs per push | Budget exists |
| Full parse per push | Too slow | Never |
| Live Jira per push | Not deterministic | Never |
| Synthetic histories for defect risk | Learns the generator | Never |
| Applying changes, PRs, filing tickets | Not earned | Suggestions applied unchanged |
