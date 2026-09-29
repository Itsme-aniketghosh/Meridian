# Week 1 · Dataset Assignments
---

## Who owns what. No overlap.

| Pair | Repos you touch | Nobody else touches these |
|---|---|---|
| **1** | `django/django` only | The Django framework itself |
| **2** | `apache/spark` + Apache Jira | Spark and all ticket data |
| **3** | 20 **third-party** packages that *use* Django, plus Defects4J | Other people's libraries, not Django |

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

**2. Usable tickets.** Of those tickets, how many are actually type **Bug** and resolution **Fixed**?
> *Why: an "Improvement" isn't a bug. A "Won't Fix" never got fixed. Only Bug + Fixed teaches us anything. This number could be a lot smaller than question 1.*

**3. Duplicate people.** List the author emails. How many different emails belong to the same human?
> *Example: `john@gmail.com`, `john@apache.org`, `jsmith@company.com` are one person.*
> *Why: we say who owns a file based on who commits to it. If one person looks like three, ownership is wrong.*

**4. Bots.** How many commits are from bots like `dependabot` or `github-actions`?
> *Why: bots touch hundreds of files. They'd look like the biggest owner in the repo.*

**5. Pulling tickets.** How long does it take to download all the Jira tickets? Any rate limits?

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

**2. How many of the 20 did you find?**

| Found | What we do |
|---|---|
| **12 or more** | We build the cross-repo feature |
| **5 to 11** | Partial. Smaller claims |
| **Under 5** | We drop it |

Already checked: **djangorestframework** has one clean commit. **wagtail** has two. Three others turned up nothing obvious.

---

## Task B · Defects4J

```
https://github.com/rjust/defects4j
```

854 bugs from 17 Java projects. Each one has been hand-verified: this bug, this fix commit.

### Questions to answer

**1. Does it download and set up without trouble?**

**2. What information does it give per bug?** List the fields.

> *Why: later we'll write code that guesses which commit caused a bug. Defects4J already knows the answer for 854 of them. If our guesses disagree with theirs, our code is broken, and we find that out cheaply.*

---

# Friday · one shared doc, seven numbers

| Pair | Number |
|---|---|
| 1 | Minutes for tree-sitter to scan Django |
| 1 | Can jedi or pyright resolve `_()`? **yes / no** |
| 1 | Minutes to hand-check 30 references |
| 2 | % of Spark commits with a ticket ID |
| 2 | Count of Bug + Fixed tickets |
| 2 | Rough count of duplicate emails |
| 3 | Packages found |

---

# Rules

- Note anything weird you find
- **If a number looks bad, note it.**
- Find more packages if you can, check if the 20 i listed are all good and usable.
- I listed few questions we should look answers for.. find more i might have missed.