# Cross-team · Debian py2removal

Step 1 of [NEXT-STEPS](../NEXT-STEPS.md) for the Claude report's #1 pick. Queried 2026-10-02 against the public UDD mirror and the Debian BTS (both read-only). Every number below is printed by a script in this folder.

## Verdict

- **Is it cross-team? Yes, as dependency data. No, as a block answer key.**
- 69% of block links join packages with different maintainers, and the links come from real package dependencies. So check 1 (code edge) mostly holds.
- But the links were bulk-added by one person from the dependency graph, they point the opposite way from Meridian's `blocked_by`, and the package bugs we sampled show no one saying they waited. Check 3 fails.
- **Use it as a pool of real cross-team edges and slow bugs to hand-label. Don't grade Meridian against its block links.** That would be circular: we'd be testing that Meridian can read a dependency graph.

## What it is

Debian removed Python 2 in 2019–2021. Its Python team filed one bug per affected source package (usertag `py2removal`) and linked them with "blocked by". Package = repo, maintainer = team, bug = ticket.

## The numbers

| What it tells you | Need | We got | |
|---|---|---|---|
| Bugs in the campaign | 100s | **3,480**, all closed (report said ~3,477) | ✅ |
| Bugs with a block link | a real share | **1,034 blocked (30%)**, 2,407 block something. Spark: 2% | ✅ |
| Package → package links | 30+ | **8,575** | ✅ |
| Links that cross teams (different maintainer) | most | **5,917 (69%)**. 8% both Debian Python Team, 8% same maintainer, 15% maintainer unknown | ✅ |
| Distinct teams | many | **793** maintainers across 3,237 packages | ✅ |
| Links added by hand, one at a time | most | **0 of 20** sampled. All 20 by `morph@debian.org`, 19 in two batches (15 on 2019-10-21, 4 on 2019-10-23), median 20 blockers per command | 🔴 |
| Links point the way Meridian's do | yes | **No.** The library's bug is blocked by its users' bugs (see below) | 🔴 |
| Circular links (A ← B and B ← A) | 0 | **97** | 🔴 |
| Sampled package bugs that say someone waited | a real share | **0 of 13**. The only hits were in the python-defaults meta bug | 🔴 |
| Slow bugs (stall material) | a real share | median **135 days** filed → last activity, slowest 10% over **370**, max 2,568 | ✅ |
| Bugs filed by hand, over time | spread out | **2,756 of 3,480 on one day** (2019-08-30), mass-filed | 🟡 |
| Exceptions with a stated reason | some | **36** `py2keep` bugs | 🟡 small |

**How to read it:**
- **✅ Cross-team structure is real.** Real repos, real owners, real dependencies, real dates, at scale.
- **🔴 The block links aren't evidence of waiting.** They're the dependency graph written into the tracker.

## Why the direction matters

Each bug template says: don't drop a Python 2 module while it "still has reverse dependencies". So the links read "remove the library only after everyone who uses it has moved":

| Blocked bug | Blocked by | Dependency (Debian `Depends`) |
|---|---|---|
| `six` 938492 | `pg8000` 937276 | python3-pg8000 depends on python3-six |
| `html5lib` 936709 | `python-bleach` 937614 | python3-bleach depends on python3-html5lib |

That's Meridian's **API owner burndown** ("can't delete `verify_token` until all consumers move"), not feature #5. Meridian's `blocked_by` ([04 §4](../../../docs/04-architecture.md)) is the reverse: a consumer stuck because the code it goes through hasn't migrated. Debian doesn't record those.

## Caveats

- **No close date in UDD.** "Days to close" uses `last_modified`, so it's an upper bound. Same mass-close trap as Spark.
- **Team = Maintainer field** at the package's last upload before 2019-10-21. Uploaders are ignored, and the Debian Python Team counts as one team.
- **Wait check is a keyword scan,** not a hand read: "waiting for/on", "blocked by/on", "once X lands/is fixed/migrates". It skips control@ messages and quoted lines. N = 20 edges and 14 distinct blocked bugs. A real read could find waits the regex missed.
- **N is small** for the who-linked and wait checks (20 each, seed 42). The batch pattern is clear, but a bigger sample would firm up the 0 of 13.

## The 20 sampled links (seed 42)

| Blocked bug (package) | Blocked by (package) | Added by | Added | Blockers in same command | Wait language |
|---|---|---|---|---|---|
| 937482 pymongo | 937065 mongodb | morph | 2019-10-21 | 3 | — |
| 936709 html5lib | 937614 python-bleach | morph | 2019-10-21 | 15 | — |
| 937695 python-defaults | 942969 cmake-extras | morph | 2019-10-23 | 20 | meta bug |
| 937695 python-defaults | 938251 python-vobject | morph | 2019-10-21 | 20 | meta bug |
| 937695 python-defaults | 937585 python-asteval | morph | 2019-10-21 | 20 | meta bug |
| 937569 python2.7 | 936936 libvmdk | morph | 2019-10-21 | 20 | — |
| 937448 pygobject | 943093 gexiv2 | morph | 2019-10-23 | 14 | — |
| 937405 pychess | 945632 debian-games | morph | 2019-11-27 | 1 | — |
| 938275 python-xlib | 936572 fusil | morph | 2019-10-21 | 10 | — |
| 936772 jupyter-client | 937117 nbconvert | morph | 2019-10-21 | 4 | — |
| 936761 jcc | 937473 pylucene | morph | 2019-10-21 | 1 | — |
| 937446 pygments | 937063 moin | morph | 2019-10-21 | 20 | — |
| 937695 python-defaults | 937468 pylibmc | morph | 2019-10-21 | 20 | meta bug |
| 937695 python-defaults | 937849 python-jedi | morph | 2019-10-21 | 20 | meta bug |
| 942941 dbus-python | 938884 zeitgeist | morph | 2019-10-23 | 20 | — |
| 936742 ipykernel-py2 | 937117 nbconvert | morph | 2019-10-21 | 5 | — |
| 937695 python-defaults | 936977 macsyfinder | morph | 2019-10-21 | 20 | meta bug |
| 938249 python-virtualenv | 942910 automake-1.15 | morph | 2019-10-23 | 6 | — |
| 937695 python-defaults | 937505 pypdf2 | morph | 2019-10-21 | 20 | meta bug |
| 938492 six | 937276 pg8000 | morph | 2019-10-21 | 20 | — |

`morph` = `morph@debian.org`. "Meta bug" = python-defaults 937695, the bug for removing Python 2 itself. Its thread has "waiting for" and "once it migrates", but about the whole campaign, not one package.

## What to do with it

- **For #5 blocks:** don't use the links as labels. If we use Debian at all, flip to the consumer's view: pick an app whose Python 3 port landed after its library's port, then hand-check the app's bug for a real wait. That's the 3-check process in NEXT-STEPS step 2.
- **For #7 stalls:** the slow bugs and the 36 `py2keep` bugs are worth a hand-coding pass. Reasons must come from comments, not from inactivity.
- **Compare against OpenStack and Mozilla** (next) before picking a source.

## Reproduce

Python 3.9+, with `pip install psycopg2-binary requests`. Run from this folder, in order:

| Script | Prints | Writes (git-ignored) |
|---|---|---|
| [debian_step1.py](debian_step1.py) | Bug counts, links, cycles, days to close, py2keep | `debian_edges.tsv` |
| [debian_who_linked.py](debian_who_linked.py) | Who added each sampled link, when, batch size | `debian_who_linked_sample20.csv` |
| [debian_cross_team.py](debian_cross_team.py) | Cross-team share, team counts, wait-keyword check | `debian_sample20_wait_check.csv` |

The UDD mirror is public (`udd-mirror` / `udd-mirror` at `udd-mirror.debian.net`). The campaign is finished, so the numbers should stay stable apart from bugs being archived.
