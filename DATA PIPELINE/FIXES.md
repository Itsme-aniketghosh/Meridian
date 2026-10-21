# Fixes · dynamic-config

This branch fixes the problems the audit found (20 numbered, plus 16 smaller ones), moves every hard-coded setting into config, adds 5 columns and tables to the knowledge base (schema v1.1), and adds the KB proof test ([tests/test_kb.py](tests/test_kb.py)). Tests: 142, 0 failures, 6 skipped because no snapshot is local (the Django and package answer keys read `snapshots/$MERIDIAN_RUN_ID/`). Before: 60 tests, 2 failures and 12 errors on Windows. `meridian.toml` changed, so collect gives a new `run_id` and every part must rerun on it. The numbers in [SCANNER.md](SCANNER.md) and [RESOLVER.md](RESOLVER.md) (2,908 candidates, 2,054 call edges, on `973531f7fd4cfd5e`) hold until that rerun.

```
python -m unittest discover -s tests -t .       # from DATA PIPELINE/
```

## Problems and fixes

Numbered as in the audit.

| # | Problem | Where | Fix | Test | Status |
|---|---|---|---|---|---|
| 1 | git read each person's global and system git config (`diff.algorithm`, `blame.ignoreRevsFile`, `log.showRoot`, `core.autocrlf`), so two machines could write different rows | every part (`collect`, `history`, `szz`, `owners`, `scan`, `resolve`, `score_packages`) | one `common.git`, run with `GIT_CONFIG_GLOBAL=devnull` and `GIT_CONFIG_NOSYSTEM=1`. Checkouts also keep the committed LF on every OS | `test_resolve` `test_changed_lines_ignore_user_git_config`; `test_history` `Keyed`, `test_szz` `test_code_rules_from_config` (each part's `git` is `common.git`) | ✅ |
| 2 | `fnmatch` ignores case on Windows: `app/Test_Case.py` was junk `test` on Windows only | `meridian/scan.py` | `fnmatchcase` on every OS | `test_scan` `test_junk` | ✅ |
| 3 | A killed write left a `.tmp` file holding emails and names, and git didn't ignore it | `.gitignore`, `meridian/common.py` | `.gitignore` adds `out/` and `*.tmp`. `write_jsonl` removes its tmp on failure | none | ✅ |
| 4 | `check_csv.py` with no arguments crashed: it split `git ls-files` on the space in `DATA PIPELINE/`. `xt_cross_team_blocks.csv` had an `added_by` column of usernames and 40+ `@handles` | `check_csv.py`, `week1/cross-team/gitlab/xt_cross_team_blocks.csv` | `splitlines()`. `added_by` dropped, handles → `@user`. The old file is still in git history at `ebe68b8` (not rewritten) | [tests/test_check_csv.py](tests/test_check_csv.py) | ✅ |
| 5 | `scope` counted defaults, decorators, bases and a def's own name as inside the def. Every caller of Django's `get_text_list` got a bogus edge: its default `ugettext_lazy('or')` runs in the module | `meridian/scan.py`, `meridian/resolve.py` | only def/class bodies count (`scan.scopes`). One rule, shared by scan and resolve | `test_scan` `test_scope`; `test_resolve` `test_exact_edges` | ✅ |
| 6 | A call to a class never counted as a call of its `__init__`: `make()` → `F()` gave no edge | `meridian/resolve.py` | a call Jedi lands on the class counts for `F.__init__` | `test_resolve` `test_exact_edges` | ✅ |
| 7 | A ticket Fixed at `snapshot_at` and reopened after it lost `resolved_at` (Jira clears `resolutiondate` on reopen) | `meridian/tickets.py` | no date, or a date after `snapshot_at` → the last resolution in the changelog up to `snapshot_at` | `test_tickets` `test_rolled_back_to_snapshot` | ✅ |
| 8 | Bulk-close used clock hours: 20 closes from 10:50 to 11:09 weren't caught. Resolutions with no author were pooled as one person | `meridian/tickets.py` | sliding 60-minute window per author. No author is no one: skipped | `test_tickets` `test_bulk_closed_by_label_or_20_in_an_hour`, `test_cut_changelog_counted` | ✅ |
| 9 | The SZZ diff parser took a deleted line `-- x` (printed `--- x`) as a file header. A path with a space kept git's trailing tab | `meridian/szz.py` | `--- ` is read only between `diff --git` and the first `@@`. The tab is stripped | `test_szz` `test_file_headers_only_in_the_header` | ✅ |
| 10 | Windows text: config and GitLab `.work/` files read as cp1252, jsonl written with CRLF. 6 collect tests and 1 resolve test failed on Windows | `collect`, `common`, `history`, `scan`, `resolve`, `writer`, the hand checks | every open says `encoding="utf-8"`, every write `newline="\n"`. Test fixtures write paths as POSIX in TOML | `test_collect`; `test_scan` `test_hand_check_saves_utf8`; `test_resolve` `test_alias_hand_check_saves_utf8` | ✅ |
| 11 | The writer tests failed on any SQLite other than the pinned 3.50.4 | `meridian/writer.py` | `check_sqlite` runs only in the command. `write()` and its tests use the machine's SQLite | `test_history` `test_sqlite_version_is_pinned`, `test_history_then_writer` | ✅ |
| 12 | The gold-set scrubber let through usernames found in `/usr/share/dict/words` (on Mac, not Windows: different output per OS), matched case-sensitively, and without the raw dump scrubbed less, silently | `labels/build_gitlab_gold.py` | every username of 4+ characters, English words too, `re.I`. No raw dump → error. One username left in `labels/gitlab_stall_reasons.csv` scrubbed by hand. The labels CSVs should still be regenerated once someone has the raw dump | [tests/test_gold.py](tests/test_gold.py) | ✅ |
| 13 | The gold set marked a pair `both` when the same list had it twice | `labels/build_gitlab_gold.py` (`put`) | `both` only when both lists have the pair. The committed `labels/gitlab_cross_team_blocks.csv` may still carry a wrong `both` until regenerated | `test_gold` `test_put_keeps_one_source_unless_both_lists_have_the_pair` | ⬜ |
| 14 | The resolver crashed loading a candidate with U+2028 in its line: `splitlines()` splits there | `meridian/resolve.py`, `tests/score_packages.py` | `common.read_jsonl` splits on `\n` only | `test_resolve` `Command` (`app/sep.py` through the one command) | ✅ |
| 15 | Jedi calls in `imported_from` and `one_hop` had no `try`: one Jedi crash stopped resolve | `meridian/resolve.py` | one `try` in `Repo.goto`: a crash is no answer, logged. Failures counted in the per-repo log line | `test_resolve` `test_jedi_failure_is_no_answer_and_counted` | ✅ |
| 16 | The owners window (180 days up to `snapshot_at`) was empty for Django and the packages, pinned years before `snapshot_at`: no `top_person_id`, `n_commits` 0 | `meridian/owners.py` | for `path` and `repo` modes the window ends at the pinned commit's `authored_at` | `test_owners` `test_repo_window_ends_at_the_pinned_commit` | ✅ |
| 17 | "Adding an email never renames the person" was false: an added email that sorts first, or two groups merging, gives a new `person_id` | `CONTRACTS.md`, `schema.sql` comment, `owners.py` docstring | wording fixed. The rule is unchanged | `test_owners` `test_person_id_is_hash_of_smallest_email` | ✅ |
| 18 | Three copies of the file walk and its rules drifted: resolve's `rglob("*.py")` matched `Views.PY` on Windows and tried to read a folder `pkg.py`; scan used `os.walk`; `tests/standin_candidates.py` had its own roles | `meridian/scan.py`, `meridian/resolve.py`, `tests/` | one walk (`scan.files`), used by resolve. `standin_candidates.py` deleted; `test_resolve` builds candidates with `scan.scan_repo` | `test_resolve` `test_files_are_the_scanners`; `test_scan` `test_unreadable_folder_halts` | ✅ |
| 19 | Risky logic had no tests: bulk close, the scrubber, `check_csv`, the keyed share, owner modes, fragility, root commits, epic parents | `tests/` | new files `test_check_csv.py`, `test_gold.py`, `test_kb.py`. New classes `test_history.Keyed`, `test_owners.Modes`, `test_resolve.ReceiverFrom` and `Helpers`, `test_szz.Fragility`, `test_tickets.Build`, plus new cases in every part's file | 142 tests | ✅ |
| 20 | `[datasets.spark] history_start` was read by nothing; `FIX_SINCE` was hard-coded in `szz.py` | `meridian.toml`, `meridian/szz.py`, `meridian/szz_check.py` | `[szz] fix_since`. `history_start` removed | `test_szz` `test_which_commits_are_fixes` | ✅ |

### Smaller ones, fixed

| Problem | Where | Fix | Test |
|---|---|---|---|
| A GitLab Epic parent was keyed as an issue of the project (`gitlab-org/gitlab#<epic iid>`) | `meridian/tickets.py` | `gl_parent` skips Epic parents. The `epic` field links them | `test_epic_parent_is_not_an_issue` |
| GitLab short refs (`gitaly#5`) kept as written, so one link showed up twice next to `gitlab-org/gitaly#5` from `blockedByIssues` | `meridian/tickets.py` (`gl_ref`) | same namespace added | `test_epic_parent_is_not_an_issue` |
| Jira links to project tickets not in the snapshot were dropped silently | `meridian/tickets.py` | still left out, now counted in the log | `test_cut_changelog_counted` |
| A changelog Jira cut short (`total` > histories) went unnoticed, so the rollback could be wrong | `meridian/tickets.py` | counted in the log | `test_cut_changelog_counted` |
| `szz_check` divided by zero with no risky files or no fixes next year | `meridian/szz_check.py` | prints `n/a` | `test_no_fixes_next_year_says_na` |
| Test-file regexes copied into 3 files and drifting (`Tests?.java` vs `Test.java`) | `szz.py`, `szz_check.py`, `owners.py` | explicit config: `[szz] test_paths` (never blamed), `[checks] test_files` (week 1's) | `test_code_rules_from_config` |
| `Co-authored-by` emails with spaces inside `<>` didn't match | `meridian/owners.py` | stripped | none |
| Jedi's `sys_path` came from the active virtualenv (site-packages, working directory) | `meridian/resolve.py` | explicit: the repo, its snapshot dependencies, the standard library | `test_sys_path_is_repo_deps_stdlib` |
| parso's cache was never cleared, and every file's tree was kept | `meridian/resolve.py` | cache emptied per repo, whole-repo walks keep no trees | `test_parso_emptied_per_repo_and_walks_keep_no_trees` |
| Jedi failures while finding callers gave no edges and no count | `meridian/resolve.py` | counted in the per-repo log line | `test_jedi_failures_while_finding_callers_are_counted` |
| `check_csv` missed people columns behind Excel's BOM, plurals (`authors`), camelCase (`authorEmail`), spaces and dashes | `check_csv.py` | `utf-8-sig`, `s?`, `words()` | `test_check_csv.py` |
| `read_jsonl` and `write_jsonl` copied into 6 files | `owners`, `resolve`, `history`, `scan`, `score_packages`, `build_gitlab_gold` | one each, in `common` | covered by every part's tests |
| `owners --top`: the file loop's `top` variable overwrote the `--top` argument (found while fixing) | `meridian/owners.py` | renamed `top_person` | `test_top_lists_groups_joined_on_a_local_part_with_other_names` |
| A root commit as a fix had no parent to blame and crashed SZZ | `meridian/szz.py` | skipped, counted in the log | `test_root_commit_skipped_and_counted` |
| A symlink in a checkout depended on the OS | `meridian/collect.py` | `core.symlinks=false`: a symlink is its target text on every OS | none |
| owners' own check of settings it can't switch (`IMPLEMENTED`) covered only owners | `meridian/owners.py` → `meridian/common.py` | `common.FIXED`, checked by `load_config` for every part (below) | `test_config_the_code_doesnt_implement_stops` |

### Still open

| Problem | Why open | Status |
|---|---|---|
| People merged on a shared email local part may be strangers | `owners --top` now lists every such group with different names, for a hand check. The rule is unchanged | ⬜ |
| Memory and run time on real data | not measured since these changes. Measure on the rerun | ⬜ |
| Django commits keep the patch's original author date | some fall outside the window that ends at the pinned commit's `authored_at` | ⬜ |
| Quotes where a username is an English word now read `user` | the wide scrub is on purpose: a dictionary let real usernames through | ⬜ |
| Git history still holds the old usernames | `ebe68b8` and the pre-scrub labels CSVs. History not rewritten | ⬜ |
| Labels CSVs from before #12 and #13 | regenerate with `labels/build_gitlab_gold.py` once someone has the raw GitLab dump | ⬜ |

## Every setting in config

`meridian.toml` is what a part computes: hashed into the `run_id`, so a change means a new run. `infra.toml` is how it runs: never hashed, and nothing in it may change a row. Parts read it with `common.load_infra(section)`; `$MERIDIAN_INFRA` points at another copy.

| Was hard-coded | In | Now | Changes the `run_id` |
|---|---|---|---|
| `GITLAB_GRAPHQL` | `collect.py` | `[datasets.gitlab] graphql` | yes |
| local part of 4+ characters | `owners.py` | `[identity] local_part_min_length = 4` | yes |
| `FIX_SINCE` (and the unused `[datasets.spark] history_start`) | `szz.py` | `[szz] fix_since` | yes |
| `TEST` | `szz.py` | `[szz] test_paths` | yes |
| `COMMENT` | `szz.py` | `[szz] comment_prefixes` | yes |
| `KEEP_NAME_ONLY` | `scan.py` | `[scanner] keep_name_only` | yes |
| `EDIT_ROLES` | `scan.py` | `[scanner] edit_roles` | yes |
| — (new) | `scan.py` | `[scanner] deprecation_markers` | yes |
| `SRC` | `szz_check.py`, `owners.py` | `[checks] source_files` | yes |
| `TEST` (week 1's) | `szz_check.py`, `owners.py` | `[checks] test_files` | yes |
| 365 days | `szz_check.py` | `[checks] fragility_window_days` | yes |
| `last=1000` | `history.py` | `[checks] keyed_last_commits` | yes |
| `"%Y-%m-%dT%H:%M:%SZ"` | `szz.py`, `szz_check.py` | `[run] time_format`, read by szz, szz_check and owners, and pinned in `common.FIXED` (it's the contract's one time format), so collect, history and tickets keeping the literal can't drift | yes |
| curl tries 6 | `collect.py` | `[collect] http_tries` | no (`infra.toml`) |
| curl `--max-time 120` | `collect.py` | `[collect] http_timeout_s` | no |
| backoff 1 s | `collect.py` | `[collect] http_backoff_s` | no |
| ×10 on HTTP 429 | `collect.py` | `[collect] http_429_factor` | no |
| GraphQL tries 6 | `collect.py` | `[collect] graphql_tries` | no |
| GraphQL backoff 5 s | `collect.py` | `[collect] graphql_backoff_s` | no |
| 20 issues per query | `collect.py` | `[collect] gitlab_batch` | no |
| SZZ workers (one per CPU) | `history.py` | `[history] szz_workers` (0 = one per CPU) | no |

**`common.FIXED`** lists the `meridian.toml` settings the code follows but can't switch: `[run] id_hash`, `id_length`; `[identity] use_mailmap`, `merge_on`, `lowercase_emails`, `use_author_not_committer`, `coauthors_count`; `[ownership] skip_merges`, `owner`, `tie_break`; `[tickets] key_must_exist`, `key_sources`, `fields_as_of_snapshot`; `[szz] blame_at`, `skip_whitespace`, `skip_comments`; `[scanner] tree_sitter_python`; `[resolver] tool`, `env`; `[graph] imports_count_as_edges`. Changing one stops the run, instead of being silently ignored.

`[rules]` (stage 5) and `[outcomes]` (stage 6+) are new. They're read by stages not built yet; [KNOWLEDGE-BASE.md](KNOWLEDGE-BASE.md)'s queries use their values today.

## Knowledge base v1.1

`schema.sql` v1 → v1.1: 23 tables → 25. Each addition is there because a planned feature had nothing to read. Full check and proof: [KNOWLEDGE-BASE.md](KNOWLEDGE-BASE.md).

| Added | Written by | Needed by |
|---|---|---|
| `ticket.description` | history | ticket checks (done condition, bug without repro) and the manager's rewrites: v1 kept only `desc_empty` |
| `candidate.in_except`, `call_site.in_except` | scan, then join | stall reason #5 (semantics unclear: the call sits inside `except`), and no LLM diff there |
| `module_import` (table) | scan | stall reason #7 (no test imports the module) and the push check's scope (changed files plus their importers). `call_edge` excludes imports by rule |
| `deprecation` (table) | scan | push check P2 (new call to a deprecation-marked symbol) and the legacy radar |
| `resolution.receiver_from` | resolve | `blocked_by`: for `x.f()`, which function made `x` |
| `blocked_by.depth`: `CHECK (depth BETWEEN 1 AND 3)` → `CHECK (depth >= 1)` | graph | depth is `[graph] blocked_by_max_depth`, a setting, not a schema rule. [RESOLVER.md](RESOLVER.md): `blocked_by` may need depth 5 |

## Rerun on the new run_id

After this branch merges. Settle any other `meridian.toml` change first (the 315 keyword-argument names are now `[scanner] keep_name_only`): every change after step 1 means another new `run_id`.

1. **Vishwa:** make the run. The last line prints `run_id=<new id>`.
   ```
   python -m meridian.collect --run-id new --config meridian.toml
   ```
2. **Vishwa:** check it: collect again with `--run-id <new id>` must print the same ID.
3. **Vishwa:** `python -m meridian.history --run-id <new id> --config meridian.toml`, then post the ID ([PENDING-TASKS.md](PENDING-TASKS.md), "how to post the new run_id").
4. **Rachna:** `python -m meridian.scan --run-id <new id> --config meridian.toml`
5. **Rochan:** `python -m meridian.resolve --run-id <new id> --config meridian.toml`, then `python -m tests.score_packages --run-id <new id> --config meridian.toml`
6. **Chaitali:** `python -m meridian.owners --run-id <new id> --config meridian.toml --top 20`
7. **Cross-machine check:** run steps 1–6 on Windows and on a Mac. The sha256 of every `out/<new id>/*.jsonl` (under `$MERIDIAN_DATA`, or the repo root) must match.
   ```
   sha256sum out/<new id>/*.jsonl        # Mac: shasum -a 256
   ```
   Any difference is a bug: fix it before the join.

Then update the numbers in [SCANNER.md](SCANNER.md) and [RESOLVER.md](RESOLVER.md).
