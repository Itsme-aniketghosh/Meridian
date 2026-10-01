# Public datasets for cross-team blocking and stalled software migrations

## Bottom line

I did **not** find a single public, ready-made dataset that cleanly satisfies Meridian’s full bar: at least 30 independently hand-checkable **cross-team blocks inferred from code/release dependencies** *and* at least 30 long-running migration tickets whose stall reason can be established from contemporaneous evidence.

The closest public corpus is **OpenStack**, especially its project-wide migration goals around Oslo libraries. It combines many separately owned repositories, explicit library dependencies in code and package metadata, coordinated migration campaigns, git/Gerrit history, and enough linked changes to plausibly produce 30+ genuine cross-repository ordering constraints. The weakness is the ticket side: many teams recorded the work directly as Gerrit reviews rather than long-lived planning bugs, so “why stalled?” is less uniformly represented. citeturn18search6turn2search16

The best corpus for the **stalled-work explanation** feature is **Mozilla Bugzilla + mozilla-central**. Bugzilla gives unusually rich dependency graphs and histories. A particularly good internal-API migration, the `nsIURI` thread-safety/immutability project, has **43 direct dependency bugs** on its meta bug, lasted roughly six years, and includes downstream work explicitly saying it had to wait for the `nsIURI` migration to land and stabilize. Its weakness is structural: Mozilla is largely one source tree, so it tests cross-team/component blocking better than cross-repository blocking. citeturn9view0turn8search16

**Kubernetes** has perhaps the cleanest public example of a migration tracker encoding *real ordering across repositories*: the long-running removal of `extensions/v1beta1` explicitly lists work in Kubernetes core, `cluster-proportional-autoscaler`, and `kops` that had to happen before old APIs could be disabled. It is excellent as a smaller gold set, but its central tracker does not appear large enough by itself to give 30 stalled tickets with causal explanations. citeturn19search16turn19search0

**Apache Hadoop/HBase/Hive** contains some exceptionally strong individual cases—stronger than generic Jira links because the comments spell out the dependency. For example, Hive maintainers explicitly postponed a Guava upgrade because Curator, Hadoop, Tez and other dependencies were not aligned. But these cases are scattered across unrelated version transitions rather than organized as one bounded migration corpus. citeturn23search16turn23search9

The practical recommendation is therefore a **small historical gold set assembled from OpenStack + Mozilla + Kubernetes**, supplemented by a clearly marked synthetic layer for causes that public trackers underrepresent, especially “wrong owner” and “team busy elsewhere.” The BUMP research benchmark is useful for realistic dependency-breakage fixtures, but it has no ticket lifecycle or organizational dependency semantics. citeturn9view3turn6search13

## Ranked candidates

Here, **R1–R5** correspond to your five requirements: R1 = many repos/teams with code-visible dependencies; R2 = bounded migration with start/end; R3 = migration tickets with timelines; R4 = actual blocking evidence; R5 = publicly downloadable history. `✓` means strong, `△` means usable with caveats, and `✗` means materially missing.

| Rank | Candidate | R1 | R2 | R3 | R4 | R5 | Rough scale | Can plausibly yield ≥30 true blocks? | ≥30 stalled tickets with knowable reason? |
|---|---|---:|---:|---:|---:|---:|---|---|---|
| **1** | [OpenStack: Remove Copies of Incubated Oslo Code](https://governance.openstack.org/tc/goals/completed/ocata/remove-incubated-oslo-code.html) citeturn18search6 | ✓ | ✓ | △ | ✓ | ✓ | ~23 repos still had copied Oslo code at the Aug. 2016 scan; at least 37 individually linked Gerrit completion changes | **Likely**, after mining Gerrit/library-release ordering | **Possible, not proven**; planning bugs are sparse |
| **2** | [Mozilla Bugzilla: `nsIURI` thread-safety/immutability migration](https://bugzilla.mozilla.org/show_bug.cgi?id=922464) citeturn9view0 | △ | ✓ | ✓ | ✓ | ✓ | 43 direct dependency bugs; several Core components; ~6-year project span | **Maybe**; 43 edges exist but many are decomposition, not causal blocks | **Likely**, after manual/comment classification |
| **3** | [Kubernetes `extensions/v1beta1` removal tracker](https://github.com/kubernetes/kubernetes/issues/43214) citeturn19search16 | ✓ | ✓ | △ | ✓ | ✓ | Several repos/SIGs; roughly tens, not hundreds, of centrally linked work items; 2017→1.22 timeline | **Not from the central tracker alone** | **No obvious 30-case set** |
| **4** | [Apache Hadoop/HBase/Hive dependency-transition cluster](https://issues.apache.org/jira/browse/HIVE-15393) citeturn23search16 | ✓ | △ | ✓ | ✓ | ✓ | Hadoop, Hive, HBase, Tez, Curator, Druid and related modules; cases scattered over many Jiras | **Probably across many migrations**, not one coherent campaign | **Probably**, but causal labeling would be expensive |
| **5** | [NumPy 2.0 downstream ecosystem](https://numpy.org/devdocs/numpy_2_0_migration_guide.html) citeturn20search18turn20search2 | ✓ | △ | ✓ | ✓ | ✓ | Hundreds of downstream packages in principle; no single tracker | **Very likely after ecosystem mining** | **Unclear**, and no clean migration end |
| **6** | **BUMP breaking-dependency benchmark** citeturn9view3turn6search13 | △ | △ | ✗ | △ | ✓ | 571 reproducible breaking updates from 153 Java projects; ~250 GB Docker corpus | **No** organizational blocks | **No** |
| **7** | **Chromium source + issue tracker/Gerrit** citeturn21search3turn21search35 | ✓ | △ | △ | △ | ✓ | Enormous source tree and many component teams; I did not find a sufficiently coherent public migration tracker in this pass | **Unproven** | **Unproven** |

A recurring measurement caveat matters here: none of the strongest candidates publishes a ready-made “median ticket stall duration” statistic for the migration. Where I could verify a campaign window or individual durations, I report them below; otherwise I mark the median as **to compute from the public API/history**, rather than inventing one.

## OpenStack is the strongest cross-repository corpus

**Dataset and access.** The strongest seed is OpenStack’s Ocata community goal, [“Remove Copies of Incubated Oslo Code.”](https://governance.openstack.org/tc/goals/completed/ocata/remove-incubated-oslo-code.html) OpenStack had historically copied common modules from `oslo-incubator` into consumer repositories. The Oslo team then graduated those modules into separately released libraries, and the project-wide goal required consumers to declare dependencies on the replacement libraries, remove the copied code, and delete `openstack/common`. The goal specifies a common Gerrit topic, `goal-remove-incubated-oslo-code`, specifically so the migration can be tracked. citeturn18search6

This is unusually close to Meridian’s intended setting. The migration involves a library-owning team and many consuming teams; dependency usage is visible directly in Python imports, copied source, and requirements files; and the new libraries were actual independently released artifacts. OpenStack’s library-graduation procedure explicitly establishes an ordering: the graduated library must first receive a successful release, it must be admitted to global requirements so other OpenStack projects can consume it, and only then are consuming projects coordinated and migrated. citeturn2search16

That is stronger evidence than a Jira “blocks” edge. It means a consumer can be mechanically identified as **not yet migratable** because its replacement library is not yet released/allowed, exactly the sort of latent upstream dependency Meridian is supposed to discover from code and package state. citeturn2search16

**Scale.** On **August 5, 2016**, the goal’s own scan found roughly **23 repositories** still containing `openstack/common`, including `heat`, `designate`, Storyboard, `castellan`, `solum`, and a long list of Python client repositories such as `python-cinderclient`, `python-glanceclient`, `python-heatclient`, `python-manilaclient`, `python-mistralclient`, `python-muranoclient`, `python-saharaclient`, and others. citeturn18search6

The same page then records completion by project team. Counting only the Gerrit review IDs explicitly printed on the page gives **at least 37 individual completion changes**, before counting topic queries and named completion links whose underlying reviews are not expanded inline. Examples include six reviews for Infrastructure, four for `python-mistralclient`, three for Monasca, seven for Solum, two for Glance, two for release tooling, and two for Winstackers. citeturn18search6

The campaign therefore comfortably clears the “tens of migration changes” threshold. It also spans genuinely separately owned repositories, unlike Spark or a single Django repository. citeturn18search6

**Tickets and stall evidence.** This is where OpenStack becomes imperfect. Some teams supplied Launchpad or StoryBoard planning artifacts—for example Infrastructure links StoryBoard story `2000776`, Barbican links Launchpad bug `1643909`, Glance links bug `1639487`, and TripleO links bug `1636767`—but many teams put `None` under planning artifacts and only recorded Gerrit completion reviews. citeturn18search6

So the **share of migration work with a conventional ticket is low and not standardized**. I would not use issue-link coverage as ground truth. Instead, construct the timeline from:

`goal introduced → replacement library release → global-requirements availability → consumer Gerrit review opened → first patchset → merged/released`.

Those events are closer to the causal mechanism Meridian cares about than ticket links anyway. The OpenStack graduation documentation explicitly says consumers are coordinated only after the replacement library is fully integrated and released. citeturn2search16

There are also other OpenStack-wide migrations that preserve stall reasons unusually well. For example, the later “Switch legacy Zuul jobs to native” goal records that some jobs had not migrated because required native Tempest functionality was unavailable and native Grenade jobs were not yet available. That shows the same public-goal infrastructure can contain explicit upstream-block explanations, although it is a separate migration from the Oslo case. citeturn18search3

**Durations.** The verified baseline scan is August 5, 2016, and the work is classified as a completed **Ocata** community goal, so the campaign is roughly one OpenStack release cycle in duration. The page does not publish a per-review median or slowest review duration; those should be computed from Gerrit review creation/patchset/merge timestamps. citeturn18search6

**Traps.** Do not equate a Gerrit topic with a blocker edge. A topic says “this change belongs to the migration,” not “this change waited on X.” The useful ground truth is the combination of code dependency, replacement-library release ordering, requirements changes, explicit cross-repository dependency metadata where present, and review discussion. Also expect renamed/retired repositories and old StoryBoard/Launchpad links. The goal page itself is valuable because it freezes the historical mapping between project teams and their migration artifacts. citeturn18search6

**Verdict.** For **30 cross-team blocks**, this is the candidate I would mine first. I think **≥30 is plausible**, but I would not label all 37+ migration reviews as blocks automatically: each needs an upstream library/version or review dependency that proves ordering. For **30 stalled tickets with a knowable reason**, the answer is less certain because many teams skipped planning tickets. The underlying review timelines probably contain enough slow cases, but that requires an extraction/manual-validation pass rather than using a ready-made field.

## Mozilla gives the cleanest stalled-work timelines

**Dataset and access.** Mozilla Bugzilla is unusually well suited to the second Meridian feature because bugs expose structured `depends on`/`blocks` relationships and Bugzilla provides web-service operations for bug lookup, search and history. Mozilla’s native REST service is publicly available on `bugzilla.mozilla.org`. citeturn10search9turn0search6

The strongest specific migration I found is Bug **922464**, [“Centralize URI parsing and make it threadsafe.”](https://bugzilla.mozilla.org/show_bug.cgi?id=922464) Its scope included removing `nsIProtocolHandler.newURI`, making `nsIURI`/`nsIURL` immutable, centralizing URI parsing and making URI handling safe off the main thread. citeturn9view0

The meta bug currently lists **43 direct dependencies**. Those include bugs such as `1416791`, `1420954`, `1423961`, `1425318`, `1431204`, `1431760`, `1432187`, `1432257`, `1432519`, `1432602`, `1432613`, `1432928`, `1434766`, `1436589`, `1440191`, `1441688`, `1442239`, `1447190`, `1448058`, `1448327`, `1453633`, `1460198`, `1476928`, `1522596`, `1532253`, `1536744` and others. citeturn9view0

One child, Bug **1416791**, is specifically about reducing the mutability of `nsHostObjectURI`, directly matching the parent API-immutability migration. citeturn8search11

**A real block, not just a tracker relation.** Bug **1443925**, “Make nsIPrincipal objects threadsafe,” is the standout example. Its discussion says the principal work should wait for `nsIURI` thread-safety to land and stabilize first, and it declares a dependency on the `OMT-nsIURI` work. That is exactly a downstream component whose own migration could not safely proceed until another API-owning component completed prerequisite work. citeturn8search16

This makes Mozilla much more useful than a tracker in which “depends on” merely means work breakdown. You can define a high-precision positive set by requiring **both** a Bugzilla dependency edge and causal language in comments such as “wait for,” “once X lands,” or “blocked until.” citeturn8search16

**Scale and timing.** The meta bug’s direct graph gives you **43 immediately bounded work items**. The overall project ran for roughly six years; near completion, a maintainer wrote that with Bug `1536744` closed, the project could officially be considered complete and remaining bugs were usability/performance follow-ups. citeturn9view0

The exact median child-bug duration is not published on the meta page and should be computed from Bugzilla history. The **slowest possible direct children are on the order of the project’s multi-year span**, which is exactly the kind of long-tail behavior you want for stall detection. citeturn9view0

For the “dependency-data share” metric, there is an important distinction. **100% of the 43 selected children are dependency-linked to the meta bug by construction**, but that does *not* mean 100% are true causal blockers. The share that represents cross-team sequencing has to be determined from component ownership, source-level dependencies, and comments.

**Repos versus teams.** Mozilla is a caveat for Meridian because this is predominantly a large shared source tree rather than dozens of independent repositories. It does, however, cross Bugzilla components and engineering ownership domains, and the prerequisite API is visible in code. Thus it is a strong test of **hidden team boundaries in a monorepo**, but a weaker test of package-release chains.

**Traps.** Meta-bug dependency graphs mix several semantics: decomposition (“this is part of the project”), prerequisite ordering, and downstream work unlocked by the project. Do not blindly translate `depends_on` into a Meridian “blocked by.” Also distinguish bug closure from the first implementation change: a bug may remain open for cleanup long after the relevant code landed.

**Verdict.** I would expect this corpus to give **30+ stalled migration tickets** once you pull the 43 direct children and their histories, but I would not claim 30 *causal cross-team blocks* from the graph without hand-checking. As a test set for “why did this ticket sit open?”, though, this is probably the strongest public candidate.

## Kubernetes and Apache supply high-quality causal examples

**Kubernetes.** The central GitHub issue [#43214, “stop serving extensions/v1beta1 and networking.k8s.io/v1beta1 in 1.22”](https://github.com/kubernetes/kubernetes/issues/43214), is one of the best migration-tracker documents I found because it explicitly lays out prerequisite ordering across versions and repositories. It was opened on **March 16, 2017** and tracks the staged removal of deprecated workload APIs and Ingress APIs. citeturn19search16

For the workload APIs, the tracker says Kubernetes first had to switch internal controllers/tests/templates, update add-on manifests, update pruning, update the add-on manager, update the separately maintained `cluster-proportional-autoscaler`, and update **kops** so cluster bring-up stopped using `extensions/v1beta1`. Only after those tasks and SIG acknowledgements was the project to stop serving the old APIs by default in 1.16 and remove the ability to re-enable them in 1.18. citeturn19search16

That is exceptionally good causal ground truth: the `cluster-proportional-autoscaler` and `kops` tasks are not merely related tickets; they are explicit preconditions in the shutdown plan. The tracker links `kubernetes-sigs/cluster-proportional-autoscaler#54` and `kops#6273` from the core migration checklist. citeturn19search16

Ingress then had a longer sequence: replicate the API into `networking.k8s.io`, improve the beta API, promote it to v1, update examples and in-organization deployments, and finally stop serving both beta forms in 1.22. The official migration guide confirms that the old API versions were removed release by release and identifies the replacement API versions consumers must adopt. citeturn19search16turn19search0

The 1.16 release notes independently document the removal of `extensions/v1beta1`/`apps/v1beta*` workload APIs and the temporary ability to re-enable them, with complete removal planned later. citeturn19search10

For Meridian, this gives a nice chain:

`old API consumer in repo A → prerequisite client/manifest migration → upstream Kubernetes serving policy → removal release`.

The downside is scale. The central tracker contains on the order of tens of concrete migration tasks, not an obvious 30+ independent stalled tickets. The “dependency share” is excellent **within the checklist** because every item is explicitly part of the ordering plan, but GitHub does not give you a standardized “blocked reason” field across the downstream ecosystem. I would use Kubernetes for perhaps **10–20 very high-confidence gold cases**, not as the sole 60-case benchmark.

**Apache Hadoop/HBase/Hive.** Apache is more fragmented, but several issues are almost textbook examples of the hidden dependency Meridian is intended to detect.

HBase issue **HBASE-4233**, “Update protobuf dependency to 2.4.0a,” states that Hadoop trunk was already using that protobuf version and that the incompatibility made it **impossible for HBase to coexist** with Hadoop until HBase regenerated its protobuf code and updated its POM. A maintainer explicitly marked it a blocker because the patch was required to run against newer Hadoop. It was created August 19, 2011 and resolved August 23—four days of actual implementation time. citeturn23search9

That same issue illustrates a major data trap: although it was resolved in 2011, it was formally **closed in a bulk-closing operation in November 2015**. Measuring `closed - created` would therefore manufacture a four-year “stall” where the technical work actually took four days. Use Jira’s `resolved` timestamp and commit/build activity, not final close time. citeturn23search9

Hive issue **HIVE-15393**, “Update Guava version,” is even more valuable for stall-reason classification. The issue began because Druid used a newer Guava than Hive and referenced Hadoop’s own Guava movement. Later, a maintainer explained that Hive could not simply move to Guava 21 because its Curator libraries did not support that version yet and said he would move once the related Curator update merged. Other comments explicitly cautioned against upgrading ahead of important dependencies such as Hadoop and Tez. citeturn23search16

The same ticket records repeated CI failures—first nine failed tests, later dozens—and a March 2018 discussion observing that different parts of the stack were simultaneously using Guava 14, 16 and 19. Another maintainer pointed out that Hadoop itself had reverted its Guava choice. The ticket was ultimately closed when Hive 3.0.0 was released in May 2018. citeturn23search16

That one issue supports several Meridian labels with real evidence:

| Possible Meridian reason | Evidence in HIVE-15393 |
|---|---|
| **Upstream block** | Curator did not yet work with the desired Guava; maintainer planned to wait for its update. citeturn23search16 |
| **Risky code / broad blast radius** | Different subprojects depended on incompatible Guava versions. citeturn23search16 |
| **Migration attempted but failing** | Repeated large test-failure sets were recorded by Hive QA. citeturn23search16 |
| **Release gating** | Issue closed on release of Hive 3.0.0. citeturn23search16 |

Another HBase case, HBASE-24469, directly points to an HDFS issue fixed only in Hadoop 2.9.0 and proposes upgrading HBase’s Hadoop dependency because the bug can otherwise hang reads. citeturn23search7

The problem is corpus coherence. You can almost certainly find dozens of such cases by mining Guava, protobuf, Netty, Hadoop API and shaded-dependency transitions across ASF projects, but they do **not form one clean migration with one announcement and one removal date**. I would therefore treat Apache as a library of strong individual examples, not as the primary Meridian benchmark. And because you already ruled out generic Jira links, I would accept Apache cases only when the **comment text, version constraint, build failure or POM diff independently proves the block**.

## NumPy and BUMP are useful for dependency mechanics, not complete ground truth

**NumPy 2.0.** NumPy 2.0 is a huge natural experiment in downstream migration. The final 2.0.0 release shipped on **June 16, 2024** after roughly 11 months of development, with substantial Python- and C-API changes and binary incompatibility for extensions compiled against NumPy 1.x. citeturn20search2

The migration guide provides machine-searchable source signatures for the transition—for example changed C-API struct access and recommendations around Cython 3—so code-level dependency detection is excellent. citeturn20search18

There are also strong downstream examples. Rasterio issue **#3024**, opened before the final release, reports that a developer trying to make `geoxarray` pass against NumPy 2 was **stuck when Rasterio imported**, because the NumPy C-API behavior had changed. That gives a real chain:

`geoxarray → rasterio → NumPy 2 C API`.

citeturn20search34

DeepLabCut issue **#2624**, opened two days after NumPy 2.0’s release, records the canonical binary-ABI problem: a module compiled for NumPy 1.x could not run against NumPy 2.0, with the runtime suggesting either downgrading below NumPy 2 or rebuilding/upgrading affected modules, including use of newer pybind11 where appropriate. citeturn20search14

This ecosystem almost certainly contains far more than 30 downstream migration issues, and it is easy to recover the dependency relation from imports, wheel metadata and package requirements. The reason I rank it only fifth is that it lacks the boundaries Meridian needs for clean supervised data: there is **no single central migration tracker**, no universal issue naming convention, no shared ownership model, and no well-defined date on which “the ecosystem migration ended.” NumPy 1.x did not simply disappear when 2.0 shipped. citeturn20search2turn20search18

The structured blocker-data share is therefore effectively **near zero**: the reason generally lives in an exception trace, issue body, package pin or comment rather than a common dependency field. That can be an advantage if you specifically want to test Meridian’s ability to infer blockers from code and version state, but it makes gold-label construction expensive.

I would expect NumPy 2.0 to yield **30 hand-checkable technical dependency blocks** with enough mining, but I would be much less confident about 30 tickets where a multi-week stall has a reliably knowable organizational reason.

**BUMP.** BUMP is the strongest actual *packaged research dataset* I found. It contains **571 reproducible breaking dependency updates from 153 Java projects**. Each record pairs a pre-update commit that builds/tests successfully with a dependency-bump commit—typically from Dependabot or Renovate—that causes the build to fail. citeturn6search13turn9view3

The records include the project and organization, pull-request URL, commit IDs, dependency group/artifact, old and new versions, and a failure category. The benchmark distinguishes classes such as compilation failures, test failures, dependency-resolution failures, Maven Enforcer failures, dependency-lock failures and warning-as-error failures. citeturn9view3

Its reproducibility story is unusually good: the maintainers publish prebuilt Docker images through Zenodo, and the documented full download needs about **250 GB of free disk** for **1,142 images**—two images for each of the 571 cases. citeturn9view3

But BUMP does **not** have the data Meridian’s two new features fundamentally require. These are individual client projects experiencing breaking dependency updates, not teams waiting on one another; there is no ticket status history explaining weeks of inactivity; and the “block” is a build failure, not an organizational or release sequencing relationship. citeturn9view3turn6search21

So the verdict is **no/no** on your two 30-case thresholds. Its value is different: it is an excellent source of *honest technical breakage fixtures* so you do not have to invent compiler errors, version conflicts or failing call sites when building synthetic Meridian scenarios.

## Chromium is attractive in theory but did not clear the evidence bar

Chromium certainly has the basic substrate: public Git repositories on `chromium.googlesource.com`, many separately owned components, dependency-management repositories, and bugs/features tracked through Chromium’s issue-tracking infrastructure. The Chromium Git service exposes the source repositories publicly, and individual project pages explicitly direct bug reports to Chromium’s tracker. citeturn21search3turn21search35

There are also genuinely separate repositories in the Chrome infrastructure world; for example `infra_superproject` exists specifically to manage dependencies among infrastructure repositories via dependency metadata. citeturn21search19

What I did **not** find in this research pass was a publicly indexed, coherent Chromium migration campaign comparable to OpenStack’s Oslo goal or Kubernetes #43214 that simultaneously gives:

a deprecated internal API, a known removal endpoint, dozens of component/team tickets, and enough explicit upstream waits to establish 30 causal cases.

That absence is important because Chromium was one of the candidates you specifically suspected. I would not recommend investing in it first merely because the codebase is huge.

There is also an acquisition trap: Chromium’s normal development checkout is specialized rather than a simple small repository clone; the official public mirror points developers toward Chromium’s own checkout instructions rather than treating a plain GitHub-style clone as the normal workflow. citeturn3search17

My verdict for Chromium is therefore **“promising source, unproven dataset.”** Before spending substantial ingestion effort, I would demand one concrete migration meta-bug with at least 30 linked issues and a removal CL. I did not find that evidence strongly enough to put it above the other candidates.

## Recommended Meridian benchmark

Given the public evidence, I would **not pretend one of these datasets is a ready-made ground truth corpus**. A better benchmark would deliberately separate historical truth from synthetic test mechanics.

### Historical gold layer

The highest-value first tranche would be about **60–90 manually certified historical cases** drawn from three sources.

Use **OpenStack** for genuine cross-repository dependency chains. Start from the ~23 repositories in the Oslo-incubator scan and 37+ explicitly linked Gerrit changes. For each candidate, require a demonstrable source/package dependency on the old or replacement Oslo module and an ordering event such as the replacement library release, global-requirements admission, or a prerequisite review. citeturn18search6turn2search16

Use **Mozilla** for stall explanations. Pull the 43 direct bugs under `OMT-nsIURI`, their full Bugzilla histories, component/assignee changes and source changes. Accept a “blocked upstream” label only when comments or code dependencies independently substantiate the graph edge; Bug 1443925 is the model positive example. citeturn9view0turn8search16

Use **Kubernetes** for clean cross-repository migration ordering. The old-API shutdown tracker directly identifies prerequisite changes in Kubernetes core, kops and `cluster-proportional-autoscaler`, giving you unusually defensible “this had to happen first” labels. citeturn19search16

A gold record should preserve both the inferred relationship and the evidence used to certify it:

| Field | Purpose |
|---|---|
| `migration_id` | One bounded deprecation/removal campaign |
| `consumer_repo`, `consumer_team` | Meridian ownership target |
| `upstream_repo`, `upstream_team` | Hidden blocker owner |
| `code_edge` | Import, Maven/Python dependency, RPC/client API, copied module, manifest API |
| `deprecated_symbol_or_version` | Exact thing being migrated |
| `deprecation_at`, `removal_at` | Campaign bounds |
| `ticket_created_at` | Start of tracked work |
| `first_code_activity_at` | First patch/commit actually doing migration |
| `resolved_at` | Technical completion, distinct from administrative close |
| `blocked_from`, `unblocked_at` | Causal waiting interval where knowable |
| `reason` | `upstream`, `owner`, `risk`, `capacity`, `other`, `unknown` |
| `evidence_type` | Code diff, package constraint, release, comment, CI failure, tracker edge |
| `evidence_url` | Human-checkable primary evidence |
| `confidence` | High/medium/low |
| `synthetic` | Must stay `false` for historical cases |

The **`first_code_activity_at`** field is especially important. Apache HBASE-4233 demonstrates why ticket timestamps alone are unsafe: its technical work was resolved in four days, while administrative bulk closure came more than four years later. citeturn23search9

### Honest synthetic layer

For labels that public projects rarely expose cleanly—particularly **wrong owner** and **team busy elsewhere**—I would synthesize the *ticket workflow*, not the underlying technical dependency.

Keep the code graph real: use an actual OpenStack, Mozilla, Kubernetes or BUMP dependency pair. Keep the API/version break real. Then create a fixture-only Jira-like timeline around that dependency and explicitly mark the generated fields `synthetic=true`.

For example, a fixture could use a real consumer→library edge but assign the migration ticket to the consumer team for 21 days, reassign it to the library-owning team, wait for the real upstream-release prerequisite, then land the migration. Meridian can then be tested on “wrong owner → upstream wait” without falsely claiming that those events historically happened in that project.

For “risky code,” BUMP is an especially defensible source because its 571 cases already contain reproducible real-world dependency bumps and categorized build/test failures. You can preserve the actual before/after commits and failure while synthesizing only the project-management timeline around it. citeturn9view3turn6search13

For “busy elsewhere,” the honest approach is to synthesize capacity contention entirely: do **not** infer that a real open-source team was busy merely because an issue was inactive. Inactivity is observable; organizational priority is not, unless a maintainer explicitly says so.

### What I would use for Meridian

My ranking by feature is therefore slightly different from the overall table:

**For hidden cross-team blocks:** OpenStack first, Kubernetes second, Apache as a source of especially strong individual examples, NumPy 2.0 for a larger but noisier ecosystem.

**For why work stalled:** Mozilla first, Apache second, OpenStack third.

**For reproducible technical fixtures:** BUMP first.

The strongest defensible benchmark would be a **joined corpus rather than a single dataset**: OpenStack supplies the multi-repo ownership/dependency topology, Mozilla supplies long-lived ticket histories and explicit waits, Kubernetes supplies canonical planned dependency ordering, and BUMP supplies reproducible breakage. No individual public source I found currently gives all four properties at the 30+ / 30+ level without additional mining and manual certification.