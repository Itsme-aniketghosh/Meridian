# Pair 1 · how to run week 1

For Rochan and Rachna. Your repo is `django/django`, and nothing else. The task and questions are in [Dataset_Assignments.md](../Dataset_Assignments.md#pair-1--django-framework-source). This file covers how to answer them so the results hold up.

## What we already know

From [03-data](../../docs/03-data.md):
- At `4353640ea9`: 279 lines to edit (110 imports, 169 calls) in 118 files.
- 649 invocations in total, 480 of them through an alias like `_ = ugettext_lazy`.
- The conversion commit is `c651331b34`. It touched about 289 `.py` lines.

Your job is to check those numbers with tools and hands, then say whether a resolver can handle the 480.

## Setup

```
git clone -c core.longpaths=true https://github.com/django/django
cd django
git rev-parse c651331b34^          # must print 4353640ea9...; if not, stop and tell us
git checkout 4353640ea9
pip install tree-sitter tree-sitter-python jedi pyright
```

- Use full history, not a shallow clone. The checks below need `c651331b34`.
- On Windows, `core.longpaths` is required. Save scripts with LF line endings.

## The questions

**1. Speed: tree-sitter over the whole repo**
- Parse every `.py` file, timing the full run and timing parse alone.
- Record:
  - number of files
  - number of files with parse errors (`tree.root_node.has_error`)
  - wall-clock minutes
  - machine specs (CPU, RAM, OS)
- Run it 3 times and report the median.

**2. The hard one: can Jedi or pyright resolve `_()`?**
- Use tree-sitter to find every call whose callee is a bare name bound by an import alias (`import ugettext_lazy as _`).
- For each call site, ask the resolver what the name is.
  - Jedi: `jedi.Script(path=f, project=jedi.Project(repo)).goto(line, col, follow_imports=True)`, then check `full_name`.
  - pyright: send `textDocument/definition` to `pyright-langserver --stdio`.
- Score each call: right symbol, wrong symbol, or no answer. Report counts out of the total (about 480) and the time per call.
- Answer yes only if one tool gets 95% or more right. The pass bar in [05-test](../../docs/05-test.md) is recall ≥ 0.95.
- Save every call site with both tools' answers to `results/alias_resolution.csv`.

**3. Checking by hand: 30 of the 279**
- Pick the 30 with a fixed seed (`random.seed(1)`) and save the list first.
- For each one, record `file, line, is_call_site (y/n), note, seconds`. Start the timer before you open the first file.
- Report total minutes, minutes per site, and that rate times 279. That last number decides whether the week-4 freeze holds.
- Save it to `results/hand_check_30.csv`.

**4. Junk: what to skip**
- Bucket all 279 sites, with a count per bucket: `tests/`, `docs/`, `django/conf/locale/`, migrations, generated, vendored, other.
- Name anything you think we should exclude, and say why.

## Checks we want on top

These are week-1 checks from [05-test](../../docs/05-test.md), or lessons from Pair 3:

- **Independent count.** Count the `ugettext` lines that `c651331b34` actually removed (`git show c651331b34 -- '*.py'`) and compare with your 279. Explain every line of the gap. Pair 3's version of this caught a real miss: an alias routed through a `compat.py` re-export.
- **Grep isn't enough.** Check for:
  - docstring and comment hits
  - names re-exported through another module
  - `ugettext` used as a string, not called (e.g. in `__all__`)
- **Method-call benchmark.** 05-test needs one `self.x.method()` style deprecation. Our suggestion is `HttpRequest.is_ajax()` (deprecated in 3.1, removed in 4.0). Find its conversion commit, and count sites the same way.
- **Push replay (optional).** Replay 50 commits, timing a parse of only the changed files for each. Report the p95. The bar is 5 minutes on a free runner.

## What to hand in

```
week1/pair1/
  README.md       verdict, flags, impact on 05-test, next steps, answers
  scripts/        everything that made a number (one command each)
  results/        alias_resolution.csv, hand_check_30.csv, junk_buckets.csv
```

- Put answers under each question in `Dataset_Assignments.md` as `> **Answer (Pair 1):**`, one or two lines each, and fill in your 3 Friday numbers.
- Every number carries its N ("455 of 480", not "95%").
- Save samples and seeds, so anyone can rerun and get the same list.
- Large data stays out of git (see the root `.gitignore`). Small result CSVs are deliverables, so add a `week1/pair1/.gitignore` with `!*.csv`.
- No AI co-author lines in commit messages.
- If a number looks bad, report it anyway. A bad number with an N is useful. A good number without one isn't.

For an example of the format, see [Pair 3's README](../pair3/README.md).
