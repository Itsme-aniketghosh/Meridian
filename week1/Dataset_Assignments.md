# Week 1 · Dataset Assignments

## TL;DR (Pair 3)

**Is it possible?** Yes for the parts our data covers. No for cross-team blocking.

- **Multi-repo scanning works.** 19 of 20 packages have a findable `url()` migration. Our scanner matched 30/30 hand-checked lines and 97% of what the migrations actually changed.
- **Blocking can't be tested with this data.** The 20 repos never call each other, so there are no cross-team chains to find. We need dependent repos, or we test blocking on fixtures only.
- **The data is thin.** 4 repos hold 80% of the call sites, so report results per repo.
- **SZZ now has an answer key.** Defects4J only records the fix, so we added 130 hand-checked "this commit caused it" labels. Our automatic method agreed 8 out of 8 times.
- **The ticket → commit join holds.** 95% of Defects4J fix commits name their ticket, the same as Spark.

Details and scripts: [pair3/README.md](pair3/README.md).

---

## Who owns what. No overlap.

| Pair | Repos you touch | Nobody else touches these |
|---|---|---|
| **1(Rochan + Rachna)** | `django/django` only | The Django framework itself | 
| **2(Chaitali + Utkarsh)** | `apache/spark` + Apache Jira | Spark and all ticket data | 
| **3(Aniket + Vishwa)** | 20 **third-party** packages that *use* Django, plus Defects4J | Other people's libraries, not Django | 

**The Django split, so it's clear:**

- **Pair 1** works *inside* Django. The framework's own source code
- **Pair 3** works on *libraries built on top of* Django. Different repos entirely, e.g. `wagtail`, `djangorestframework`

Pair 3 never clones `django/django`. Pair 1 never touches the 20 packages.

---

## First, the one-minute background

Our product finds every place in a codebase that calls a function which is being removed.

To test if we're any good at that, we need a case where **someone already did it and we know the right answer.**

That case is Django. In 2019 Django removed a function called `ugettext` (it translated text into other languages). Every place that called it had to be changed. Django did the whole migration in one commit, so we can look at the code just before, count the call sites ourselves, and compare.

**279 call sites. 118 files. That's our answer key.**

---

# PAIR 1 · Django framework source

### Your dataset, and only this

```
git clone https://github.com/django/django
cd django
git checkout 4353640ea9      # the state just BEFORE they migrated
```

One repo. The Django framework itself. Nothing else.

**The symbol you care about:** `ugettext` and its variants (`ugettext_lazy`, `ungettext`, `ugettext_noop`).

**Your job:** figure out if a computer can find those 279 places on its own.

### Questions to answer

**1. Speed.** Run tree-sitter over the whole repo. How many minutes?
> *Why: if it takes 3 hours, a daily pipeline won't work.*

**2. The hard one.** Most Django files don't call `ugettext` directly. They rename it first:

```python
from django.utils.translation import ugettext_lazy as _
...
_("Hello")        # this IS a call site, but it doesn't say ugettext anywhere
```

Tree-sitter sees `_("Hello")` and has no clue what `_` is. Try **jedi** or **pyright**. Can either one tell you that `_` is really `ugettext_lazy`?

> *Why: about 480 of the call sites look like this. If neither tool can figure it out, we lose a whole feature.*

**3. Checking by hand.** Take 30 of the 279. Confirm each one really is a call site. Time yourself.
> *Why: we need all 279 hand-checked by week 4. If 30 takes 4 hours, that plan breaks.*

**4. Junk.** What's in this repo that we should skip? Test files? Auto-generated files? Anything else?

---

# PAIR 2 · Spark + Apache Jira

```
git clone https://github.com/apache/spark
```

```
https://issues.apache.org/jira/rest/api/2/search
  ?jql=project=SPARK AND issuetype=Bug AND resolution=Fixed
```
No login needed. Just open it in a browser first and look.

**Background:** we want to know which files have caused bugs before. To do that we need to connect a bug ticket to the code that fixed it.

Apache Spark is good for this because their rule is that every commit message must contain the ticket number, like `[SPARK-1234] Fix null pointer in shuffle`.

**That commit message is the only connection between a ticket and the code.** Jira itself has no link to commits.

### Questions to answer

**1. Linkage.** Look at the last 1,000 commits. How many have a `SPARK-1234` style ID in the message?
> *Why: this is the bridge. If it's low, the whole thing doesn't work. We expect around 95%.*

> **Answer (Pair 2):** **94.0%** of the last 1,000 (96–97% per year since 2021, 0% before 2014). The rest are almost all `[MINOR]`, some of them real fixes. 50/50 hand-checked links point to the right ticket. Details: [PAIR2-REVIEW.md](PAIR2-REVIEW.md).

**2. Usable tickets.** Of those tickets, how many are actually type **Bug** and resolution **Fixed**?
> *Why: an "Improvement" isn't a bug. A "Won't Fix" never got fixed. Only Bug + Fixed teaches us anything. This number could be a lot smaller than question 1.*

> **Answer (Pair 2):** **145 of 853** linked tickets (17%), touched by 153 commits. Type is what cuts it: 45% are Sub-tasks, 28% Improvements. Resolution barely filters (99% Fixed). Project-wide: 10,981 Bug+Fixed.

**3. Duplicate people.** List the author emails. How many different emails belong to the same human?
> *Example: `john@gmail.com`, `john@apache.org`, `jsmith@company.com` are one person.*
> *Why: we say who owns a file based on who commits to it. If one person looks like three, ownership is wrong.*

> **Answer (Pair 2):** 3,381 emails → **~2,888 people (~490 duplicates, 15%)**. People with several emails wrote 75% of commits. No `.mailmap`, so we need an alias table.

**4. Bots.** How many commits are from bots like `dependabot` or `github-actions`?
> *Why: bots touch hundreds of files. They'd look like the biggest owner in the repo.*

> **Answer (Pair 2):** **~0.** Spark's merge script keeps the human as author. 2 commits by an AI agent ("Claude"). The real trap: committer = merger, so use author. 13% of recent commits have `Co-authored-by`.

**5. Pulling tickets.** How long does it take to download all the Jira tickets? Any rate limits?

> **Answer (Pair 2):** **35 min** for all 59,492 tickets with full changelog (1.8 GB raw, 118 MB gzipped). About 9 min without changelog. Anonymous, no rate limits, 0 errors in 595 requests.

---

# PAIR 3 · Third-party libraries + Defects4J

**You do not clone `django/django`. That's Pair 1.**
You clone libraries that *depend on* Django.

**Different symbol too.** Pair 1 studies `ugettext`. You study `django.conf.urls.url()`. Different migration, different year, different code.

## Task A · Find packages that had to migrate

**Background:** Django 4.0 deleted `django.conf.urls.url()`. Thousands of other libraries were using it, so all of them had to change their code.

That means **each of those libraries is a real migration**, in a separate repo, done by a separate team.

### Your 20 repos

```
github.com/encode/django-rest-framework
github.com/wagtail/wagtail
github.com/pennersr/django-allauth
github.com/django-cms/django-cms
github.com/carltongibson/django-filter
github.com/jazzband/django-debug-toolbar
github.com/django-extensions/django-extensions
github.com/django-haystack/django-haystack
github.com/django-crispy-forms/django-crispy-forms
github.com/django-guardian/django-guardian
github.com/jazzband/django-taggit
github.com/jschneier/django-storages
github.com/django-oscar/django-oscar
github.com/cookiecutter/cookiecutter-django
github.com/django-import-export/django-import-export
github.com/deschler/django-modeltranslation
github.com/django/channels
github.com/graphql-python/graphene-django
github.com/django-tenants/django-tenants
github.com/jazzband/django-two-factor-auth
```

If any of these turn out to be dead ends, replace them. Search GitHub for Python repos whose requirements mention `Django>=3.2`.

### Questions to answer

**1. For each of the 20: can you find the commit where they stopped using `url()` and switched to `re_path()` or `path()`?**

How to look:
```
git log -S "conf.urls import url" --oneline
git log -S "re_path" --oneline
```
Or read their CHANGELOG for the release that dropped Django 3.2 support.

Record: repo name, commit SHA, date, files changed. One row each.

> **Answer (Pair 3):** 19 of 20. The `-S` search misses `import include, url`; use `git log -G 'django.conf.urls' -- '*.py'`. Commit table: [pair3/README.md](pair3/README.md#django-packages).

**2. How many of the 20 did you find?**

| Found | What we do |
|---|---|
| **12 or more** | We build the cross-repo feature |
| **5 to 11** | Partial. Smaller claims |
| **Under 5** | We drop it |

Already checked: **djangorestframework** has one clean commit. **wagtail** has two. Three others turned up nothing obvious.

> **Answer (Pair 3):** 19 of 20, so build it. But 4 repos hold 80% of call sites, and the repos don't call each other, so blocking can't be tested here. Swap storages, crispy, taggit, and modeltranslation for autocomplete-light, dj-rest-auth, silk, and tastypie.

---

## Task B · Defects4J

```
https://github.com/rjust/defects4j
```

854 bugs from 17 Java projects. Each one has been hand-verified: this bug, this fix commit.

### Questions to answer

**1. Does it download and set up without trouble?**

> **Answer (Pair 3):** In Docker, yes (7 min build). Natively on Windows, no: it needs `core.longpaths=true`, Java 11, svn, and cpanm.

**2. What information does it give per bug?** List the fields.

> *Why: later we'll write code that guesses which commit caused a bug. Defects4J already knows the answer for 854 of them. If our guesses disagree with theirs, our code is broken, and we find that out cheaply.*

> **Answer (Pair 3):** Fields: `bug.id`, buggy and fixed revisions, `report.id`, `report.url`. Also the minimized patch, failing tests with stack traces, and modified classes.
>
> **The "Why" is wrong:** Defects4J records the fix, not the cause. So we added 130 hand-checked cause labels from Fonte, remapped to Defects4J v3. Our test bisection matched 8 of 8 on a 38-bug sample. See [pair3/README.md](pair3/README.md#defects4j).

---

## Questions Pair 3 added

**Are the 20 repos big enough to measure anything?**
Barely. Only 7 have 20 or more call sites, and oscar, wagtail, DRF, and cms hold 80% of the ~1,085 total. A pooled recall number is really a 4-repo number.

**Can this corpus test cross-team blocking?**
No. Each library calls Django's `url()`, never another library's. To test chains we need pairs like dj-rest-auth → allauth or djoser → DRF.

**Do aliases show up here too?**
Yes. django-autocomplete-light switched to `from django.urls import re_path as url`, so the import looks migrated but 76 `url(` calls remain. It's the same problem as Pair 1's `_`.

**Can grep be the independent check on our counts?**
No. It misses guardian, which re-exports `url` through `compat.py`, and it counts docstring examples. The lines each migration commit removed make a better check, and they match our scanner at 97%.

**Can we find migrations from commit messages?**
No. The real ones are titled "Minor cleanups" (two-factor) and "Bumps all-auth + fixes tests" (dj-rest-auth), and one came in on a branch called `translate-russian` (taggit). Read the diffs instead.

**Is our scanner accurate?**
Mostly. 30 of 30 random lines were real call sites. It found 809 of the 834 lines the migrations changed. It misses imports routed through a compat module.

**When did libraries migrate?**
9 before the deprecation (Aug 2020), 6 before the removal (Dec 2021), and 4 after. graphene-django still isn't finished. That spread is good material for the "why it stalled" feature.

**Do the published cause labels (Fonte) work as-is?**
Only 62 of 130. Defects4J v3 re-converted the Lang and Math repos, so those 68 commit IDs no longer exist. We remapped all of them by author time and subject: [defects4j_bic_labels.csv](pair3/defects4j/defects4j_bic_labels.csv).

**Can we make more cause labels automatically?**
Yes, slowly. We run the bug's test back through history until it starts failing. On 38 bugs it answered 8, all correct, at about 2 minutes each. The other 30 gave no answer, mostly because old code won't compile. Run on all 724 unlabelled bugs, that might add ~150 labels.

**Does the ticket → commit join hold on Defects4J?**
Yes. 812 of 854 fix commits (95%) name their ticket. The misses are Chart (SVN), plus a few in Time and Gson.

**What breaks on Windows?**
- 3 clones fail without `core.longpaths=true`.
- Defects4J needs Docker.
- Files written with CRLF line endings break shell scripts inside the container.

---

# Friday · one shared doc, seven numbers

| Pair | Number |
|---|---|
| 1 | Minutes for tree-sitter to scan Django |
| 1 | Can jedi or pyright resolve `_()`? **yes / no** |
| 1 | Minutes to hand-check 30 references |
| 2 | % of Spark commits with a ticket ID: **94.0%** |
| 2 | Count of Bug + Fixed tickets: **145 of 853** linked (10,981 project-wide) |
| 2 | Rough count of duplicate emails: **~490** (15%) |
| 3 | Packages found: **19 / 20** (13 clean, 7 with 20+ sites) |
| 3 | Defects4J bug-inducing labels: **130** (bisection: 8/8 exact on 38 sampled) |

---

# Rules

- Note anything weird you find
- **If a number looks bad, note it.**
- Find more packages if you can, check if the 20 i listed are all good and usable.
- I listed few questions we should look answers for.. find more i might have missed.
