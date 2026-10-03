# Public Test Data for Meridian: Cross-Team Blocking and Stalled Migrations

The best public match is Debian's Python 2 removal campaign (the "py2removal" bugs). It covered thousands of separately maintained packages that import each other's code. It has a dated start and a per-package bug for each one, and the bug tracker records real blocked-by links between packages. It is the only candidate I found that clearly gives you at least 30 hand-checkable cross-team blocks *and* at least 30 stalled tickets whose reason you can find out. No single public dataset meets all five requirements cleanly, though. Every candidate has at least one weak spot: tickets filed by bots, blocking links added in bulk, or dependencies declared in packaging metadata rather than found in the code.

## TL;DR
- **Use Debian py2removal (Ultimate Debian Database plus the Debian bug tracker) as your main fixture source.** It has about 3,477 per-package bugs, mass-filed by Matthias Klose on 30 Aug 2019 (e.g., #936979 mailman, dated 07:25:25 UTC). It also has explicit "blocked by" links between packages' bugs, a "py2keep" tag that explains deliberate stalls, and a public PostgreSQL mirror. Fedora's portingdb (which has a "Blocked" status) and conda-forge's NumPy 2 migration (which has an "awaiting parents" status) are the next-best sources for blocks.
- **For "why it stalled", NumPy 2.0 downstream GitHub issues and Mozilla Bugzilla meta bugs give the richest free-text reasons.** One example is a librosa issue saying it could not test because sklearn, numba and soundfile were not ready. Both need hand labelling, and neither gives clean status-change timestamps for every team.
- **Nothing meets all five requirements as-is.** Build a "real graph, real labels" fixture. Take the dependency graph and the dated blocked-by links from Debian (cross-checked against Fedora and conda-forge). Hand-label 30–60 stall reasons from the bug comments. Then project the result into Meridian's Jira-plus-repos shape. Do not invent the blocks themselves.

## Ranked candidates

| Rank | Candidate | R1 code-level cross-repo deps | R2 dated start/end | R3 per-team tickets with dates | R4 real blocking evidence | R5 public, has history | ≥30 blocks? | ≥30 stalls with known reason? |
|---|---|---|---|---|---|---|---|---|
| 1 | Debian py2removal (BTS + UDD) | Yes (imports; Depends/Build-Depends) | Yes (filed 30 Aug 2019; ended with the bullseye-era removal) | Yes, one bug per source package | Yes: BTS blocks/blocked-by, many added by hand | Yes (public UDD mirror, rsync of bug spool, salsa git) | **Yes** | **Yes** |
| 2 | Fedora portingdb + Red Hat Bugzilla | Yes (RPM deps; Python imports) | Yes (2015 to the Python 2 retirement in F32) | Partial (Bugzilla bugs; portingdb YAML) | Yes: "Blocked" status; Bugzilla Depends/Blocks | Yes (git repo; Bugzilla API) | Yes | Partial |
| 3 | conda-forge NumPy 2 migration | Partial (recipe host deps, not call sites) | Yes (started 15 May 2024; on 28 May 2025 conda-forge announced it would conclude within a week) | Yes, bot PR per feedstock repo | Yes: "awaiting parents" graph state | Yes (GitHub feedstocks, bot repo) | Yes | Partial (many stalls are bot bookkeeping) |
| 4 | NumPy 2.0 ecosystem (numpy#26191 + downstream issues) | **Yes** (real imports across independent repos) | Yes (NumPy 2.0.0 released 16 Jun 2024, per numpy.org) | Yes, one GitHub issue per project | Yes, in prose ("unable to evaluate … due to intermediate dependency incompatibilities") | Yes | Likely (by hand) | Yes (by hand) |
| 5 | Mozilla Bugzilla meta bugs (e.g., Places sync API removal) | Partial (mostly one repo; comm-central consumers are separate) | Yes | Yes | Yes: Depends on/Blocks are used heavily | Yes (REST API) | Maybe | Yes |
| 6 | Apache cross-project Jira (HBase↔Hadoop) via Public Jira Dataset | Yes (Maven deps on Hadoop artifacts) | Partial | Yes | Yes, cross-project "depends upon"/"blocks" links | Partial (Zenodo record reports data removal for anonymisation) | Maybe | Partial |
| 7 | OpenStack Ussuri drop-py27 goal | Yes (oslo libraries imported by services) | Yes (phased schedule) | Weak (Gerrit topic, no StoryBoard story) | Weak/indirect | Yes (opendev git, Gerrit) | Unlikely | Unlikely |
| 8 | Kubernetes 1.16 extensions/v1beta1 removal in Helm charts | Partial (YAML manifests; subchart dependencies) | Yes (announced 18 Jul 2019; removed in 1.16) | Yes, GitHub issues per chart | Some (subchart chains) | Yes | Unlikely | Partial (stale-bot noise) |

Academic migration datasets (BUMP, PyMigBench) do not make the list. Both record single-repo commits with no tickets and no chains between repos (details below).

## 1. Debian py2removal — the best fit

**What it is.** Debian set out to remove Python 2. Matthias Klose mass-filed one bug per affected source package, all tagged `Usertags: py2removal` under user `debian-python@lists.debian.org`. For example, bug #938578 (subversion) went in with that header.\[1\] Each bug told maintainers: "If the conversion or removal needs action on another package first, please document the blocking by using the BTS affects command."\[2\]\[3\] That instruction asks maintainers to record blocks by hand. It is the closest public analogue to Meridian's Team A waiting on Team B.

**Scale.** Sandro Tosi's dashboard, generated 2021-01-12, reports "Total bugs found: 3477 (open: 169, closed: 3308)". It has a per-bug "# blocked bugs" column, with values such as bareos = 2, crossfire = 3 and gnat-gps = 3.\[4\] The November 2019 debian-devel-announce post "Python 2 removal in sid/bullseye: Progress and next steps" said: "With about 3300 py2removal bugs filed and 1500 closed, we are now almost done with half of the removals." So roughly 45% were closed after about 2.5 months, and about 4.9% were still open about 16.5 months after filing. That is a long tail of stalled tickets. The "teams" are Debian maintainer groups named on each bug, such as Debian Games Team, Debian Science Team, and Debian/Kubuntu Qt/KDE Maintainers.\[4\]

**Real blocks with links (from the tracker):**
- #938847 xlsxwriter: "Fix blocked by plaso: Python2 removal…, pandas: Python2 removal…", and it in turn blocks python-defaults.\[5\]
- #936463 ecasound: blocked by debian-multimedia; blocks python-docutils and texlive-extra. Filed 30 Aug 2019, fixed in 2.9.3-1 on 18 Jan 2020, about 4.6 months later.\[6\]\[7\]
- #936757 jackd2: blocking bugs 936813 and 938081 were added on 21 Oct 2019, then 939106 and 945633.\[8\]
- #938392 ros-ros is blocked by seven other ROS bugs, including 938374 and 938390. That is a chain inside one team.\[9\]
- #937498 pyopengl is blocked by psychopy, pyepl and python-expyriment, and blocks python-numpy's own removal.\[10\] This is the "shared library can't drop until its consumers move" direction.
- Hub bugs such as #938492 (six) and #937665 (python-coverage) were blocked by 30+ bugs each.\[11\]\[12\]

**Stalls with knowable reasons.** The tracker records several reasons directly:
- **Deliberate keep:** the `py2keep` usertag. The November 2019 debian-devel-announce post says: "If you are absolutely sure you need to keep your Python 2 only application in Debian, you should mark the Python 2 removal bug with the 'py2keep' user tag… and provide a rationale". It adds that py2keep bugs "will not have their severity raised". Subversion's bug was tagged py2keep in November 2019.\[1\]
- **Blocked upstream:** the blocked-by links above.
- **Upstream not ported:** #942988 displaycal. The maintainer quoted upstream planning Python 3 "around end of the year-ish", and a later comment says "upstream doesn't seem to be making much progress".\[13\] #937184 offlineimap: "no plans to convert OfflineIMAP to Python3."\[14\]
- **Fix ready but blocked:** #943149 pam-mysql. Ubuntu supplied a patch "I understand that this is blocked in Debian by the marked bug… helpful when this bug is unblocked".\[15\]
- **Abandoned:** "RM:" removal requests for dead packages.\[12\]

These map well onto your categories: upstream block, wrong owner (bugs reassigned to ftp.debian.org), risky, and busy elsewhere.

**How to get it.** The public UDD mirror at `postgresql://udd-mirror:udd-mirror@udd-mirror.debian.net/udd` refreshes hourly.\[16\]\[17\] Its schema has `bugs`, `bugs_blocks`, `bugs_blockedby`, `bugs_usertags` and the `archived_*` counterparts.\[18\] Raw bug logs are available by rsync from bugs-mirror.debian.org; as of July 2023 the active spool was about 21 GB and the archive about 124 GB.\[19\] Package source and git history are on salsa.debian.org and in the archive. Join `bugs_usertags` (tag = 'py2removal') to `bugs_blockedby`, then get created and closed dates from `bugs`.

**Traps:**
1. Most blocked-by links were not filed by the blocked team. Sandro Tosi added them in batches, mostly around 21–23 Oct 2019, and some were still being added to #942959 in November 2022.\[20\]\[21\] They are probably derived from the dependency graph. That makes them good ground truth for "a dependency exists", but you must check the comments to confirm the block actually held up work.
2. Bot-style mass filing. Each bug's opening text is boilerplate, so dates matter more than descriptions.
3. Bugs were bulk-closed with "-done" by campaign drivers, and bugs are archived 28 days after closing (query `archived_bugs`).\[19\]
4. Dependencies are declared in packaging (Depends/Build-Depends). Matching them to `import` call sites in upstream source takes an extra step.
5. bugs.debian.org blocks automated crawlers. Use UDD or rsync, not scraping.
6. There are no explicit "work started" timestamps. Use the first maintainer reply or the first "pending"/upload message as a proxy.

**Verdict:** Yes on both bars. 30+ blocks can be taken directly from `bugs_blockedby` and checked by reading the logs. 30+ stalls with a stated reason (py2keep, upstream not ported, blocked) come out of a few hours of reading.

## 2. Fedora portingdb + Red Hat Bugzilla

**What it is.** The fedora-python/portingdb dashboard tracked every Python package in Fedora. Its legend includes "**B Blocked**: Depends on Python 3 support in another package" and "**X Legacy**: Package will not be ported; dependents must use an alternative".\[22\] Its data comes from a `fedora.json` built by a DNF plugin that records each package's dependencies, plus YAML overrides with `status`, `priority` and `links`.\[23\] The git commit log records state transitions such as "idle → released" and "missing → py3-only (71)".\[24\] That gives you dated status changes for each package. At launch, "of the about 3000 Python packages in Fedora, just 900 do support Python 3".\[23\] The final dashboard shows 5,076 packages with 5,072 Python 3 only.\[22\] Group pages show cross-team pockets, for example the GIMP group: 8 packages, 2 Blocked, 4 Legacy.\[25\]

**Bugzilla side.** Fedora trackers carry real Depends/Blocks fields. The Python 3.12 tracker (#2135404, alias PYTHON3.12, opened 2022-10-17) "Depends On" several bugs.\[26\] One per-package example: OpenIPMI failing because distutils was removed (#1948437) "Blocks 1890881, 1927309, 1927313".\[27\] A late status note says "there are still 150 packages in Fedora 39 that need to be rebuilt with Python 3.12."\[26\]

**Traps:** FTBFS bugs filed by bots, automated "CLOSED EOL" closures, and some duplicates (#2339514 closed as a duplicate of #2339905).\[28\] Red Hat Bugzilla pages need JavaScript for the UI, but `ctype=xml` and the REST API work.

**Verdict:** Yes for blocks, using git-dated portingdb "Blocked" states cross-checked against Bugzilla Depends. Stalls are only partial, because portingdb records state, not reasons. Use it to cross-validate Debian.

## 3. conda-forge NumPy 2 migration

**What it is.** On 15 May 2024 conda-forge began "a migration of all conda-forge packages that depend on numpy during the build".\[29\] The bot opens a PR in each feedstock repository and builds a migration graph. A PR is issued "only if… the node has no dependencies that depend on the new pinnings and have not been migrated".\[30\] That is a machine-computed, timestamped upstream block. The status dashboard shows columns for Done, In PR, Awaiting PR, **Awaiting parents**, Not solvable and Bot error.\[31\] On 28 May 2025 the project announced the migration would close within a week, "for more than a year… while not every affected feedstock has been done".\[32\] That gives you a start, an end, and a residue of stalled feedstocks.

**Why it's useful for "why stalled":** it is full of *false* blocks, which is exactly what Meridian needs to tell apart from real ones. Bot PR #6680 found that 30 of 43 feedstocks "awaiting parents" on the riscv64 migration were stalled only because hand-migrated parents had no `PRed` record.\[33\] Issue #6787 reports that auto-closing PRs makes migrations "considered done" when they are not.\[34\] The docs warn that closing a migration PR "makes the bot think that another PR… is merged".\[30\]

**Traps:** almost everything is bot-generated. Dependencies are recipe-level (`requirements/host`), not call sites. Dashboard history isn't archived, so rebuild it from PR timestamps in each feedstock repo.

**Verdict:** Yes for 30+ blocks (graph plus PR dates). Partial for stalls: the reasons tend to be bot bookkeeping rather than human ones.

## 4. NumPy 2.0 ecosystem tracking (numpy/numpy#26191 + downstream issues)

**What it is.** numpy#26191, opened 1 Apr 2024, "tracks the compatibility status of packages that depend on or support NumPy". It has one row per downstream project with the minimum compatible version and a link to that project's own tracking issue (astropy#16200, dask#11066, h5py#2353, geopandas#3258, and so on).\[35\] The Scientific Python blog post "NumPy 2.0: an evolutionary milestone" describes it as part of "an extraordinary amount of effort… tracking compatibility of popular open source projects". Here the dependency really is in the code (`import numpy`, C-API use), and projects depend on each other in chains (NumPy → numba/scikit-learn → librosa).

**Real block example.** librosa#1831: "we're unable to evaluate this due to intermediate dependency incompatibilities (sklearn, numba, soundfile, etc)". It links numba PR #9466 and scikit-learn #27075, and its checklist shows each upstream ticked off as it shipped (numba 0.60, scikit-learn 1.4.2).\[36\] This is the closest public example of a hidden-in-code chain: librosa never calls NumPy 2-specific code, but it is blocked through numba.

**Traps:** you have to collect the issues one by one. There are no structured blocked-by fields, open dates vary by project, and some projects (TensorFlow #67291) opened issues only after being prompted.\[37\]

**Verdict:** About 30 hand-checked chains is feasible with a day of curation. This is the best source of natural-language stall reasons.

## 5. Mozilla Bugzilla meta bugs

Mozilla uses Depends on / Blocks heavily. The Places synchronous-API removal (bug 834457) depends on about 14 bugs. When it was resolved, the assignee wrote that "remaining dependencies just track work left to do in external consumers (SM and CZ so far), but those don't block the bug from being fixed".\[38\] SeaMonkey and ChatZilla are separate teams and repositories (comm-central). Another example is FileUtils.getFile() removal (bug 920187), which took years with dependencies added and removed.\[39\] A newer example is moz-phab migrating off `pkg_resources` before setuptools removed it (bug 1970907).\[40\] The REST API is public. **Traps:** "No longer depends on" churn, [meta] bugs closed as WONTFIX (e.g., 1578286),\[41\] and most consumers live in one monorepo. **Verdict:** Good for stalled tickets with reasons; fewer than 30 cross-repo blocks without heavy curation.

## 6. Apache cross-project Jira (HBase ↔ Hadoop)

You ruled out Jira links in general, but there is one new piece of evidence: *cross-project* links exist and are meaningful here. HBASE-22953 "Supporting Hadoop 3.3.0" **depends upon HADOOP-17008 "Release Hadoop 3.3.0"**.\[42\] HBASE-6581 **blocks OOZIE-2973** "Make sure Oozie works with Hadoop 3", which is still Open.\[43\] HBASE-28846 "is blocked by HBASE-28929".\[44\] The HBase `hadoop3` component lists dozens of breakage tickets, e.g., HBASE-23834 "HBase fails to run on Hadoop 3.3.0/3.2.2/3.1.4 due to jetty version mismatch".\[42\]\[45\] In bulk, the Public Jira Dataset (Montgomery, Lüders, Maalej, MSR 2022) covers 16 Jiras, 1,822 projects, 2.7M issues, 32M changes, 9M comments and 1M issue links, with full change history.\[46\] That gives you status-change timestamps. **Traps:** the current Zenodo record says "Data has been removed while anonymising the data", so check what is still downloadable, or pull from the live ASF Jira API.\[47\] Links stay sparse; this matches your earlier finding. **Verdict:** Use it as a supplement for status-change history, not as the main source of blocks.

## 7. OpenStack Ussuri "drop Python 2.7"

The schedule was phased by dependency direction. "Services Drop python 2 Completed" was due by Ussuri-1 (9–13 Dec 2019), and "Common libraries & QA Drop python 2 Completed" by Ussuri-2 (10–14 Feb 2020).\[48\] Libraries were deliberately ordered *after* services. The Cinder PTG notes describe waiting until "any other project that needs to install" Cinder is ready.\[49\] Oslo libraries such as oslo.messaging and oslo.service record the change as "[ussuri][goal] Drop python 2.7 support and testing".\[50\]\[51\] **Trap:** the goal page says "Storyboard stories TODO"; tracking was by the Gerrit topic `drop-py27-support`\[52\] and an etherpad. So there are no per-team tickets. **Verdict:** Clean code-level dependencies and dated phases, but too few tickets. Useful only for checking code-graph detection.

## 8. Kubernetes 1.16 API removals in Helm charts

The removal was announced 18 Jul 2019 and took effect in v1.16.\[53\] Per-chart GitHub issues include helm/charts#17926 (sonarqube, labelled `lifecycle/stale`) and binderhub#1011.\[54\]\[55\] anchore-engine#351 is a real chain: the failure came from the bundled `charts/postgresql/templates/deployment.yaml` subchart, not from Anchore's own templates.\[56\] **Traps:** the dependencies are YAML, the stale bot auto-closes issues, and helm/charts has since been archived. **Verdict:** Not enough for either bar.

## Ruled out after checking
- **BUMP (SANER 2024):** 571 breaking dependency updates from 153 Java projects. Each is a one-line pom change, typically from Dependabot or Renovate,\[57\]\[58\] with no tickets and no chains between repos.
- **PyMigBench / PyMigBench-2.0:** 335 verified migrations across 311 repos, recorded at the commit level, with no issue tracking and no dependencies between client repos.\[59\]
- **Chromium:** tracking bugs have Monorail "blocked-on" fields that survived the migration to issues.chromium.org, e.g., the MSE-in-workers bug.\[60\] But it is a monorepo, and migration CLs mostly wrap ready work, the same weakness you found with Spark.

## Recommendations
1. **Build the block fixture from Debian.** Run a UDD query: py2removal usertag joined to `bugs_blockedby`, with arrival and done dates. Sample 60 pairs at random. For each, confirm three things: (a) the upstream package is imported by the downstream source (check salsa git), (b) the downstream fix landed after the upstream fix, and (c) a log comment shows the wait. Keep the pairs that pass all three; expect well over 30.
2. **Build the stall fixture from three sources.** Use Debian bugs open for more than 90 days, coded as py2keep / upstream-not-ported / blocked / removal. Add NumPy 2 downstream issues for free-text reasons, and conda-forge "awaiting parents" cases as *false-block* negatives.
3. **Project into Meridian's shape honestly.** Map source package → repo and maintainer team → owning team. Map the bug → a Jira ticket, keeping the real created and resolved dates. Map blocked-by → a hidden block, then **remove the link from the ticket view** so Meridian has to rediscover it from imports. Write down every transformation you apply. Do not synthesise blocks or reasons. Synthetic code is acceptable only as packaging around real, cited dependency edges.
4. **Track the deprecated-function angle separately.** Debian, Fedora and conda-forge are platform migrations, not function deprecations. For call-site realism, add a NumPy 2 API-removal subset (NEP 52 removals tracked in numpy#23999). There, downstream code calls removed functions such as `np.issubsctype`.\[61\]

## Caveats
- I could not get campaign-wide counts of py2removal bugs with blocked-by links, or median and longest time-to-close. The Debian bug tracker blocks crawlers and the list archive is behind a challenge page. A UDD query will give the exact figures. The 3,477 total and the 169-open snapshot come from an unofficial dashboard.
- Several Debian bug details were read from search-engine snippets of bug pages, not full fetches.
- "Teams" in distribution packaging are volunteer maintainers. Their reasons for stalling (time, interest) are noisier than in a company and lean towards "busy elsewhere".

## Sources

1. [#938578 - subversion: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=938578)
2. [#936834 - ledger: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=936834)
3. [#938027 - python-pip: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/938027)
4. <http://sandrotosi.me/debian/py2removal/index.html>
5. [#938847 - xlsxwriter: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs-devel.debian.org/cgi-bin/bugreport.cgi?bug=938847)
6. [#936463 - ecasound: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs-devel.debian.org/cgi-bin/bugreport.cgi?bug=936463%3Bmsg%3D49)
7. [#936463 - ecasound: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=936463)
8. [#936757 - jackd2: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=936757)
9. [#938392 - ros-ros: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs-devel.debian.org/cgi-bin/bugreport.cgi?bug=938392)
10. [#937498 - pyopengl: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs-devel.debian.org/cgi-bin/bugreport.cgi?bug=937498%3Bmsg%3D19)
11. [#937665 - python-coverage: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=937665)
12. [#938492 - six: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=938492)
13. [#942988 - displaycal: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=942988)
14. [#937184 - offlineimap: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=937184)
15. [#943149 - pam-mysql: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=943149)
16. [Services/PublicUddMirror - Debian Wiki](https://wiki.debian.org/Services/PublicUddMirror)
17. [Ultimate Debian Database](https://news.ycombinator.com/item?id=25584695)
18. [Index for udd - Ultimate Debian Database](https://udd.debian.org/schema/udd.html)
19. [Debian -- Debian BTS — access methods](https://www.debian.org/Bugs/Access)
20. [#937480 - pymia: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=937480)
21. [#942959 - dh-python: Python2 removal in sid/bullseye - Debian Bug report logs](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=942959)
22. [99.9% → 99.9%](https://fedora.portingdb.xyz/)
23. [Python 3 Porting Database · Matej Stuchlik](http://synfo.github.io/2015/10/14/Python3-Porting-Database/)
24. [Commits · fedora-python/portingdb](https://github.com/fedora-python/portingdb/commits)
25. [GIMP - Python 2 Dropping Database for Fedora](https://fedora.portingdb.xyz/grp/gimp/)
26. [2135404](https://bugzilla.redhat.com/show_bug.cgi?id=2135404)
27. [Full Text Bug Listing](https://bugzilla.redhat.com/show_bug.cgi?format=multiple&id=1948437)
28. [2339514](https://bugzilla.redhat.com/show_bug.cgi?id=2339514)
29. [NumPy 2 Migration](https://conda-forge.org/news/2024/05/15/numpy-2-migration/)
30. [Knowledge Base](https://conda-forge.org/docs/maintainer/knowledge_base/)
31. [Status dashboard](https://conda-forge.org/status/)
32. [Upcoming closure of NumPy 2.0 migration](https://conda-forge.org/news/2025/05/28/numpy-2-migration-closure/)
33. [Treat already-migrated predecessors as built in arch migrations by velonica0 · Pull Request #6680 · conda-forge/conda-forge-bot](https://github.com/conda-forge/conda-forge-bot/pull/6680)
34. [Auto-closing issues on conflicts leads to migrations being considered done · Issue #6787 · conda-forge/conda-forge-bot](https://github.com/conda-forge/conda-forge-bot/issues/6787)
35. [Ecosystem compatibility with numpy 2.0 · Issue #26191 · numpy/numpy](https://github.com/numpy/numpy/issues/26191)
36. [Numpy 2.0 compatibility · Issue #1831 · librosa/librosa](https://github.com/librosa/librosa/issues/1831)
37. [NumPy 2.0 support · Issue #67291 · tensorflow/tensorflow](https://github.com/tensorflow/tensorflow/issues/67291)
38. [834457 - Remove deprecated synchronous APIs from Places](https://bugzilla.mozilla.org/show_bug.cgi?id=834457)
39. [920187 - Deprecate and get rid of FileUtils.getFile()](https://bugzilla.mozilla.org/show_bug.cgi?id=920187)
40. [1970907 - Dependency pkg\_resources is deprecated and due for removal by end of 2025](https://bugzilla.mozilla.org/show_bug.cgi?id=1970907)
41. [1578286 - \[meta\] Deprecate background pages in favor of service workers](https://bugzilla.mozilla.org/show_bug.cgi?id=1578286)
42. [\[HBASE-22953\] Supporting Hadoop 3.3.0 - ASF Jira](https://issues.apache.org/jira/browse/HBASE-22953)
43. [\[HBASE-6581\] Build with hadoop.profile=3.0 - ASF Jira](https://issues.apache.org/jira/browse/HBASE-6581)
44. [\[HBASE-28846\] Change the default Hadoop3 version to 3.4.1, and add tests to make sure HBase works with earlier supported Hadoop versions - ASF JIRA](https://issues.apache.org/jira/browse/HBASE-28846)
45. [Issue Navigator - ASF JIRA](https://issues.apache.org/jira/issues/?jql=project+%3D+HBASE+AND+component+%3D+hadoop3)
46. [An Alternative Issue Tracking Dataset of Public Jira Repositories](https://arxiv.org/pdf/2201.08368)
47. [The Public Jira Dataset](https://zenodo.org/records/15393866)
48. [OpenStack Releases: Ussuri Release Schedule](https://releases.openstack.org/ussuri/schedule.html)
49. [CinderUssuriPTGSummary - OpenStack](https://wiki.openstack.org/wiki/CinderUssuriPTGSummary)
50. [CHANGES — oslo.service 4.9.0.dev15 documentation](https://docs.openstack.org/oslo.service/latest/user/history.html)
51. [CHANGES — oslo.messaging 17.2.0.dev15 documentation](https://docs.openstack.org/oslo.messaging/latest/user/history.html)
52. [Drop Python 2.7 Support — OpenStack Technical Committee Governance Documents](https://governance.openstack.org/tc/goals/completed/ussuri/drop-py27.html)
53. [Deprecated APIs Removed In 1.16: Here’s What You Need To Know](https://kubernetes.io/blog/2019/07/18/api-deprecations-in-1-16/)
54. [K8s 1.16 deprecates extensions/v1beta1 for apiVersion, breaking the Helm charts · Issue #1011 · jupyterhub/binderhub](https://github.com/jupyterhub/binderhub/issues/1011)
55. [\[stable/sonarkube\] extensions/v1beta1 does not exist in Kubernetes 1.16 · Issue #17926 · helm/charts](https://github.com/helm/charts/issues/17926)
56. [Helm Chart Installation fails with Kubernetes 1.16 · Issue #351 · anchore/anchore-engine](https://github.com/anchore/anchore-engine/issues/351)
57. [GitHub - chains-project/bump: A dataset of reproducible breaking dependency updates, SANER 2024 (https://doi.org/10.1109/SANER60148.2024.00024) · GitHub](https://github.com/chains-project/bump/)
58. [BUMP: A Benchmark of Reproducible Breaking Dependency Updates](https://ieeexplore.ieee.org/document/10589737/)
59. [Characterizing Python Library Migrations](https://arxiv.org/html/2207.01124v2)
60. [Investigate options for supporting MSE API from web worker context (offloading main thread) \[40591101\] - Chromium](https://issues.chromium.org/issues/40591101)
61. [Tracking issue: Python API cleanup for NumPy 2.0 (NEP 52) · Issue #23999 · numpy/numpy](https://github.com/numpy/numpy/issues/23999)
