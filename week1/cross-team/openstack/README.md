# Cross-team · OpenStack Oslo incubator removal

Step 1 of [NEXT-STEPS](../NEXT-STEPS.md) for the ChatGPT report's #1 pick. Queried 2026-10-03 from OpenStack's public Gerrit (review.opendev.org) and PyPI, both read-only. Every number below is printed by [openstack_step1.py](openstack_step1.py).

## Verdict

- **Is it cross-team? The repos are. The waiting isn't.** 36 repos owned by different teams all moved off one team's shared code. But no change waited on another team.
- **0 of 59** code changes have a `Depends-On`. Every Oslo library they switched to had been released **1.5–3.5 years earlier**. Only **1** review comment mentions waiting, and that was for an old release's end of life, not for another team.
- **No stalls either.** Median **4 days** from opened to merged, max 39.
- Most of the work was done by a few volunteers sweeping across repos (top 3 people wrote 61% of the code changes), not by each team on its own code.
- **Not usable for #5 (blocks) or #7 (stalls).** Debian is the better of the two reports' top picks: see [../debian/](../debian/).

## What it is

OpenStack is a cloud platform split into hundreds of repos, each owned by a project team (Nova, Neutron, Heat, the `python-*client` repos, …). The Oslo team owns shared code. For years other teams **copied** Oslo modules into their own repos under `openstack/common` instead of importing them. Oslo then released those modules as real libraries (`oslo.config`, `oslo.log`, …).

In the Ocata cycle (2016) the Technical Committee made it a community goal: [Remove Copies of Incubated Oslo Code](https://governance.openstack.org/tc/goals/completed/ocata/remove-incubated-oslo-code.html). Delete your copy and depend on the real library. Every change was tagged with the Gerrit topic `goal-remove-incubated-oslo-code`.

In Meridian terms: repo = repo, project team = team, Gerrit change ≈ ticket + PR, Oslo = the API owner.

## What we did

1. **Pulled every change in the topic** from Gerrit's REST API, including the commit message, changed files, review comments and author.
2. **Split code from paperwork.** Changes in `openstack/governance` are teams posting status updates ("Acknowledge remove-incubated-oslo-code for Glance"), not code.
3. **Time open:** created → merged, for merged code changes.
4. **Looked for evidence of waiting**, three ways:
   - `Depends-On:` in the commit message. This is Gerrit's way of saying "can't merge until that other change merges". It's the strongest evidence of a wait there is.
   - `requirements.txt` edits that add an Oslo library. We then checked on PyPI when that library was first released. If it was out long before, nobody was waiting for it.
   - Waiting language in human review comments ("wait for", "blocked by", "once X is released", "needs a release", …). Bot comments from Zuul/Jenkins are skipped.
5. **Counted authors** to see whether each team migrated its own repo.

## The numbers

| What it tells you | Need | We got | |
|---|---|---|---|
| Changes in the topic | 30+ | **115** (report said 37+) | ✅ |
| …that are code, not paperwork | 30+ | **59** (51 merged, 8 abandoned). The other 56 are governance status updates | ✅ |
| Repos with code changes | many | **36** (report said ~23) | ✅ |
| Real code edges (added an Oslo lib to requirements) | some | **10** changes: oslo.config, oslo.i18n, oslo.log, oslo.utils | ✅ |
| Changes that formally waited (`Depends-On`) | a real share | **0 of 59** | 🔴 |
| Library ready only shortly before consumers moved | yes | **No.** Released 2013–2015, used Oct–Nov 2016 (table below) | 🔴 |
| Review comments showing a wait on another team | a real share | **0.** The one hit is "Wait for liberty EOL" on [neutron 393540](https://review.opendev.org/c/393540), a release-calendar hold | 🔴 |
| Slow work (stall material) | a real share | median **4 days**, slowest 10% over **18**, max **39**. **16** merged the same day | 🔴 |
| Each team migrated its own code | mostly | **No.** 17 authors. The top 3 wrote 36 of 59, across 24 repos | 🟡 |
| Per-team planning tickets | most | **Sparse.** The goal page lists Launchpad/StoryBoard links for only a few teams (per the ChatGPT report; not re-counted) | 🟡 |

**How to read it:**
- **✅ The cross-team structure is real:** one owner team, 36 consuming repos, real imports, real dates.
- **🔴 There's nothing to find.** Upstream was done years before, so no one was blocked and nothing stalled. Each repo was a few days of cleanup.

## Order check: libraries were ready long before

| Library | First PyPI release | First consumer change in this goal |
|---|---|---|
| oslo.config | 2013-03-12 | 2016-10-28 |
| oslo.i18n | 2014-07-02 | 2016-10-26 |
| oslo.log | 2015-01-15 | 2016-10-26 |
| oslo.utils | 2014-07-29 | 2016-11-11 |

The ChatGPT report's case rested on the ordering "library released → added to global requirements → consumers migrate". That ordering is real, but it played out in 2014–15. By the time of this goal it was history.

## Who did the work

| Author | Code changes | Repos |
|---|---|---|
| Steve Martinelli | 19 | 12 (mostly `python-*client`) |
| Henry Gessau | 10 | 9 (neutron and its sub-projects, all on 2016-11-04) |
| Doug Hellmann | 7 | 3 (storyboard, release-tools) |
| 14 others | 23 | |

So a handful of people swept through other teams' repos. For Meridian that's the opposite of the problem we solve: there was no handoff between teams to get stuck on.

## What's in the dataset: code changes per repo

| Repo | Changes | Merged | Abandoned | First change | Slowest (days) |
|---|---|---|---|---|---|
| opendev/python-storyboardclient | 1 | 1 | 0 | 2016-11-08 | 24 |
| opendev/storyboard | 5 | 5 | 0 | 2016-11-07 | 4 |
| openstack/blazar | 1 | 1 | 0 | 2016-12-09 | 6 |
| openstack/castellan | 1 | 1 | 0 | 2016-10-26 | 8 |
| openstack/designate | 2 | 2 | 0 | 2016-10-27 | 14 |
| openstack/dragonflow | 1 | 1 | 0 | 2016-11-25 | 2 |
| openstack/heat | 1 | 0 | 1 | 2016-09-28 | — |
| openstack/heat-cfnclient | 2 | 2 | 0 | 2016-10-26 | 12 |
| openstack/networking-bagpipe | 1 | 1 | 0 | 2016-11-04 | 3 |
| openstack/networking-bgpvpn | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/networking-hyperv | 1 | 1 | 0 | 2016-11-17 | 0 |
| openstack/networking-odl | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/networking-sfc | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/neutron | 2 | 2 | 0 | 2016-11-04 | 20 |
| openstack/neutron-dynamic-routing | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/neutron-fwaas | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/neutron-lbaas | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/octavia | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/os-win | 1 | 1 | 0 | 2016-11-17 | 0 |
| openstack/python-blazarclient | 4 | 4 | 0 | 2016-10-28 | 18 |
| openstack/python-ceilometerclient | 1 | 1 | 0 | 2016-10-31 | 7 |
| openstack/python-cinderclient | 1 | 1 | 0 | 2016-11-04 | 0 |
| openstack/python-cloudkittyclient | 1 | 1 | 0 | 2016-10-31 | 21 |
| openstack/python-congressclient | 1 | 1 | 0 | 2016-11-11 | 2 |
| openstack/python-glanceclient | 3 | 2 | 1 | 2016-09-27 | 39 |
| openstack/python-heatclient | 4 | 3 | 1 | 2016-11-04 | 18 |
| openstack/python-karborclient | 1 | 1 | 0 | 2016-09-28 | 8 |
| openstack/python-mistralclient | 4 | 2 | 2 | 2016-11-08 | 0 |
| openstack/python-monascaclient | 3 | 3 | 0 | 2016-11-08 | 6 |
| openstack/python-muranoclient | 1 | 1 | 0 | 2016-11-08 | 0 |
| openstack/python-searchlightclient | 2 | 1 | 1 | 2016-11-09 | 4 |
| openstack/python-troveclient | 2 | 1 | 1 | 2016-03-23 | 4 |
| openstack/release-tools | 1 | 1 | 0 | 2016-11-01 | 0 |
| openstack/releases | 1 | 1 | 0 | 2016-10-31 | 1 |
| openstack/solum-infra-guestagent | 2 | 1 | 1 | 2016-10-28 | 8 |
| openstack/tripleo-common | 1 | 1 | 0 | 2016-10-26 | 27 |

### Slowest merged changes

| Days | Repo | Change |
|---|---|---|
| 39 | python-glanceclient | [Remove unused _i18n.py shim](https://review.opendev.org/c/380452) |
| 27 | tripleo-common | [Remove last vestiges of oslo-incubator](https://review.opendev.org/c/390808) |
| 24 | python-storyboardclient | [remove or adopt incubated oslo code](https://review.opendev.org/c/395116) |
| 21 | python-cloudkittyclient | [move old oslo-incubator code out of openstack/common](https://review.opendev.org/c/391885) |
| 20 | neutron | [Remove legacy oslo.messaging.notify.drivers](https://review.opendev.org/c/393973) |

### Abandoned changes

Eight abandoned changes. In the repos that also have a merged change in the topic (glanceclient, heatclient, mistralclient, searchlightclient, troveclient, solum-infra-guestagent), the abandoned one was most likely replaced by that later change. heat's has no merged change in the topic, so if its copy was removed, it was under another topic. We haven't checked why each was abandoned.

| Repo | Change |
|---|---|
| heat | [378197](https://review.opendev.org/c/378197) Remove copy of incubated Oslo code |
| python-glanceclient | [378053](https://review.opendev.org/c/378053) Remove copies of incubated Oslo code |
| python-heatclient | [396704](https://review.opendev.org/c/396704) no longer use oslo-incubator code |
| python-mistralclient | [395064](https://review.opendev.org/c/395064), [395066](https://review.opendev.org/c/395066) remove strutils.py / uuidutils.py |
| python-searchlightclient | [395739](https://review.opendev.org/c/395739) Move old oslo-incubator code out of openstack/common |
| python-troveclient | [296667](https://review.opendev.org/c/296667) Replace obsolete oslo-incubator apiclient |
| solum-infra-guestagent | [391387](https://review.opendev.org/c/391387) Remove o/c/local.py and o/c/log.py |

## Caveats

- **Topic coverage.** We only see changes someone tagged with the topic. Untagged migration work, like heat's final removal, is missed. That wouldn't change the verdict: untagged work can't add `Depends-On` evidence to tagged changes.
- **Waiting is a keyword scan,** not a hand read of every review. With 0 `Depends-On`, a median of 4 days and libraries released years earlier, we didn't think a full read was worth it.
- **Code edges:** only 10 changes added a library to requirements. The rest only deleted dead copies, or the repo already depended on the library, so there was nothing to add.
- **Authors:** grouped by Gerrit display name, not merged across accounts.

## Reproduce

Python 3.9+, with `pip install requests`. Run from this folder:

```
python openstack_step1.py
```

It prints every number above and writes `openstack_changes.csv` (git-ignored): one row per change with repo, author, status, dates, days open, patchsets, Depends-On, Oslo libs added, wait hits and a Gerrit link. Gerrit sometimes drops the connection mid-run. If it does, just rerun.
