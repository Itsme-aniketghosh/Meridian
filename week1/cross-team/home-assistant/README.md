# Cross-team · Home Assistant (home-assistant/core)

**The question:** can Home Assistant fill the gaps the week-1 datasets left? It's a Python monorepo where each integration names its own owners, explored as a new source after GitLab. Neither research report picked it.

**The data:**
- Pulled on 2026-10-02 and 03 from GitHub (GraphQL through `gh`), PyPI, and a full copy of the code history.
- **Code snapshot:** `dev` at `75c314edf0` (2026-10-01).
- **Tickets:** issues and PRs created 2025-01-01 to 2025-07-01 (5,706 issues, 7,730 PRs).
- We only read data; nothing was changed.
- Every number below comes from a script in this folder.
- Every "was it really stuck?" answer comes from reading the PR's full timeline.

## The short answer

- **Yes, HA fills two gaps no other dataset covers:**
  - **Method calls for the resolver.** Django (Pair 1) had only 30 method-call uses, in untyped code, and failed the 05-test bar (Jedi 5, pyright 2). HA has **395** uses of one deprecated method, mostly typed. Jedi gets **390**, pyright **384**, with **0 wrong**. GitLab can't do this: it's Ruby, and our tools only read Python. See [Method calls, strict](#method-calls-strict).
  - **#3, stale owners.** On 33% of integrations, no listed owner wrote or reviewed anything in a year. It's the strongest of our sources.
- **It's a second source for #7, why work got stuck.** 602 PRs sat idle 30+ days. Of 40 read by hand, 37 were really stuck and 32 have a written reason. The main reason is **waiting on an owner or core reviewer (24 of 40)**, which complements GitLab's "waiting on upstream work".
- **It does not fill #5, teams blocking each other.**
  - The owners are cross-team: 698 import pairs cross owners.
  - The blocks aren't. All **11** real in-repo blocks are one author waiting on their own earlier PR, and **51 of 57** library waits are on the owner's own library. Only **about 7** involve a different maintainer.
  - GitHub's native `blockedBy` is never used.
- **It's weak for #1, #2 and #6.** Only 12.7% of PRs name an issue. #4 (risky files) shows the same tie as GitLab: churn alone predicts as well as bug history.
- **One warning:** a model read the 40 stalled PRs and the 30 method-call sites, not a person. A person should spot-check about 5 of each before anything is called gold.

## What it is

- **Home Assistant** is open-source home-automation software, written in Python.
- **`home-assistant/core`** holds the core plus 1,523 **integrations**, one folder per device brand or service (`hue/`, `sonos/`, …).
- **Owners:** each integration's `manifest.json` lists its `codeowners` (GitHub handles). Core code is owned by a core team.
- **Code edges:** integrations call shared integrations (`http`, `bluetooth`, `diagnostics`, …) and their own PyPI libraries.
- **In Meridian terms:**
  - repo = monorepo
  - integration = team's code
  - codeowners = team
  - issue or PR = ticket
  - shared integration or library = the shared client
- So it tests team boundaries inside a monorepo, like Mozilla and GitLab, plus library chains out to PyPI.

## What we did

1. **Pulled every issue and PR in the window** with timelines, reviews and labels. We fetched by number in batches of 25, because GraphQL `search` silently trims timelines.
2. **Dropped bots.** These are dependabot, the stale bots (`issue-triage-workflows` for issues, `github-actions` for PRs), the CLA bot, and `copilot-pull-request-reviewer`.
3. **Stalls:** a PR stalled if it sat 30+ days with no activity before merge or close.
   - Hand-read a seeded 40 (`random.seed(1)`).
   - Coded with the NEXT-STEPS reasons.
   - Cross-team = waiting on someone who isn't the author or a codeowner of the touched integration.
4. **Blocks, looked for three ways:**
   - **In-repo text:** "depends on", "blocked by", "after #N is merged", …
   - **Library chains:** feature PRs that waited on a library bump. For each library, we compared its GitHub owners and top contributors (via PyPI) with the integration's codeowners.
   - **Shared-client edges:** pairs of integrations with no owner in common, where a PR touched both sides.
5. **Checked the other five failures and a resolver benchmark** on the code and tickets. See [Beyond blocks and stalls](#beyond-blocks-and-stalls).

## The numbers

| What it tells you | Need | We got | |
|---|---|---|---|
| Integrations with named owners | many teams | **1,116 of 1,523**, 769 people | ✅ |
| Code edges between different owners | a real share | `dependencies` 326 of 530. Imports (non-platform) **698 of 966** pairs | ✅ |
| Shared-client edges, a PR touching both sides | a real share | **19 of 610** edges (50 PRs) | 🔴 |
| In-repo block phrases → real blocks | 30+ | 190 hits → 80 blocker phrases → **11 real, all same author**, median 6.9 days | 🔴 |
| GitHub native `blockedBy` | any | **0** for the whole repo | 🔴 |
| Feature PRs that waited on a library bump | 30+ | **35**. 23 same author, median 1.4 days | 🟡 |
| Library maintained by the integration's own owner | — | **51 of 57** (89%) in a seeded 60 bump PRs | — |
| Library waits on a different maintainer | 30+ | **about 7** (paho-mqtt, aiohttp, uiprotect, elevenlabs), median 8.3 days | 🔴 |
| Stalled PRs (idle 30+ days) | 30+ | **602 of 7,651** (7.9%) in 355 integrations | ✅ |
| Stalled PRs really stuck (hand read) | most | **37 of 40** (23 fully, 14 partly) | ✅ |
| Reason readable | most | **32 of 40** | ✅ |
| Stalled PRs waiting on a non-owner | a real share | **15 of 40**, almost all core reviewers | ✅ for #7 |
| Stall rate, owner authors vs non-owners | — | 4.9% vs **10.6%** | ✅ |
| PRs with no review for 14+ days | — | 447 (5.8%) | ✅ |
| Issues marked stale by the bot | — | 2,249 of 5,694 (39.5%). 1,960 closed, 60% with no maintainer reply | 🟡 inactivity only |

**How to read it:**
- **✅ Stalls are real and explained.** The review thread says who was waited on and why.
- **🔴 Blocks are self-blocks.** People split their own work, or bump their own library first. Code edges between owners are everywhere, but almost no PR touches both sides, so nobody was waiting.

## The 40 hand-read stalled PRs

`stall_hand_check_40.csv`

| Reason | PRs | Detail |
|---|---:|---|
| owner | 24 | review wait 14, design or architecture decision 10 |
| unknown | 8 | changes requested, the author never replied 7, no trace 1 |
| upstream | 4 | waiting on another PR 2, frontend or docs 2 |
| capacity | 2 | deprioritized, only where the author said so ("My free time :)") |
| other | 2 | planned design waits |

- **Really stuck:** 23 y, 14 partly (the work stopped on the author's side), 3 n (planned).
- **Cross-team:** 15 y, all on reviewers or maintainers. 0 on another integration's owners.

**Patterns:**
- **Core reviewers are the bottleneck.** Codeowners can't merge their own integration's PRs. Examples: [#145387](https://github.com/home-assistant/core/pull/145387) "waiting for two months for someone to tell me what to do next", [#135009](https://github.com/home-assistant/core/pull/135009) "any chance of feedback".
- **Architecture decisions park PRs for months.** Example: [#145848](https://github.com/home-assistant/core/pull/145848) "discussed in the architecture meeting, but it was forgotten to update here".
- **The author's turn, then the stale bot.** Changes requested, no reply, closed after 60+7 days: [#139888](https://github.com/home-assistant/core/pull/139888), [#136660](https://github.com/home-assistant/core/pull/136660).

## How it compares

| | Debian | Mozilla | OpenStack | GitLab | **Home Assistant** |
|---|---|---|---|---|---|
| Owners per unit | maintainers | components | project teams | ~100 `group::` | **769 people, 74% single** |
| Waits read by hand | keyword, 0 of 13 | ✅ 3 of 7 | keyword | ✅ 57 | ✅ **40** |
| Real cross-team waits (#5) | 0 | 3–4 | 0 | 5 (about 50 projected) | **0 in-repo, about 7 via libraries** |
| Stalls with a written reason (#7) | — | 1 | 0 | 50 of 57 | **32 of 40** |
| Main stall reason | — | upstream | — | upstream, same team | **owner or reviewer** |

## Beyond blocks and stalls

The other failures from [01-problem](../../../docs/01-problem.md), checked the same way Pair 2 checked Spark:

| # | Failure | HA? | Evidence |
|---|---|---|---|
| 3 | Stale owners | ✅ **Strongest source** | On **285 of 871** integrations (33%), no codeowner wrote or reviewed anything for 12 months. 300 of 685 owners inactive. On **46%** of issues the first human reply is a non-owner. Owner rule **26.1%** vs last toucher 20.5% (N = 964). Codeowners hit 31.3% where listed |
| 1 | Consumers find out late | 🟡 Outside the repo | **187** removed deprecations, median 10.8 months of warning. In-repo, one core dev sweeps 82–97% of sites, often a year before the warning. Custom integrations (seeded 147 from HACS): **10 of 41** fixed only after the removal (median 26–233 days late), 3 still broken |
| R | Resolver: method calls | ✅ **Passes Pair 1's bar** | Strict rerun, see [Method calls, strict](#method-calls-strict): 395 uses, all removed by the deletion commit. Jedi **390**, pyright **384**, **0 wrong**. Misses are untyped `hass` only. The loose first pass (389 sites): Jedi 386, pyright 380, 0 of 62 on dynamic `hass.helpers.*` |
| 4 | Risky files | ✅ With a catch | 3+ bug fixes → **7.3x** next-year fixes (8,436 files, 310 risky). Churn alone gets **7.4x**. GitLab shows the same tie |
| 6 | Adds while others remove | 🟡 Weak | 16 integrations first used a deprecated API after it was deprecated. 9 were new integrations, 15 added by their own owner |
| 2 | Ticket says done, code disagrees | 🔴 Weak | Only **12.7%** of PRs name an issue (Spark 94%, GitLab 76%). 15 of 20 "done, no PR" closes were support answers |

Other numbers:
- **Bots:** 1.0% of merged PRs.
- **Identities:** 7.4% are duplicates. Use GitHub logins, since git emails give a login only 40.6% of the time.
- **Duplicates:** almost never link the original (7 of 203).

## Method calls, strict

Pair 1's Django method-call test had 30 uses in untyped code (Jedi 5, pyright 2). This reruns it on HA with Pair 1's rules and scripts: their tool versions (tree-sitter 0.23.2, jedi 0.19.2, pyright 1.1.414), their language-server client, and their checks against git.

- **Method:** `ConfigEntries.async_setup_platforms`, called as `hass.config_entries.async_setup_platforms(...)`.
- **Conversion commit:** `cd03c49fc27` (2022-07-09, #73806) deprecated it and rewrote most uses in one go, like Django's `c651331b34`.
- **Removal commit:** `739963b5ee1` (2023-04-23, #91929) deleted it, so every real use had to be gone by then.
- **Snapshot:** the conversion's parent, `3a5cca3ff2`. All 8,417 `.py` files, tests included, 0 parse errors.

| What it tells you | Need at least | We got | |
|---|---|---|---|
| Uses found by tree-sitter | — | **395** in 392 files: 389 integrations, 4 scaffold templates, 2 tests. 394 calls, 1 `.mock_calls` read | — |
| Uses the conversion commit changed | — | 365 of 395. The other 30 were changed in later PRs | — |
| Uses gone by the removal commit | all | **395 of 395** | ✅ |
| Changed lines naming the method that the scanner missed | 0 | **0** | ✅ |
| Jedi recall | ≥ 0.95 | **390 of 395** (98.7%) | ✅ |
| pyright recall | ≥ 0.95 | **384 of 395** (97.2%) | ✅ |
| Precision (right ÷ answered) | ≥ 0.98 | **390 of 390** and **384 of 384**. 0 wrong for both | ✅ |
| Hand check of a seeded 30 | 30 of 30 | **30 of 30** real uses, **checked by Claude**. A person still has to spot-check (`method_check.py`) | 🟡 |
| Time | — | Jedi 2.1 s, pyright 1.8 s for all 395 | ✅ |

**Stricter than Pair 1 in one way:** "right" means landing on `homeassistant/config_entries.py:1157` only. `zwave_me/__init__.py:84` defines its own module-level `async_setup_platforms`, called bare. Tree-sitter doesn't count it as a use, and landing on it would count as wrong. Pair 1 accepted any definition of the name.

**Why the misses happen** (`method_receivers.py`): every right answer came when the tool knew the receiver was a `ConfigEntries`, and every miss when it didn't. That's Pair 1's Django result again.

| | Knew the receiver | Didn't know |
|---|---|---|
| Jedi | 390 right | 5 no answer |
| pyright | 384 right | 11 no answer |

- **Both missed 5:** `self.hass` set from an untyped `__init__(self, hass, config)` (broadlink, roon, unifi), and 2 test mocks.
- **Only Jedi got 6:** the same untyped `self._hass` / `self.hass` (gdacs, geonetnz ×2, glances, islamic_prayer_times, transmission). Jedi infers the type from where the class is built in the same file; pyright doesn't.
- **Typed `hass: HomeAssistant` parameters resolve every time** (379 of 382 `hass.config_entries` calls for both).

**Django vs HA:**

| | Django (Pair 1) | HA |
|---|---|---|
| Uses | 30 | 395 |
| Typed? | No | Mostly |
| Jedi / pyright right | 5 / 2 | 390 / 384 |
| Wrong | 0 / 0 | 0 / 0 |
| Misses explained by an unknown receiver | all | all |

**What it means for 05-test:** the resolver row ("one method-call deprecation, recall ≥ 0.95, precision ≥ 0.98") passes on HA for both tools. Jedi stays ahead, 390 vs 384, as on Django. Keep Pair 1's rule of keeping unbound name matches as `ast_unresolved`: with it, all 395 are found.

**The seeded 30** (`method_hand_check_30.csv`, `random.seed(1)`, saved before checking):
- **30 of 30** are real calls of `hass.config_entries.async_setup_platforms`. 0 comments, strings, or the zwave_me function.
- All 30 were gone by the removal commit. 23 of them were changed by the conversion commit.
- Jedi got 30 of them right, pyright 29. pyright missed gdacs's untyped `self._hass`.
- The sample happens to include all 4 scaffold templates (`script/scaffold/templates/`). They count as real: they're the code copied into every new integration, so they had to change. The seed was fixed before looking, so they stay.
- **Caveat:** Claude read each line with its context, not a person, and no time was recorded. Pair 1's week-4 timing (0.31 min per site) can't be checked here until a person runs `method_check.py`.

## Caveats

- **Checked by Claude, not by a person.** The 40 stalled PRs and 20 closed issues were judged by Claude reading each full API timeline. A human should spot-check about 5 of each. The line between "partly" and "unknown" is a judgement call.
- **Library ownership is approximate.** It counts the PyPI project's GitHub owner and top 3 contributors. 5 of the 7 "different maintainer" cases were spotted by hand where PyPI didn't match (paho-mqtt, aiohttp). Each text search was capped at 300 results, so the 35 library waits is a lower bound.
- **Owners come from the 2025-04-01 manifests.** Integrations added later in the window show no owners. "Inactive owner" counts only merged PRs authored or reviewed, not comments, so 43.8% is an upper bound.
- **Timelines are capped at 100 items.** 116 PRs and 22 issues are longer, so their gaps may be undercounted.
- **Custom integrations:** "removal" is the dev commit date, about a month before the release. Only 63–81 of 147 repos have history from before the removal.
- **Traps.**
  - The two stale bots are different accounts.
  - The CLA bot posts "reviews".
  - Third-party `Units.TEMP_CELSIUS` matches `TEMP_*` regexes.
  - `git log -G` is not PCRE.
  - The git committer is GitHub's web merge on 99.9% of commits.

## What to do with it

- **For #5 (blocks):** don't use HA. Its waits are self-blocks or reviewer waits. The library-chain idea is real but mostly the same person: 89% own their library.
- **For #7 (stalls):** use the PR side. The 40 rows are a gold-set start, after a human spot-check. "Waiting on owner or reviewer" complements GitLab's "waiting on upstream, same team".
- **For #3:** use HA as the main ownership dataset.
- **For the method-call gap:** Django (Pair 1) had 30 untyped uses and failed the 05-test resolver bar. HA fills that gap: the strict rerun passes recall ≥ 0.95 and precision ≥ 0.98 for both tools, and Claude's read of the seeded 30 found 30 of 30 real. A person should run `method_check.py` before calling it gold.
- **For #4:** add a churn baseline to 05-test. Bug history ties churn here and in GitLab.

## Reproduce

Python 3.9+, `gh` logged in, `pip install jedi` (and pyright in a venv for the resolver). Run from this folder:
- Clone first: `git clone --single-branch --branch dev --no-checkout https://github.com/home-assistant/core.git data/repo` (929 MB, about 30 s).
- Then run `mkdir -p data/code data/tix data/stall`.
- `data/` is git-ignored.

```
# Stalls and blocks (#7, #5)
python3 stall_fetch.py && python3 stall_prs.py && python3 stall_issues.py
python3 stall_handcheck_dump.py && python3 stall_handcheck_codes.py && python3 stall_block.py

# Owners and tickets (#3, #2)
python3 tix_pull.py all && python3 tix_identities.py && python3 tix_owners.py --users && python3 tix_tickets.py

# Code (#1, #4, #6, resolver)
python3 code_deprecations.py && python3 code_burndown.py && python3 code_migration.py
python3 code_hacs_clone.py && python3 code_hacs_usage.py
python3 code_bugfix_prs.py && python3 code_fragility.py && python3 code_quality_scale.py
git -C data/repo worktree add --detach ../wt_setup_platforms a697672944b8bbdd32e3dad8eca93bd9ae8b40a0
git -C data/repo worktree add --detach ../wt_get_registry 4f6b2e6e1cdd46af29589be2aceb05176c199560
for s in async_setup_platforms async_get_registry; do for t in jedi pyright; do python3 code_resolve.py $s $t; done; done

# Method calls, strict (Pair 1's rules; venv: pip install tree-sitter==0.23.2 tree-sitter-python==0.23.6 jedi==0.19.2 pyright==1.1.414)
python3 method_sites.py && python3 method_resolve.py && python3 method_receivers.py
python3 method_pick.py && python3 method_check.py      # method_check is interactive and timed, for a person

# First quick check (run from inside data/repo)
python3 ../../burndown.py 'async_forward_entry_setup\(' 2022-01 2025-06
python3 ../../deps.py 75c314edf0 && python3 ../../depends_on.py 75c314edf0
```

| Script | What |
|---|---|
| `stall_*.py` | PR and issue timelines, stalls, the seeded 40, in-repo and library blocks |
| `tix_*.py` | Issues and PRs, identities, owners (#3), closed-vs-done (#2), labels |
| `code_*.py` | Deprecations, burndowns, custom integrations, fragility, adders, resolver |
| `method_*.py` | Method calls, strict: sites vs both commits, Jedi and pyright, receivers, seeded 30 and the timed hand check |
| `burndown.py`, `deps.py`, `depends_on.py`, `resolve.py` | The first quick check |

Result sheets (CSV), kept on Vishwa's machine only and not in git (ask Vishwa for them):
- `stall_hand_check_40.csv`, plus `stall_block_*.csv`
- `tix_closed_no_pr_20.csv`
- `code_resolve_*.csv`, `resolve.csv`
- `method_sites.csv`, `method_defs.csv`, `method_grep.csv`, `method_resolution.csv`, `method_receivers.csv`, `method_hand_check_30*.csv`
- `code_hacs_usage.csv`, `code_adders.csv`, `code_deprecations.csv`, `code_migration_summary.csv`
