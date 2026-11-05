# Vishwa: decisions

Each decision has its test and the rule that picks, written before the run. Results go under each one after the run, with N.

## 1. What the gate counts as a fact token

Options:
- **a. numbers only**: numbers and dates
- **b. plus paths and IDs**: also `path` and `path:line`, SHAs, ticket keys
- **c. plus team names**: also the team names in `team_map`

Always on, whatever the option: emails, `@handles` and known person names fail the gate. No person is ever a fact (Contract 5).

Test: `tests/test_fact_gate.py` builds 200 clean texts and 200 injected errors from the fixture's facts (`acme/shop`, KNOWLEDGE-BASE.md). There are 40 errors of each kind: a wrong number, a made-up path, a swapped team, a fake ticket key, and a real fact from another task. The test measures the catch rate and the false-reject rate for each option.

Rule: pick the smallest option that catches 100% with at most 5% false rejects (05-test). If none does, pick the one with the highest catch rate and write down what it misses.

Result (Nov 5, `python -m tests.fact_gate_corpus`, seed 5, N = 200 + 200, numbers = round):

| Option | Caught | False rejects | Misses |
|---|---|---|---|
| a. numbers | 63 / 200 | 0 / 200 | every path, team and ticket error; 17 of 40 other-task facts |
| b. ids | 160 / 200 | 0 / 200 | all 40 swapped teams |
| c. teams | **200 / 200** | **10 / 200 (5.0%)** | none |

Picked **c**: the only option that catches 100%. Its 5.0% false rejects sit exactly on the bar. All 10 come from one filler, "Keep the same error handling across the platform.", where `platform` is a plain word, not the team. That cost depends on how often a writer uses a team name as a word, so it's measured again on real LLM output (decision 5). If it goes over 5% there, the cheapest fix is in the writer's prompt (don't use team names as plain words), not a looser gate.

A catch counts only when the gate flags something its clean text doesn't, so a filler the gate already rejects isn't counted as catching the error.

## 2. Number matching

Options:
- **exact**: the text's number is written exactly as the fact (`1526` only)
- **format**: the same value however it's written (`1,526` = 1526, `71%` = 0.71)
- **round**: also rounded (`71%` or `71.2%` from 0.712). Rounding applies only to a number written with `%` or decimals. A bare whole number must equal a fact, so `1` never passes because some fact is 0.712.

Test: the same 200 + 200, run with option c from decision 1. False rejects on the clean texts, and the catch rate, for each.

Rule: pick the strictest option with at most 5% false rejects and a 100% catch rate.

Result (Nov 5, same run, kinds = c):

| Option | Caught | False rejects |
|---|---|---|
| exact | 200 / 200 | 40 / 200 (20%) |
| format | 200 / 200 | 24 / 200 (12%) |
| round | **200 / 200** | **10 / 200 (5.0%)** |

Picked **round**: the only option within 5%. exact rejects "1,526" and "71%"; format still rejects "71%" and "86%" from 0.712 and 0.857. The 10 left are decision 1's `platform` filler, not numbers.

## To do when building the writer

From decision 1: all 10 false rejects are a team name (`platform`) used as a plain word. Loosening the gate to allow that let a swapped team through ("Done: platform, 2 of 2." for search), so the fix goes in the writer, not the gate.

- [ ] The writer's prompt tells the LLM: "Use team names only to name teams. Don't use them as ordinary words." So it writes "across the codebase", not "across the platform".
- [ ] Measure the gate's false-reject rate on real LLM output (decision 5). At or under 5%: done. Over 5%: revisit the team rule then, with real data, not our own test sentence.

## Known limits, written before the run

- Numbers written as words ("two calls") aren't checked. Only digits are. A wrong count in words passes.
- A team name that's also a plain word ("search", "platform") is flagged wherever it appears in the text. Decision 1 measures what that costs.
- A 0 or 1 flag in the facts (`in_except`) also lets "0" and "1" pass as numbers. Found while testing; a flag should reach the gate as a bool, which the gate skips.
- A line number counts only next to its path, never as a count (found by the tests: "214 bug fixes" passed before).
- The 200 + 200 texts are written by us from the fixture, not by an LLM. The false-reject rate on real LLM output is measured again once the writer runs (decision 5).
