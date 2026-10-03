# Week 1 · next steps

All week-1 checks are done. This is what's left, by owner. Results are in each folder's README.

## Decide as a team

- **CSV policy.** Pairs 1–3 push their result CSVs so every number can be checked. The GitLab folder keeps CSVs local, including the 57 stall reads and both lists of 30 blocks. Pick one rule. If personal data is the worry, push copies with usernames removed.
- **Who owns the cross-team gold set** (default: Pair 3).
- **Demo:** cross-team blocks shown from GitLab cases. Chains across repos stay on fixtures, and the screen says so.

## Pair 1 · Django

1. Add the one-hop rule (`_ = old_name` → follow `old_name`) and rerun Q2. Expected: 542 of 542.
2. Find a method-call deprecation with 100+ uses, then confirm Jedi over pyright.
3. Hand-check 30 alias calls. Today the key comes from our own scripts.
4. Optional: replay 50 Django pushes, timed.

## Pair 2 · Spark + Jira

1. Hand-check 50 more ticket links, so precision ≥ 0.95 is proven, not just consistent.
2. Hand-check 50 collision pairs. Drop generic names (`apply`, `__hash__`) first.
3. Review the biggest identity groups in `people_emails`.
4. Decide whether Jira components are the "team" for the Spark demo.

## Pair 3 · packages + Defects4J

1. Swap storages, crispy, taggit and modeltranslation for autocomplete-light, dj-rest-auth, silk and tastypie.
2. Freeze each package's answer key at its migration commit's parent. Label every line reachable, example, or dead.
3. Run `make_labels.py` and `ticket_key_check.sh` once. They're the only untested scripts.
4. Optional: bisect the 724 unlabelled Defects4J bugs (~150 more labels, ~10 h of Docker).

## Cross-team · GitLab

1. Merge Vishwa's 30 and the link pass's 30 into one deduplicated list. At least 38 distinct issues.
2. A person spot-checks 10 "wait", 5 "planned" and 5 "partial" rows. If 9 of 10 waits hold, they're gold.
3. Run the link pass on Jul–Dec 2024 (`gitlab/xt_*.py`, ~1 h), to pass 30 on written waits alone.
4. Turn the 57 stall reads into the #7 gold set. End each blocked spell at the earliest of: label removed, status changed, or blocker closed.

## Docs and design

- Jev: pick the models for each agent's ladder, and Jev's own model, in `meridian.toml`.
- Add Pair 1's goldens to 05-test's candidate list:
  - the `_ = ugettext_lazy` assignment alias
  - Python inside `code.sample`
  - `user.is_anonymous = lambda: True`
- Unresolved method calls: keep the name matches, labelled `ast_unresolved`, instead of dropping them.
- Scoping doc: add the missing surnames, confirm the licenses, and line the timeline up with course deadlines.
