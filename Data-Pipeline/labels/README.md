# Labels

Answer keys and gold sets, scrubbed per [CSV-POLICY.md](../CSV-POLICY.md). Run `python3 "Data-Pipeline/check_csv.py"` before committing.

## commit_ticket_hand_check_50.csv

Does the commit really do the work of the ticket its message names? 50 `commit_ticket` rows of v1, run 70dd1dabfed544b8, drawn with `random.seed(1); random.sample(rows, 50)` over `out/70dd1dabfed544b8/commit_ticket.jsonl` in file order. A person judged each row from the commit (`evidence_url`) and the Jira ticket (`ticket_url`). Follow-up and hotfix commits on a ticket's own code count as `yes`. Result: 49 `yes`, 1 `no` (0.98, bar 0.95 in [05-test](../../docs/05-test.md)).

The 50 GitLab verdicts with `checked_by = VP` (`human_verdict` in `gitlab_cross_team_blocks.csv`): a model drafted each one and a person reviewed it against the GitLab issue, changing 2 of 50. That's a reviewed draft, not a blind hand check.

## q2_audit_hand_check_20.csv

Is the commit really bug-inducing, as SZZ says? GAPS.md gap 6, Chaitali's decision 11. The model finds 0 of the 51 `size_bucket=Q2` positives in `val` (2023), and this checks their labels. 20 of the 51, drawn with `random.Random(1).sample(sorted(q2_shas), 20)`, each with the first fix that blamed it (v1's `bug_inducing`, run 70dd1dabfed544b8). A person judges each one **blind**, without the model's answer, from the commit (`commit_url`), the fix (`fix_url`) and the ticket (`ticket_url`): `yes` if the commit introduced the bug the fix repairs, `no` if not (the blamed lines were only moved, reformatted or unrelated), `unclear` if the pages don't settle it. Written by `labels/q2_audit.py sheet`. The model's answers are compared with it in `Model-Pipeline/reports/q2_label_audit.json`. Never used as training labels.
