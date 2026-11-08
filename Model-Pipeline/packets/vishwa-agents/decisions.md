# Vishwa: decisions

Each decision has its test and the rule that picks, written before the run. Results go under each one after the run, with N.

## 1. What the gate counts as a fact token

Options:
- **a. numbers only**: numbers and dates
- **b. plus paths and IDs**: also `path` and `path:line`, SHAs, ticket keys
- **c. plus team names**: also the team names in `team_map`

Always on, whatever the option: emails, `@handles` and known person names fail the gate. No person is ever a fact (Contract 5).

Test: `tests/fact_gate_corpus.py` builds 200 clean texts and 200 injected errors from the fixture's facts (`acme/shop`, KNOWLEDGE-BASE.md). There are 40 errors of each kind: a wrong number, a made-up path, a swapped team, a fake ticket key, and a real fact from another task. The test measures the catch rate and the false-reject rate for each option.

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

## 11. A team name used as an ordinary word

Why: the seed sweep (below, Review fixes) puts the gate at 7–14 false rejects per 200, mean 4.95%, over 5% in seeds 3, 8 and 9. Every one is the filler "Keep the same error handling across the platform.", where `platform` is a word, not the team. The lead asked for clearly under 5% (Nov 7).

Options (`check(..., team_rule=...)`):
- **strict**: today's gate. Every team name, singular or plural, must be a fact. The baseline.
- **article**: a team name right after `the`, `a`, `an`, `this`, `that`, `our` or `your` is read as a word and not checked, unless the next word is `team`, `'s`, `owns` or `own`. "across the platform" passes; "the platform team" and "Done: platform, 2 of 2" are still checked.
- **ownership**: a team name is checked only next to `team`, `'s`, `owns` or `own`, or in a list with another team name. Everything else is read as a word.

Test: `python -m tests.fact_gate_corpus --sweep` from Data-Pipeline/: seeds 0–9, each 200 clean + 200 injected (N = 2,000 + 2,000), kinds = teams, numbers = round. For each option: caught per seed, false rejects per seed, mean.

Rule, written before the run: an option passes if it catches 200 / 200 on every seed, its mean false-reject rate is at most 2.5%, and no seed is over 5% (10 / 200). Of the options that pass, pick the one with the lowest mean false-reject rate; on a tie, the stricter one (strict > article > ownership). If none passes, keep strict and the writer's prompt fix below.

Result (Nov 7, `python -m tests.fact_gate_corpus --sweep`, seeds 0–9, N = 2,000 + 2,000, kinds = teams, numbers = round):

| Option | Caught per seed | False rejects per seed | Caught | False rejects | Worst seed | Passes |
|---|---|---|---|---|---|---|
| strict | 200 200 198 200 200 200 200 200 199 200 | 7 10 9 14 8 10 9 9 12 11 | 1,997 / 2,000 | 99 / 2,000 (4.95%) | 14 / 200 | no |
| article | 200 on every seed | 0 on every seed | **2,000 / 2,000** | **0 / 2,000 (0.00%)** | 0 / 200 | **yes** |
| ownership | 163–170 | 0 on every seed | 1,649 / 2,000 | 0 / 2,000 (0.00%) | 0 / 200 | no: 351 swapped teams pass |

Picked **article**, the only option that passes, now `DEFAULT_TEAM_RULE` in `fact_gate.py`. `test_fact_gate.py` (`TeamRule`) checks the bar on all 10 seeds in CI.

- strict's 3 short catches aren't the gate missing anything: the swapped team was `platform` in a text whose clean version already has the `platform` filler, so the scorer (which counts only new flags) sees nothing new.
- ownership reads "Ask payments to review." and "Done: platform, 2 of 2." as words, so most swapped teams get through.
- 0% is on our own 400 texts per seed, where the only team-as-word sentence is the `platform` filler. Real LLM output is measured in decision 5.
- Known limit: a team name after an article in other words, "ask the payments folks", reads as a word and passes. A test pins it down (`test_known_limit_article_hides_a_swapped_team_in_other_words`). Decision 5 shows how often the writer does this.

Review fix (Nov 8, PR #42): two swapped teams got past article: a curly apostrophe ("the platform’s client", as LLMs write it) and a team after an article in a list ("the payments and checkout teams"). Both are checked now and pinned in `TeamRule`; the sweep didn't move (article: 2,000 / 2,000 caught, 0 / 2,000 rejected). Still read as a word: "owned by the platform", "blocked by the platform", "the platform owners".

## To do when building the writer

From decision 1: all 10 false rejects are a team name (`platform`) used as a plain word. Loosening the gate to allow that let a swapped team through ("Done: platform, 2 of 2." for search), so the fix goes in the writer, not the gate.

- [ ] The writer's prompt tells the LLM: "Use team names only to name teams. Don't use them as ordinary words." So it writes "across the codebase", not "across the platform".
- [ ] Measure the gate's false-reject rate on real LLM output (decision 5). At or under 5%: done. Over 5%: revisit the team rule then, with real data, not our own test sentence.

## Review fixes, Nov 6 (PR #28)

The lead's review probed the gate and found these passing when they shouldn't. Each is now a test in `tests/test_fact_gate.py` (`Bypasses`), and the corpus result above didn't move (seed 5: 200 / 200 caught, 10 / 200 false rejects).

- `check(text, facts)` had `teams=()` and `people=()` as defaults, so the Contract 5 call checked no teams and no names. Both are now required keywords: `check(text, facts, teams=..., people=...)`, from `team_map.team` and `person.name`. Utkarsh's diff drafter and Rachna's Jev must pass them.
- A fact that names a person (an email or a name in `people`) raises `ValueError`: no person is ever a fact.
- A percent matched counts and the wrong scale ("4%" and "400%" from `bug_fixes = 4`, "1%" from 0.712). A percent now only matches a share (a float or an "x%" fact), at ×100.
- Counts in words, two to twenty, are checked like digits. "one" isn't: it's a pronoun far more often than a count.
- Numbers with a unit (`14d`, `3x`, `5k`, `500ms`) and a minus sign are read.
- SHAs in any case, and all-letter hex (`deadbee`), are checked.
- More extensions (`.rs .tsx .jsx .vue .erb .kt .c .h .cpp .cs .php` and others), and `./` or `/` before a path.
- A team's plural or singular ("payment", "checkouts") counts as the team.

Still not checked: a year alone when some fact date has that year ("2026 calls"), hashes under 7 characters, `1e3` and `1_526`, directories and files with no extension, spelling variants ("fulfilment").

Seed sweep (the corpus re-samples formats per seed, seeds 0–9, N = 200 + 200 each): caught 200 in 8 seeds, 199 and 198 in the other two; false rejects 7–14, mean 9.9 (4.95%), over 10 in seeds 3, 8 and 9. Every false reject is the `platform` filler, so the bar sits on how often that one sentence is drawn. The honest number is the one decision 5 measures on real LLM output.

Process note: the rules and results for decisions 1 and 2 landed in one commit (5147d54), so git can't show the rule came first. Decisions 3 on are committed before their run. The settings stay as constants in `fact_gate.py` (`DEFAULT_KINDS`, `DEFAULT_NUMBERS`), not `meridian.toml`, so the gate doesn't change the block's run_id; a run records them with the writer's settings.

## Known limits, written before the run

- A team name that's also a plain word ("search", "platform") is flagged wherever it appears in the text. Decision 1 measures what that costs. (Decision 11 changed this: right after an article it's read as a word.)
- A 0 or 1 flag in the facts (`in_except`) also lets "0" and "1" pass as numbers. Found while testing; a flag should reach the gate as a bool, which the gate skips.
- A line number counts only next to its path, never as a count (found by the tests: "214 bug fixes" passed before).
- The 200 + 200 texts are written by us from the fixture, not by an LLM. The false-reject rate on real LLM output is measured again once the writer runs (decision 5).
