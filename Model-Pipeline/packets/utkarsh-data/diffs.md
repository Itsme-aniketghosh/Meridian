# Diff drafter: codemod path, decisions 10 and 11

Code: [meridian/diffs.py](../../../Data-Pipeline/meridian/diffs.py). Tests: [tests/test_diffs.py](../../../Data-Pipeline/tests/test_diffs.py). Bars: [pass-bars.md §5](pass-bars.md) (on `utkarsh-features`), written before the drafter existed.

Everything below comes from the team run `c5118b019d642a9b`. Each repo is drafted at its pinned commit and compared with the commit that really migrated it: `c651331b34` for Django, and each package's `url()` migration commit (its `meridian.toml` ref without `^1`).

## How it drafts

- **One draft per line.** All sites on a line become one after-line, so each draft changes only its own line.
- **Rename rules:** `ugettext` → `gettext` and the other four Django names. `url` → `django.urls`, in the chosen style.
- **Every draft must:**
  - apply: the line holds the symbol at its column;
  - change only its own line;
  - parse: ast, for `.py` files;
  - resolve: Jedi, through `meridian.resolve.Repo`, lands every new name in real code. That means the repo itself, or for `url()` a Django 3.2 tree exported read-only from the mirror. Jedi's bundled Django stubs don't count: without that rule, any name "resolved".
- **Checked per file:** resolution is checked with all of a file's drafts applied together. A renamed call only resolves once its import is renamed too, just as in a real migration.
- **Owner habits copied:** an import that was sorted stays sorted (Django uses isort), and a name listed twice after the rename is dropped.
- **Scoring:** a line with no draft counts as unchanged. Whitespace is ignored. When the commit merged, split or moved lines, a draft counts only if it's one of that change's new lines. For an import, it can also be any of the file's new lines, since isort moves them.

## Results

| Bar | Result |
|---|---|
| Django ≥ 90% (05-test) | ✅ **254 of 267 lines = 95.1%.** 265 drafted. 2 refused (`code.sample` isn't Python). 13 misses: 2 non-Python lines, and lines where the real commit did more than rename (e.g. `widgets.py` switched to `_`, a multi-line import merged into one) |
| Packages ≥ 90% on the 12 finished repos | ❌ **73.2%** on the files the drafter wasn't shown (655 of 895), with the owner's style learned from their first files (decision 13) and whole statements (decision 14). 58.1% for the default style on the same lines. At the parent commit, with no owner lines: 58.6% (623 of 1,063; 677 of 1,236 = 54.8% over all 20). Django is unchanged at 254 of 267 |
| Draft touching another line refused | ✅ test. With decision 14 it's "another statement": a `url(...)` call over several lines is drafted whole. **Needs Aniket's OK**, since it changes the PASS-BARS §2.5 row; `--one-line` keeps the old rule and gives the old numbers |
| New name that doesn't resolve refused | ✅ test (no `gettext` in the module: all 4 drafts refused) |
| No LLM diff inside `except` | ✅ tests: `llm_allowed`, and a fixture run where the LLM is never called for an `except` site. The codemod may draft there; only the LLM may not |
| LLM off: codemod drafts, run completes | ✅ test. Rows load into the `suggestion` table (#30) |
| Hand-off (packets/README, Nov 9): `fact_gate.check` and `log_llm_call(..., facts_hash=)` on the fixture, LLM off | ✅ `Model-Pipeline/tests/test_handoff_diffs.py`: warm cache, 0 API calls, every LLM row's `facts_hash` in the `llm_calls` table |

## Decision 10: codemod or LLM

**Test.** Draft Django from the parent of `c651331b34`, and measure the share of drafts matching the real after-line, whitespace ignored.
**Rule.** If the codemod alone clears 90%, the LLM path is cut first (PLAN §9).

**Result.** 254 of 267 (95.1%).

**Pick, for Django: codemod only.** For the packages the codemod reaches 49.5%, which leaves the packages as the only place an LLM path could earn its keep. The LLM is allowed only when 04 §6's rule holds: 2 or more same-shape examples, and never inside `except`. That needs Vishwa's fact gate and cache, plus Rochan's `log_llm_call`.

## Decision 11: `url()` replacement

**Test.** On the 20 packages, compare drafts in each style with each migration commit's real lines.
**Rule.** Pick the style with the highest match. A single style that leaves any finished repo far behind means the choice has to be made per repo.

| Style | All 20 packages | Repos it fits |
|---|---|---|
| `re_path` always | 376 / 1,236 = 30.4% | cms 154/160, dj-rest-auth 50/51, two-factor 37/37, tastypie 17/31 |
| `path` when the pattern allows (converters `<int:>`, `<slug:>`, `<path:>`, `<str>`), else `re_path` | **612 / 1,236 = 49.5%** | wagtail 194/240, DRF 129/182, allauth 42/50, debug-toolbar 26/29, oscar 81/247 |
| aliased import (`re_path as url`) | 85 / 1,236 = 6.9% | autocomplete-light 66/67 |
| learned from the repo's earlier `django.urls` lines | same as `path` (none of the repos has earlier ones) | — |
| each repo's best style, known only after the fact | about 68% | — |

**Pick: `path` when the pattern allows, else `re_path`.** It's the style `--url-style path_if_plain` guesses with. `learned`, the default, reads the owner's style instead (decision 13) and waits when there's none (decision 15). Each real migration chose its own style (cms kept `re_path`, wagtail moved to `path`, autocomplete-light aliased), and nothing at the parent commit says which. So no single codemod style reaches 90%, and even an oracle choosing per repo stops near 68%. The rest are rewrites beyond a one-line swap: patterns turned into converters the rules don't cover, and lines merged, split or moved.

## Decision 13: learn each owner's style from their own lines (GAPS 3)

**Why the packages miss.** Of the 624 package lines the default drafts get wrong (Nov 11, team run):

| What the real commit did | Lines | One line can fix it? |
|---|---:|---|
| `re_path` where we drafted `path` (cms, dj-rest-auth, tastypie, two-factor) | 173 | only by knowing the owner's style |
| kept `url` through `re_path as url` (autocomplete-light, graphene, extensions, …) | 84 | only by knowing the owner's style |
| rewrote the hunk: lines merged, split or moved (oscar alone: 132) | 156 | no |
| deleted the line | 34 | no |
| a one-line `url()` rewrite the rules don't make (33 have the pattern on the next line, so `path(` would change 2 lines) | 120 | partly |
| wrote the import differently, other | 57 | partly |

The parent commit doesn't show the style. `path` and `re_path` both arrived in Django 2.0, and owners supporting the same Django versions chose differently: wagtail and DRF (≥ 2.2) went to `path`, dj-rest-auth and two-factor (≥ 2.2) kept `re_path`. And 190 lines (15.4%) aren't one-line changes at all, so **no one-line drafter can get above 84.6%**, with or without an LLM.

**Test, written before the run.** The owner migrates a repo's first `url()` files (by path) by hand, until their lines show 3 style votes or 3 files are read (`owner_seed`). The drafter takes the style those lines show (`style_from_owner`) and drafts the rest. It's scored **only on the files it wasn't shown**, against the default style on the same lines. The answer key is read only for the shown files.
**Rule.** Keep it if it beats the default by 10 points or more on the 12 finished repos.

**Result** (`test_packages_owner_seeded`):

| | default style | owner's style | best style per repo, picked in hindsight |
|---|---|---|---|
| 12 finished repos, held-out files | 464 / 895 = 51.8% | **600 / 895 = 67.0%** | 69.0% (all lines) |
| all 20, held-out files | 488 / 1,006 = 48.5% | **684 / 1,006 = 68.0%** | 68.6% (all lines) |

Biggest gains: cms 47 → 143 of 149, autocomplete-light 0 → 60 of 61, dj-rest-auth 6 → 33 of 33, tastypie 0 → 11 of 22. It reaches nearly the best a per-repo style can do. What's left is the hunk rewrites, deletions and two-line patterns.

Seeding until 5 votes (5 files) gives 68.9% on all 20; one file gives 57.3%. These are reported, not picked: the 3-vote rule was set first.

**Pick: keep it.** In the product it's `learned`. `style_vote` reads every line already on `django.urls` in the checkout (outside the repo's own `django/`), so once an owner migrates one file by hand, the next run drafts the rest their way. At the parent commit no package has such lines, so `learned` waits for the owner (decision 15). Django's own checkout has them, and its bar is unchanged (254 of 267).

**Also fixed while reading the misses:**
- `url(regex=...)` now drafts `re_path(r"...", ...)`. The old draft `re_path(regex=...)` was a TypeError, because `re_path`'s first argument is `route`.
- Any `/`-free character class (`[a-z0-9]+`) becomes `<str:…>`, as owners wrote it. It recovers few lines, since most of those lines were also split across lines.

**On GAPS' bar** ("≥ 90% on repos the codemod didn't learn from"): style is per owner, so learning from one repo can't carry to another (cms's `re_path` says nothing about wagtail). The held-out unit here is the files of the same repo that the drafter wasn't shown.

## Decision 14: draft the whole statement (options 1–3)

**Test, written before the run.** On top of decision 13, with the same held-out files:
1. A `url(...)` call over several lines is drafted as one statement. Its pattern may be on a later line.
2. The rewritten call goes on one line when it fits the repo's own limit (flake8 `max-line-length`, else 79).
3. The `django.urls` import takes its names from what the file's drafted calls use.

**Rule.** Keep it if it beats decision 13 on the 12 finished repos.

**Result** (scratch run, then the product code):

| Held-out files | 12 finished repos | all 20 |
|---|---|---|
| decision 13 alone | 600 / 895 = 67.0% | 684 / 1,006 = 68.0% |
| + 1 | 597 = 66.7% | 695 = 69.1% |
| + 1 + 2 | 647 = 72.3% | 744 = 74.0% |
| + 1 + 2 + 3, in `diffs.py` | **655 = 73.2%** | **753 = 74.9%** |
| 1 + 3, joining only if the owner's seed files did | 597 = 66.7% | 695 = 69.1% |

Joining does it. Oscar goes from 75 to 154 of 237, because its owners rewrote multi-line calls as one-line `path(...)` calls. It costs cms (143 → 125) and two-factor (14 → 5), whose owners kept calls multi-line. Reading the join habit from the seed files lost: oscar's first files show none, so it kept the layout and lost 79 lines. It's reported, not used.

**Pick: keep 1–3.** A call with a comment inside isn't joined. A trailing comma is dropped only at a line break, so `(1,)` stays a tuple. When the call doesn't fit, `path` is used only if the pattern line can say it where it is; otherwise it's `re_path`. A draft still changes nothing outside its own lines (`only_span`), and must parse and resolve. **This changes PASS-BARS §2.5's "a draft that changes any other line is refused" to "any other statement", so it needs Aniket's OK.** `--one-line` (or `statements=False`) drafts the old way and gives the old numbers.

## Decision 15: don't guess; learn from the owner

A wrong diff costs the owner more than no diff. So for `url()` the drafter doesn't guess a style:

1. **Wait.** With style `learned` (the default), no owner lines means no `url()` draft. Those sites are refused as `style_unknown`. Renames (`ugettext` → `gettext`) have no style to choose and still draft. `--url-style path_if_plain` keeps the old guess, for evaluation.
2. **Copy.** Once an owner migrates a file by hand, the checkout's own `django.urls` lines vote (decision 13), and the next run drafts the rest their way.
3. **Learn from edits.** A diff whose newest `outcome` is `applied` or `edited` gives a pair: our before-line and the line the owner really wrote, read with `git show` at the evidence commit. Those pairs outrank the checkout's lines. A diff the owner `rejected` is never offered again with the same facts (`rejected_before`).

The evidence format the drafter reads: `outcome.evidence` is `{"sha", "path", "line"}` (or a list of them), the commit and line where the owner's line landed. No outcome writer exists yet, so it should write this.

**Measured:** 1 and 2 give the held-out numbers above: 73.2% on the 12 finished repos when the drafter has the owner's style, and no `url()` draft before that. **3 is built and tested on a fixture** (`OwnerFirst` in `test_diffs.py`), but there are no real outcomes yet to measure it on.


The 244 misses left on the 12 finished repos, sorted by comparing each draft with the real lines (Nov 11):

| | Lines |
|---|---:|
| **We drafted the migration differently from the owner** | **230** |
| ↳ the pattern's converter or escaping (e.g. `<object_pk>` for `.+`) | 48 |
| ↳ we wrote `re_path`, the owner `path`, loosening the regex (e.g. `\-?\d+` → `<user_id>`) | 51 |
| ↳ we joined, the owner kept the lines; or the other way | 63 |
| ↳ we wrote `path`, the owner kept `re_path` (DRF, on some lines) | 14 |
| ↳ the import's names, or an import the owner deleted | 44 |
| ↳ guardian's `url` from `guardian.compat`: the codemod refuses those lines | 11 |
| **The owner did more than migrate** (routes changed, names added, lines deleted) | **14** |

The earlier guess was wrong: owner restructuring is only 14 lines (5.7%), so a "migration-only" score would barely differ. What's left is owners choosing line by line: `path` on one line and `re_path` on the next, joined here and kept there, a strict converter here and a loose one there. The parent commit doesn't show those choices, and a few seed files can't settle them.


Built on Vishwa's cache and fact gate (#28, #48) and Rochan's `log_llm_call` (#32), the same way the writer uses them.

- **Which lines.** Only a line the codemod refused (`multi_line_import`, `other_names`, `parse`, `resolve`, `other_line`), when every site on it passes `llm_allowed`. That means 2 or more same-shape examples and never inside `except`. The examples are the repo's own codemod drafts with the same (symbol, role) set, which passed every check. The prompt shows 3 of them.
- **Each answer.** `llm_cache` or a call, then JSON `{"text", "used"}` (the writer's `parse`), then the fact gate (decision 12), then the codemod's own checks: one line, parses, resolves. Any failure moves one model up: coding small → mid → large (04 §6). If the top model fails, nothing is drafted, because code has no template.
- **LLM off.** No LLM and no cache: the codemod runs alone, as before. Cache only (`--llm-cache`, as in CI): a hit drafts, a miss drafts nothing, or with `--strict-cache` the run stops.
- **Tracking.** Every answer, cached or called, goes to `log_llm_call` with `agent diff_drafter`, `route rules`, `task diff` and the row's `facts_hash`. Codemod drafts aren't LLM calls and aren't logged. An LLM row in `suggestion` has `source llm` and the model id.
- **Privacy.** A line whose facts name a person (`people` from `person` and `person_alias`, by the writer's rule) is never sent. No prompt or reply text reaches MLflow.

**What it can reach.** On the team run the codemod refuses 20 of the 1,236 package lines: 14 `resolve`, 4 `multi_line_import` and 1 `other_names` in django-guardian, and 1 `resolve` in debug-toolbar. Django has 2 `not_python` refusals, which the LLM may not try. So under 04 §6's rule the LLM path can add at most 20 lines, 1.6 points, to the packages' 49.5%. The packages' gap is the `url()` style each migration chose (decision 11), not lines the codemod can't write. This is the result decision 10 predicted: the LLM path isn't what closes the packages bar, so it stays small, and it's first in the cut order if it causes trouble.

## Decision 12: the fact gate on a line of code

**Test.** Run `fact_gate.check` on code lines: an unchanged copy of a line, a `path()` conversion, an invented number, and a comment naming a person, an email or a ticket.
**Rule.** The gate must pass every correct draft and stop every invented fact.

**Result.** Gated as text, the whole after-line fails even an unchanged copy. `\d{4}` reads as the number 4, and `edit\.html` reads as a path that isn't in the facts. Gating only what the draft **adds** (its tokens that aren't on the before-line) with `kinds="numbers"` passes the `path()` conversion. It stops the invented number and the person or email. It can't see a ticket key or a team in a new comment, so a new comment is refused outright.

**Pick:** gate the added tokens with `kinds="numbers"`, and allow no new comment. A person, an email or a handle always fails (the gate's own rule).

## What's left

- Writing to the real block: `python -m meridian.diffs --run-id c5118b019d642a9b --config meridian.toml` writes `out/c5118b019d642a9b/suggestion.jsonl`. #30 is merged now, so this can run. All the evaluation runs wrote to a scratch folder.
- Diff match rate per repo and per team, for Chaitali's LLM bias row: the per-repo table above is the input.
