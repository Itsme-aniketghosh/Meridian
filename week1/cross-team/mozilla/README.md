# Cross-team · Mozilla bug 922464 (nsIURI thread-safety)

Step 1 of [NEXT-STEPS](../NEXT-STEPS.md) for the pick both reports favoured for stalls. Queried 2026-10-03 from bugzilla.mozilla.org's public REST API (read-only). The counts are printed by [mozilla_step1.py](mozilla_step1.py). The "real wait?" calls are a hand read of every keyword hit and every downstream bug.

## Verdict

- **Is it cross-team? Not the 43 children. A little, in the 7 downstream bugs.**
- **The 43 "dependencies" are one engineer's to-do list,** not cross-team work. 41 of 43 are in the same team (Core::Networking) and 37 are assigned to the same person. Median **9 days** to resolve.
- **The cross-team part is the other direction:** the 7 bugs the meta bug *blocks*. They sit in 6 other teams (DOM, CSS, Workers, Security, XPCOM, Graphics), and **3 show a real wait** on the migration, plus 1 possible.
- **Far short of the bars.** About 3–4 real blocks (need 30+) and about 7 stall cases, mostly abandoned follow-ups with no stated reason (need 30+).
- **Most useful lesson:** in Mozilla, look at a meta bug's **blocks** list, not its **depends on** list. "Depends on" is the project broken into pieces. "Blocks" is other teams waiting for it. This is the only one of our three datasets where the links point the way Meridian's `blocked_by` does.

## What it is

Firefox's code is one large repo (mozilla-central) shared by many teams. Teams are Bugzilla **components** (product::component, e.g. `Core::Networking`), each with its own owners. So this tests **team boundaries inside a monorepo**, not chains between repos.

Bug 922464 is a **meta bug** (an umbrella for a project), "Centralize URI parsing and make it threadsafe", filed 2013-10-01 and resolved 2019-06-07. It made Firefox's URL objects (`nsIURI`) immutable and safe to use off the main thread, so callers across the codebase had to move off the old in-place setters. In Meridian terms: Networking = API owner, `nsIURI` setters = the dying API, other components = consumer teams.

## What we did

1. **Fetched the meta bug** and both link lists:
   - **children:** the 43 bugs it depends on
   - **downstream:** the 7 bugs it blocks
2. **For each of the 50 bugs,** fetched fields, full history and all comments.
3. **Time to resolve:** filed → `cf_last_resolved`. Unlike Debian, Bugzilla records a real resolved date.
4. **Team per bug** = product::component. Counted how many fall outside the meta bug's team.
5. **Looked for waits:** a keyword scan of human comments, then **read every hit in context by hand**, plus all 7 downstream bugs.

## The numbers

| What it tells you | Need | We got | |
|---|---|---|---|
| Child bugs | 30+ | **43** (report said 43) | ✅ |
| Children outside the owner team | most | **2 of 43**. 41 are Core::Networking | 🔴 |
| Different people doing the work | many | **37 of 43** assigned to one engineer. 5 unassigned | 🔴 |
| Children that sat open 90+ days | a real share | **2 of 39** resolved. Median **9 days**, slowest 10% over 69 | 🔴 |
| Children still open | — | **4**, all unassigned, filed 2017–18, 1–4 comments each | 🟡 |
| Downstream bugs (other teams waiting on this) | 30+ | **7**, in **6** different teams | 🟡 |
| Real cross-team waits (hand-checked) | 30+ | **3 confirmed + 1 possible** | 🔴 |
| Keyword hits that were real waits | most | **2 of 5.** The other 3 were a code description, a build log and a same-team test note | 🟡 |
| Stall cases with a known reason | 30+ | **1** (1443925, waited on upstream). The rest are unassigned, with no stated reason | 🔴 |

**How to read it:**
- **🔴 The children aren't a migration across teams.** They're one person's work broken into tickets, and it moved fast.
- **🟡 The downstream bugs are the right shape:** other teams, explicit "wait for this to land", and a 4.7-year case. But there are only 7.

## The 7 downstream bugs (hand-checked)

| Bug | Team | Status | Days | Real wait on the migration? | Evidence |
|---|---|---|---|---|---|
| [1443925](https://bugzilla.mozilla.org/show_bug.cgi?id=1443925) Make nsIPrincipal objects threadsafe | DOM: Security | Fixed 2022-12-02 | 1,730 | ✅ Yes | 2018-03-19: "we definitely want to wait for nsIURI thread-safety to land and stabilize first". Depends on 922464 |
| [1432481](https://bugzilla.mozilla.org/show_bug.cgi?id=1432481) expose base URL on nsIGlobalObject once we have a thread-safe way to do so | DOM: Core & HTML | Open since 2018-01-23 | — | ✅ Yes | The title states the wait. The meta bug resolved in 2019 but this never resumed: **a stall after unblock** |
| [1452947](https://bugzilla.mozilla.org/show_bug.cgi?id=1452947) Get rid of useless PtrHolder / PtrHandle dance in style now that URIs are thread-safe | CSS Parsing | Fixed in 1 day | 1 | ✅ Yes | The title says the work was unlocked by the migration ("now that URIs are thread-safe") |
| [1448328](https://bugzilla.mozilla.org/show_bug.cgi?id=1448328) Most URLWorker methods don't need to be proxied to the main thread anymore | DOM: Workers | Fixed in 21 days | 21 | 🟡 Possible | Depends on 922464. 2018-04-10: "are we waiting for bug 1443925?" (a question, not a statement) |
| [1347507](https://bugzilla.mozilla.org/show_bug.cgi?id=1347507) [meta] Stuff we can remove when legacy extensions are no longer supported | XPCOM | Open since 2017 | — | ❌ No | Another meta bug, a cleanup list |
| [1558361](https://bugzilla.mozilla.org/show_bug.cgi?id=1558361) eagerly create nsIURI objects during restyling | CSS Parsing | Open since 2019-06-10 | — | ❌ No | Filed after the migration finished: a follow-up idea, not a wait |
| [2002619](https://bugzilla.mozilla.org/show_bug.cgi?id=2002619) Consider removing gfxFontSrcURI | Graphics: Text | Open since 2025 | — | ❌ No | Filed six years later: cleanup |

## Keyword hits, read by hand

| Bug | Team | Comment | Real wait on another team? |
|---|---|---|---|
| 1416791 | Networking | "…while we are waiting for the timer to delete the data…" | ❌ Describes how the code works |
| 1532253 | Networking | "mozmake: *** Waiting for unfinished jobs…" | ❌ A build log line |
| 1536744 | Networking | "These should be removed once bug 1553105 lands" | ❌ A test cleanup note, same team |
| 1443925 | DOM: Security | "wait for nsIURI thread-safety to land and stabilize first" | ✅ Yes |
| 1448328 | DOM: Workers | "are we waiting for bug 1443925?" | 🟡 Possible |

**This matters beyond Mozilla:** 3 of 5 keyword hits were noise. The Debian and OpenStack wait checks are keyword scans too, so treat their counts as rough until someone reads them.

## Stall material

| Bug | Team | Open | Why it stalled |
|---|---|---|---|
| 1443925 | DOM: Security | 1,730 days | Waited on the nsIURI migration (upstream), then other prerequisites |
| 1432481 | DOM: Core & HTML | open since 2018 | Unblocked in 2019, never resumed. Unassigned, so the reason is unknown |
| 1440191 | Networking | 1,863 days | Closed WORKSFORME in 2023: a nice-to-have that got overtaken |
| 1425889, 1434507, 1456088, 1459861 | Networking (+ Cache) | open since 2017–18 | Unassigned follow-ups, 1–4 comments, no stated reason |
| 1416791 | Networking | 128 days | Resolved. No wait stated |

About 7 cases. Only **1** has a reason someone wrote down. Per NEXT-STEPS, inactivity alone isn't a reason, so the rest are `unknown`.

## Caveats

- **Monorepo.** Teams are Bugzilla components, not repos. That tests Meridian's team boundaries but not package-release chains.
- **Team = component of the bug.** A bug filed in Networking can still touch code another team owns. We didn't check patches.
- **Only one meta bug.** Mozilla has thousands. The "look at blocks, not depends on" pattern might scale, but we haven't measured it.
- **Hand calls** were made by one person, reading the comment around each hit and each downstream bug's title and dependencies. They haven't been double-checked.

## What to do with it

- **Don't use the 43 children** as cross-team blocks or stalls.
- **If we want Mozilla for #5,** mine many meta bugs' **blocks** lists across Core components. Keep a bug only if it's in a different component, filed before the meta bug resolved, and has a wait in its title or comments. That's the 3-check process in NEXT-STEPS step 2.
- **For #7,** the 1443925-style case (a long wait with the reason written down) is what we want, but here we found only one.

## Reproduce

Python 3.9+, with `pip install requests`. Run from this folder:

```
python mozilla_step1.py
```

It prints every count above and the raw keyword hits, and writes `mozilla_bugs.csv` (git-ignored): one row per bug with role, team, status, dates, days open, assignee, comment count, wait hits and a link. The script retries failed requests, because Bugzilla sometimes times out.
