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

- [x] The writer's prompt tells the LLM: "Use team names only to name teams. Don't use them as ordinary words." So it writes "across the codebase", not "across the platform". (`agents.SYSTEM`, Nov 9)
- [x] Measure the gate's false-reject rate on real LLM output (decision 5). At or under 5%: done. Over 5%: revisit the team rule then, with real data, not our own test sentence.

### Decision 5 result (Nov 11): the gate's false rejects on real LLM text

Block 70dd1dab, the writer's 204 real tasks on Gemini 3.8 Flash, Llama 4 Maverick, Llama 4 Scout and Gemma 4 26B (816 answers, `llm_cache/`). Every text the gate rejected was read by hand next to its facts: fine (every claim is in the facts or follows from them, and the gate was too strict) or wrong (a claim the facts don't support, or a reply that can't be read). Sheet: `out/70dd1dabfed544b8/handcheck_writer.csv`, local, not in git.

| Rejected by | Texts | Read | Fine (false reject) | Wrong |
|---|---:|---:|---:|---:|
| **the gate** (a token not in the facts) | 27 | all 27 | **23 (85%)** | 4 |
| the person check (a person's name) | 45 | 0: such replies are never stored | likely all (below) | 0 |
| the `used` list (a name that isn't a top-level fact) | 183 | 18, sampled | 17 | 1 |
| not JSON | 2 | 2 | 0 | 2 (an extra closing brace) |

**85% is over the 5% bar, so the gate is revisited. The team rule isn't the cause:** of the 23 false rejects, 21 are numbers and 2 are paths, none a team. The writers state numbers that follow from the facts and are right ("11 sites in config/urls.py", counted from the site list; "done when the count is 0"), and the gate takes only numbers the facts spell out. The 4 it caught were real: "44 other files" (the facts have 35), a date that doesn't exist (2016-13-05), a wrong path, and a site list with wrong lines and one site left out.

- **The person check:** a git author in the block is named "Nothing", so "nothing blocks them" names a person. Gemini 39 consumer texts, Maverick 5 consumer and 1 ticket draft. Regenerated, 2 of 3 replies matched "Nothing" (the third came back without the word); the stored ones are only the redacted marker, so the 45 aren't read one by one.
- **The `used` list:** the open models list fields inside a fact (`bug_fixes`, `files[0].path`) or words (`evidence`, `reason`). The gate passes the text itself in 175 of the 183. With a looser `used` check: Gemma 98.5%, Scout 93.1%, Maverick 87.7%, against 65.2%, 54.4% and 74.0% as run.
- None of this changes the writer's comparison: the rules were written before the run and every model is held to the same ones. A fix (numbers that follow from the facts, person names that are common words, top-level-or-inner `used` names) is a new decision, written before its run, and measured from the cache at $0.

### Decision 5b (written Nov 11, before its run): the person check and the `used` list

Two of decision 5's three causes. The gate's fact rules don't change: numbers that follow from the facts (21 of the 23) are a later decision, fixed in the writer's facts, not by loosening the gate (decision 1 showed why).

| Option | Change |
|---|---|
| as is | nothing |
| **1. person names** | a person whose whole name is one English function word ("nothing", "none", "nobody", ... a closed list in `agents.py`) isn't a name the text can be said to use. Function words that are also first names (Will, May, June, Bill) stay names. A dictionary is too wide: 89 of the block's 8,299 people have a one-word name in /usr/share/dict/words, most of them first names (Frank, Mark, Tom, Anna) |
| **2. `used` list** | an entry counts if, with `[n]` indices dropped, every dotted part is a key somewhere in the facts (`files[0].path`, `bug_fixes`, `evidence`). A value (a date, a symbol) still fails |
| **both** | 1 and 2 |

Test: the 816 answers in `llm_cache/` gated again ($0). The 45 replies the person check kept as the redacted marker have no text, so they're asked again (about $0.10) and stored only if the new check passes them.

Rule: adopt an option only if all of these hold. Otherwise keep as is.
1. The 7 texts judged wrong in decision 5 are still rejected.
2. Every test passes unchanged, the decision-1 corpus sweep among them (2,000 / 2,000 caught), and every person test in `test_agents.py`.
3. No newly accepted text names a person: each one that has a skipped name in it is read, and the name is an ordinary word there.
Of the options that pass, the one that accepts the most texts. Reported: each model's pass rate before and after. The writer's comparison isn't redone: its pick stands under the rules it was run with.

Result (Nov 11): **both, adopted**, now in `agents.py` (`FUNCTION_WORDS` in `names()`, `_a_fact` in `parse()`). The 45 redacted replies were asked again ($0.10); the other 771 were gated again from the cache.

| Model | As run | With 5b |
|---|---:|---:|
| Gemini 3.8 Flash | 80.4% | **98.0%** |
| Llama 4 Scout | 54.4% | **93.1%** |
| Gemma 4 26B A4B | 65.2% | **92.2%** |
| Llama 4 Maverick | 74.0% | **90.7%** |

1. All 7 texts judged wrong in decision 5 are still rejected.
2. Every test passes unchanged (186 in test_agents, test_fact_gate, test_jev, test_views, test_diffs and test_llm), and the decision-1 sweep is unchanged: article catches 2,000 / 2,000 with 0 / 2,000 false rejects.
3. The one person name skipped on this block is NOTHING. 19 accepted texts have the word, all read by hand: 16 "blocked by nothing and is blocking nothing", 3 "nothing blocks them". In every one it's the ordinary word.

Under 5b the cheapest model within 2 points of the best (98.0%) is still Gemini 3.8 Flash: the others are 4.9 points or more behind. What's left is the gate's 21 numbers that follow from the facts, and the writer's facts are where they get fixed.

### Decision 5c (written Nov 13, before its run): derived numbers in the writer's facts (GAPS.md gap 23)

Written by Aniket's session for Vishwa's gap, to land before the code freeze. The gate doesn't change.

**What's left after 5b.** Gating the 816 cached answers again gives 37 texts that parse but fail the gate. Gemini Flash has 4, Scout 13, Gemma 2 and Maverick 18. All 37 were read next to their facts, AI-assisted, with Aniket's session checking each claim against the block:

- **26 are right.**
  - 12 are counts the facts don't spell out: "across 3 files" in 11 consumer texts, and "done when the count is 0" in 1 ticket draft.
  - 14 are ticket drafts that list lines without their path ("django/forms/formsets.py at lines 9, 344, and 351").
- **11 are wrong.**
  - "44 other files": django-cms has 35 files.
  - "2016-13-05": not a date.
  - "debug_toolbar/staticfiles.py:34": the path is `debug_toolbar/panels/staticfiles.py`.
  - 8 Maverick consumer texts write a file's site count as its line ("apps.py:1"). The block has those sites at lines 2, 4 and 18.

**The change.**
- A ticket draft's facts gain `files`, which is `[{"path", "sites"}]` in site order, plus `files_n` and `done_when_left: 0`.
- A consumer's facts gain `files_n`, the file total before any context cut.
- The 14 line lists stay rejected. A line is a fact only next to its path (decision 1). A bare `lines` list would make every line number a count, so "44 other files" would pass wherever a site sits on line 44. Reading "path at lines a, b" is a change to the gate, a later decision.

**Test.** The cached answers are gated again ($0): each reply is looked up under its old facts' key and gated against the new facts.

**Rule.** Adopt only if all of these hold. Otherwise keep as is.
1. All 11 wrong texts are still rejected, and so are the 7 judged wrong in decisions 5 and 5b.
2. Every test passes. Only fixtures that spell out a ticket draft's or a consumer's facts may change, and only by the new keys.
3. Every newly accepted text is read and is right.

Reported: each model's pass rate before and after. Changed facts change the cache keys, so the 136 ticket draft and consumer tasks miss the cache until the live run asks again. On Gemini Flash that's about $0.30. Until then those tasks ship their templates. The writer's pick isn't redone.

## The writer, Nov 9: settings, not tests

These are design choices for `meridian/agents.py` and `llm_cache.py`, made Nov 9 before any LLM ran. None of them is a measured decision: decisions 3–6, 9 and 10 still measure the model, the prompt format, the templates and the cache once there's a key.

- **Tasks:** rewrite (one per ticket, across every symbol its sites call, naming each owning team), ticket_draft and consumer (per team and symbol), stall_reason (per stall). The first three write a `suggestion` row of their kind. **consumer has no row**: schema.sql's `suggestion.kind` has no value for it, and a new one is a contract change (S10), asked in the team chat first. Until then its text goes only to the views.
- **Cache:** an entry keeps the raw reply, and the writer gates it again on every run. A reply that names a person is never written: the entry keeps a redacted marker, so a rerun still makes 0 calls and the committed cache never holds a name.
- **Prompt version:** 12 hex of sha256 over the system prompt, the task prompt, the frame and the facts format. Any edit is a new version, including decision 4's switch from JSON to bullets.
- **Ladder:** `claude-haiku-5-5` → `claude-sonnet-5-5` → `claude-opus-5-5` → the template (04 §6), route `rules` until Jev routes. Bad JSON or a gate miss moves one model up. The ladder is a constant in `agents.py`, not `meridian.toml [llm]` as 04 §6 says: a new section there changes every block's config hash, and with it its run_id. That's a contract change to agree in the team chat.
- **Jev, Nov 10 (Rachna's request):** `write(ladder=, route=)` takes the models and the route from Jev's `Router.ladder(task)`, and `run(router=)` asks Jev for each task. Without a router, it's the fixed ladder and route `rules`, as before. The route goes on the suggestion row and the logged call. A task whose facts name a person is skipped before Jev sees it, because Jev's prompt carries the facts. With Jev off, the rows are byte for byte the same as without it (`tests/test_agents.py`, `Routing`).
- **No LLM client yet:** `llm` is a function the caller passes. With none (no key, as in CI), the cache answers what it has and every miss ships the template, or stops the run with `--strict-cache` (PASS-BARS §6.5).
- **Logging:** only LLM answers go through `log` (`tracking.log_llm_call` with `--track`), cached or called. A template that ships with no LLM attempt isn't a call. When the last attempt fails, its row says `source = template`; bad JSON says `gate_pass = null` (never gated). So MLflow's `api_calls` and gate rates are the LLM's alone. The fallback counts go in `writer_report.json`.
- **No person:** a stall's evidence rows can name a file's top author (#4 Orphaned owner), so stall facts carry only the reason names and their evidence counts. The gate's people are persons who aren't bots and have at least one alias that isn't generic: the real block's placeholder `test` isn't anyone's name. A task whose facts still name a person is skipped and counted, never written.
- **Stalls:** the facts hold the stall's start (`since`) and its symbol, never its age, so a stall's text and row stay the same while it lasts. The text lists the reasons "in the auditor's order", never "most likely first" (06-dead-ideas: no probabilities).
- **Fragility ships with ticket IDs** (02-product rules): the consumer text names a file's bug-fix keys with its count. "bug fix(es)" is the writer's `VOCAB`, read as words by the gate even though the real block has a team named Bug.
- **A template the gate flags still ships** (it can only say facts) and is counted in the report: on a real block a fixed word can be a team's name.

Result on the fixture, LLM off (`python -m unittest tests.test_agents`, 33 tests, N = 14 tasks): 14 of 14 ship their template, 0 template gate misses, 9 suggestion rows (2 rewrite, 3 ticket_draft, 4 stall_reason), 0 persons in any fact, prompt or cache entry, each untracked site in exactly one draft. With a fake LLM: a warm cache makes 0 of 14 calls with identical rows; bad JSON and gate misses go small → mid → large → template.

Result on the real block `70dd1dabfed544b8` (LLM off, `python -m meridian.agents --quiet`, N = 204 tasks: 68 ticket_draft, 68 stall_reason, 68 consumer, 0 rewrites: staff_vague returns no ticket on it): 0 template gate misses, 0 skipped. Before the review fixes the run stopped on its first ticket_draft. Checked against all 76 of its team names on the fixture's texts: the one collision was "bug fixes" with the team Bug, now `VOCAB`.

## Review fixes, Nov 9 (PR #48, before review by the lead)

An independent review ran the writer on the real block and found 11 problems; each now has a test in `tests/test_agents.py` or `tests/test_fact_gate.py` (`RealBlockNames`).

- The run stopped on the real block: a template failing its own gate raised. Gate fixes: an exact team name wins over another team's plural (`tests` vs `test`); text quoting a fact word for word is known (the symbol `url`, the reason "Test gap", `checkout.verify`); a fact path is a path whatever its extension (`code.sample:4`); `vocab=` for the caller's fixed phrases. Writer: placeholder persons aren't people; a person in the facts skips the task instead of stopping the run.
- A block from before schema v1.3 has no `suggestion` table: rows so far are then none.
- Templates were logged as LLM calls, inflating `api_calls` and the gate pass rate. Now only answers are.
- A reply naming a person reached the committed cache. Now redacted; the test scans the committed `llm_cache/` too.
- The ladder dropped the large model, against 04 §6. Added back.
- A ticket across two symbols got two partial rewrites, and a cross-team ticket named one owner. Now one rewrite per ticket, an owner per line.
- The prompt version missed the frame and the facts format. Now covered.
- The stall-policy test couldn't see which policy was served. Now it serves a planted backwards policy.
- Stall texts carried their age, so each run offered a new row. Now their start date and symbol.
- "most likely first" claimed a likelihood. Now "in the auditor's order".
- Smaller: a fenced ```json reply is read; `--write` refuses an `--at` that isn't the snapshot (schema.sql: `offered_at = snapshot_at`); an area only when every site shares a directory; the fallback counts are published in `writer_report.json`.

Still open: the suggestion kind for consumer text, and the ladder's home (`meridian.toml [llm]`), both contract changes for the team chat. `suggestion.jsonl` holds every row so far, so once the diff drafter writes it too, the two must agree who writes the file (Utkarsh).

## The views, Nov 10: review fixes before the lead's review

An independent review of `views.py` against Contract 6 and section O found these. Each fix has a test in `tests/test_views.py`, and each test was checked by undoing its fix: all 13 undone fixes fail a test.

- **One request at a time.** Every request aims the block's temp tables at one (repo, symbol) with `A.point()`, and every server thread shares one connection. In the review, 9 parallel clients got 27 wrong answers and 54 errors out of 120. The server now holds a lock per request (`Server.test_parallel_requests_...`).
- **`--fixture --serve` works.** The fixture's in-memory block is copied into a connection the server's threads may use (`FixtureServer`).
- **A failure is a 500**, with the traceback in the log, never a dropped connection. Without `--serve`, an unknown page exits with its message.
- **As of the snapshot, everywhere** (`AsOf`): the default (repo, symbol) and the team check count `site_now`, not every row of `call_site`. A team or symbol first known later is a 404. Suggestion rows count only when offered by the snapshot. A push shows only if its base run is known by then, and an outcome decided after the snapshot shows as `open`.
- **Diffs are shown.** The consumer view lists the team's `diff` rows for call sites still present, and the staff view lists `legacy` rows (the radar), both exactly as stored.
- **The committed cache is read.** With the LLM off, a text is the cached reply if it passes the gate again, else the template. Nothing is written.
- **Stall reasons only for STALLED teams.** A blocked team doesn't get a reason. `/staff` is served like `/api/staff`, and `/api/staff/owner/x` is a 404.
- **Tests that could barely fail were tightened.** Every number must sit in its own row's cell, not just anywhere on the page. A test plants a number in the wrong cell to show the check catches it. The no-writes test is now an SQLite authorizer on a writable connection that refuses any write to the block's own tables. The real-block test also checks every page for emails and people's names.

**The fixture bar, in the fixture's numbers.** PASS-BARS §5 quoted 02-product's mock-up: "31 of 52 done", Checkout "STALLED", Fulfillment "blocked". `acme/shop` has 13 call sites, so its owner page reads **"5 of 13 done"**, Checkout STALLED and Fulfillment blocked. The test checks that rendered text. The bar's wording now says this. The bar itself is the same.

**02-product's layout, now built.** The staff view has Collisions (`derive_collides`), Duplicates (`derive_duplicates`), Teams not connected (`stall_never_notified`) and the legacy radar (`radar_deprecated_with_callers`, plus any stored `legacy` rows). The owner view's Per team table says who a blocked team is waiting on (`stall_upstream`): Fulfillment, waiting on Platform.

**Decision 8, built as written: real where its tables have rows, fixture otherwise.** The measure is the rows each view's tables return on its default page, logged on every start. On the real block (run `70dd1dabfed544b8`) those are: staff 251, owner 3, consumer 28, tickets 1, **push 0**. So the push page comes from the fixture: the block predates `push_alert`. The banner says it's the fixture, and why. A link from a real page into a fixture view opens the fixture's own default page. `--real-only` turns this off, and `--fixture` shows every view from the fixture.

**A stall's repo is never empty.** Stalls come only from `auditor.block_stalls`, which points at each (repo, symbol). So `x.repo or ""` in `agents.stall_tasks` never fires, and the owner view finds each text. There's a test.

**Startup.** The writer runs once at start, about 46 s on the real block, and 35 s of that is one query, `stall_competing_load`. Its `EXISTS` on `commit_file` makes SQLite build a temporary index over all 849k rows on every call, 25 times a run. Collecting the commits that touch a present site once gives the same rows four times faster. That's KNOWLEDGE-BASE.md, so it's a separate PR for its owner, like #49 for the auditor.

**Run 2: not measured yet.** The views are tested on two runs (the fixture's R1 and R2, the `AsOf` tests), and on a block only `call_site` and `run` keep earlier runs' rows (`join.py`). But there's no real run 2 yet: `out/c5118b019d642a9b` has run 1's snapshot time and is a partial rebuild of run 1. Running the views on run 2 waits on a second Collect.

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
