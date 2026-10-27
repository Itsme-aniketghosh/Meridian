# Labels

Answer keys and gold sets, scrubbed per [CSV-POLICY.md](../CSV-POLICY.md). Run `python3 "Data-Pipeline/check_csv.py"` before committing.

## commit_ticket_hand_check_50.csv

Does the commit really do the work of the ticket its message names? 50 `commit_ticket` rows of run 70dd1dabfed544b8, drawn with `random.seed(1); random.sample(rows, 50)` over `out/70dd1dabfed544b8/commit_ticket.jsonl` in file order. Each row was judged by a person from the commit (`evidence_url`) and the Jira ticket (`ticket_url`). Follow-up and hotfix commits on a ticket's own code count as `yes`. Result: 49 `yes`, 1 `no` (0.98, bar 0.95 in [05-test](../../docs/05-test.md)).
