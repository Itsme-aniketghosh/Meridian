# Week 1 · Dataset Assignments

## TL;DR (Pair 1)

**Is it possible?** Yes for names and aliases. Not yet for method calls.

- **Scanning is instant.** 0.78 s for all 2,374 files, 0 parse errors.
- **The answer key is 265 lines, not 279.** The conversion commit changed 285 `.py` lines. 265 are real uses, and our scanner finds all 265 with 0 false finds. The other 20 are renames, comments, and rewrapping. 2 more code lines hide in a non-`.py` file: 265 of 267 overall.
- **The 279 is a plain text search.** Grep finds all 265 plus 14 lines that aren't uses (comments, docstrings, strings, test names), once the 5 definitions are dropped. It sees none of the 542 alias calls.
- **Aliases resolve.** Jedi and pyright each get 533 of 542 `_()` calls right (98.3%). The 9 misses all go through one `_ = ugettext_lazy` line, where both tools stop a step early. A one-hop rule fixes it.
- **Method calls don't.** On `is_ajax()` and `is_authenticated()`, Jedi gets 5 of 30 and pyright 2 of 30, and each gets 0 of 12 in Django's own code. Both give up when the object is a function parameter, but they're never wrong. Jedi wins the tie, narrowly.
- **Almost no junk.** 187 of the 265 edit lines are in Django itself and 78 in tests, 0 in docs, translations, migrations, generated, or vendored code. Tests are real work: keep them, labelled.
- **Hand-checking is quick.** 30 of 30 sampled lines confirmed in 9.3 minutes, so all 279 would take about 1.5 hours. The week-4 freeze holds.
- **The migration was 2017, not 2019.**

Details and scripts: [pair1/README.md](pair1/README.md).

## TL;DR (Pair 3)

**Is it possible?** Yes for the parts our data covers. No for cross-team blocking.

- **Multi-repo scanning works.** 19 of 20 packages have a findable `url()` migration. Our scanner matched 30/30 hand-checked lines and 97% of what the migrations actually changed.
- **Blocking can't be tested with this data.** The 20 repos never call each other, so there are no cross-team chains to find. We need dependent repos, or we test blocking on fixtures only.
- **The data is thin.** 4 repos hold 80% of the call sites, so report results per repo.
- **SZZ now has an answer key.** Defects4J only records the fix, so we added 130 hand-checked "this commit caused it" labels. Our automatic method agreed 8 out of 8 times.
- **The ticket → commit join holds.** 95% of Defects4J fix commits name their ticket, the same as Spark.

Details and scripts: [pair3/README.md](pair3/README.md).

## TL;DR (Pair 2)

**Can we use it?** Yes. Spark + Jira is our Jira half, ready to build on. It can't prove stalls or cross-team blocks.

- **The ticket join works.** 94% of commits name their ticket, and 50/50 hand-checked links are correct. Only 3 keys in all of history point to missing tickets.
- **Fragility is real.** Files with 3+ bug fixes in a year are 2.2% of files but get 21% of next year's fixes (9.7x).
- **Ticket history is complete.** We can rebuild any ticket's status at any past date, so P3 and P5 are testable.
- **Ownership is weak.** Our owner rule beats "last toucher" only 20% vs 18%. Identities need merging (13.5% of emails are duplicates).
- **No stalls or blocks to find.** 45% of tickets get their first commit within a day of filing, and only 2% have a block link.

Details and scripts: [pair2/README.md](pair2/README.md).

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

> **Answer (Pair 1):** **0.78 s** for all **2,374** `.py` files at `4353640ea9` (median of 3 runs; 0.60 s of it parsing). **0 of 2,374** had parse errors. Apple M4, 16 GB. Speed is not a constraint for a daily pipeline. Details: [pair1/README.md](pair1/README.md#q1--speed).

**2. The hard one.** Most Django files don't call `ugettext` directly. They rename it first:

```python
from django.utils.translation import ugettext_lazy as _
...
_("Hello")        # this IS a call site, but it doesn't say ugettext anywhere
```

Tree-sitter sees `_("Hello")` and has no clue what `_` is. Try **jedi** or **pyright**. Can either one tell you that `_` is really `ugettext_lazy`?

> *Why: about 480 of the call sites look like this. If neither tool can figure it out, we lose a whole feature.*

> **Answer (Pair 1):** **Yes.** Jedi and pyright each resolve **533 of 542** aliased calls (98.3%; bar 95%), in about 1 s total. Both miss the same **9**: calls through `_ = ugettext_lazy`, an `=` alias rather than an import, where both stop one step short. 03-data's ~480 is a line count (542 calls on 485 lines). Details: [pair1/README.md](pair1/README.md#q2--alias-resolution).

**3. Checking by hand.** Take 30 of the 279. Confirm each one really is a call site. Time yourself.
> *Why: we need all 279 hand-checked by week 4. If 30 takes 4 hours, that plan breaks.*

> **Answer (Pair 1):** **9.3 min** for 30 sites (0.31 min per site; median 13 s), so **~87 min** for all 279 (82 for the real 265). **30 of 30** were real edit lines, matching the commit. The week-4 freeze holds. Details: [pair1/README.md](pair1/README.md#q3--hand-check).

**4. Junk.** What's in this repo that we should skip? Test files? Auto-generated files? Anything else?

> **Answer (Pair 1):** **Almost nothing.** Of the 265 edit lines, **187** are in `django/` and **78** in `tests/`, and **0** in docs, `django/conf/locale/`, migrations, generated, or vendored code. Keep tests, labelled: the commit changed all 78. Skip vendored `django/utils/six.py` and non-code (docs, 2,250 `.po` / `.mo` files). Watch for code outside `.py`: `tests/i18n/commands/code.sample` has 2 edit lines. Details: [pair1/README.md](pair1/README.md#q4--junk).

## Questions Pair 1 added

**Does the 279 match what the commit changed?**
Not exactly. `c651331b34` changed 285 `.py` lines. 265 are real uses (113 import, 151 call, 1 alias), and our scanner finds all 265 with 0 false finds. The other 20 are renamed tests, rewrapped lines, comments, and docstrings.

**What does "about 480" count?**
Lines. There are 542 aliased `_()` calls on 485 lines in 96 files. Q2 scores per call.

**Are aliases only made with `import ... as _`?**
No. `tests/mail/tests.py:929` does `_ = ugettext_lazy`, used by 9 calls. Both Jedi and pyright stop at that line instead of following it to `ugettext_lazy`.

**Could a resolver hide a call by naming the new function?**
It could. The old names are second names for the new ones (`ugettext = gettext`). Neither tool did (0 of 542), but scoring must require the old name.

**Is `_` ever reused for something else in these files?**
No. For all 542 calls, the nearest meaning of `_` is the old function.

**Do Jedi and pyright ever disagree?**
No. Same answer on all 542 calls. Each tool takes about 1 s for the whole set.

**When did the migration actually happen?**
2017. `c651331b34` was committed 2017-02-07, not 2019.

**Where does the 279 come from?**
Very likely a plain text search. `git grep` for the old names finds 284 `.py` lines. Drop the 5 definition lines and you get 279 lines in 118 files, 110 with the word "import" and 169 without: all four 03-data numbers. 14 of the 279 aren't uses.

**Is a plain text search good enough?**
For counting edits, nearly. It finds all 265, plus 19 lines that aren't uses: 5 definitions, 5 strings such as `__all__`, 5 test names, 2 docstrings, 2 comments. For aliases, no: it finds 0 of the 485 alias-call lines.

**Are old names re-exported through another module?**
No. No file imports a nickname from another file. The 14 `import *` lines that could bring one in pull from modules whose `__all__` leaves the old names out.

**Should test files be skipped?**
No. They hold 78 of the 265 edit lines, and the commit changed all 78: the old function can't be deleted while tests still call it. Label them instead.

**Can migration files be spotted by their name?**
No. 32 of the 128 aren't named like `0001_name.py`, and `tests/migrations/test_writer.py` is a test, not a migration. Look for `class Migration(migrations.Migration)`.

**Is all Python code in `.py` files?**
No. `tests/i18n/commands/code.sample` holds 2 lines the commit changed. A `.py`-only scanner never opens it, so the scanner finds 265 of 267.

**Is `is_ajax()` big enough for the method-call benchmark?**
No. Django's own code calls it twice. `is_authenticated()` / `is_anonymous()`, which became properties in `c1aec0feda`, gives 28 uses.

**Can Jedi or pyright resolve method calls?**
Rarely. Jedi 5 of 30, pyright 2 of 30, and 0 of 12 each in Django's own code. They answer only when they know the object's class (pyright: `(parameter) request: Unknown`), and they were never wrong.

**Are method uses always calls?**
No. Two tests replace the method on one object (`user.is_anonymous = lambda: True`). Those lines had to change too, and a call-only scanner misses them.

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

> **Answer (Pair 2):** **94.0%** of the last 1,000 (96–97% per year since 2021, 0% before 2014). The rest are almost all `[MINOR]`, some of them real fixes. 50/50 hand-checked links point to the right ticket (consistent with ≥ 95%). Details: [pair2/README.md](pair2/README.md#spark-git).

**2. Usable tickets.** Of those tickets, how many are actually type **Bug** and resolution **Fixed**?
> *Why: an "Improvement" isn't a bug. A "Won't Fix" never got fixed. Only Bug + Fixed teaches us anything. This number could be a lot smaller than question 1.*

> **Answer (Pair 2):** **144 of 853** linked tickets (17%), touched by 152 commits. Type is what cuts it: 45% are Sub-tasks, 28% Improvements. Resolution barely filters (99% Fixed). Project-wide: 10,980 Bug+Fixed.

**3. Duplicate people.** List the author emails. How many different emails belong to the same human?
> *Example: `john@gmail.com`, `john@apache.org`, `jsmith@company.com` are one person.*
> *Why: we say who owns a file based on who commits to it. If one person looks like three, ownership is wrong.*

> **Answer (Pair 2):** 3,381 emails → **2,926 people (455 duplicates, 13.5%)**. People with several emails wrote 65% of commits. No `.mailmap`, so we need an alias table.

**4. Bots.** How many commits are from bots like `dependabot` or `github-actions`?
> *Why: bots touch hundreds of files. They'd look like the biggest owner in the repo.*

> **Answer (Pair 2):** **~0.** Spark's merge script keeps the human as author. 2 commits by an AI agent. The real trap: committer = merger, so use author. 4% of recent commits credit a different co-author.

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
| 1 | Minutes for tree-sitter to scan Django: **0.013 min** (0.78 s) |
| 1 | Can jedi or pyright resolve `_()`? **yes** (533 of 542, both tools; on method calls only Jedi 5 of 30, pyright 2 of 30) |
| 1 | Minutes to hand-check 30 references: **9.3 min** (≈87 for 279) |
| 2 | % of Spark commits with a ticket ID: **94.0%** |
| 2 | Count of Bug + Fixed tickets: **144 of 853** linked (10,980 project-wide) |
| 2 | Rough count of duplicate emails: **455** (13.5%) |
| 3 | Packages found: **19 / 20** (13 clean, 7 with 20+ sites) |
| 3 | Defects4J bug-inducing labels: **130** (bisection: 8/8 exact on 38 sampled) |

---

# Rules

- Note anything weird you find
- **If a number looks bad, note it.**
- Find more packages if you can, check if the 20 i listed are all good and usable.
- I listed few questions we should look answers for.. find more i might have missed.
