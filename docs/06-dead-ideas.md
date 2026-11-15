# Dead ideas

| Idea | Why dead | Revive if |
|---|---|---|
| Trained stall or ETA model | No public migration history | A customer gives one |
| Probabilities on stall reasons | Too few stalls to calibrate | N ≥ 30 verdicts |
| Fragility from incidents | Public data has bugs, not incidents | Incident tracker connected |
| Promoting a policy on few matches | Elo is noise at small N | Never |
| Unseeded exploration | Breaks determinism | Never |
| LLM deciding facts or picking auditor policies | Not deterministic | Never |
| Auto-promoting LLM-written policies | Unreviewed code decides | Never |
| Removal commit as ground truth | ~0 call sites in it | Never |
| `index_together` benchmark | Refs are tests, not usage | Cross-repo only |
| Burndown as resolver proof | Grep finds all 265 too | Never |
| tree-sitter alone | No name resolution | Never |
| Incremental-only parsing | Resolution crosses files | Never |
| Kafka for the ticket join | 62% keyed | Weaker-keying tests |
| Raw "Incomplete" as vague label | 3,214 of 3,359 bulk-closed | Never |
| Jira links as chain truth | 5% linked, 0 "Blocks" | Never |
| Debian py2removal links as block truth | Reversed, bulk-added from the dependency graph | Never |
| Counting waits by keyword | 3 of 5 hits were noise (Mozilla) | Never |
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
| LLM on every push | Costs per push, and the answer changes run to run | Budget exists, for text only. Never for P9: it would decide a fact |
| Full parse per push | Too slow | Never |
| Live Jira per push | Not deterministic | Never |
| Synthetic histories for defect risk | Learns the generator | Never |
| P9 alerting when the model and lines × files both flag | 0.619 on 42 val alerts, bar 0.80 | ≥ 0.80 on 30 alerts |
| LLM auditing defect labels | Agreed with a blind hand check on 10 of 20 | ≥ 15 of 20 |
| Per-group thresholds for the size bias | Equal recall by size costs 0.121 precision, recall 0.52 → 0.34 | Passes the mitigation rule |
| Loosening the fact gate for right text it rejects | Lets a swapped team name through. Fix the facts instead | Never |
| Applying changes, PRs, filing tickets | Not earned | Suggestions applied unchanged |
