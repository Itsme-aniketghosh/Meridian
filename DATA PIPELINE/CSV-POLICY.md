# CSV policy · v1

**Final.** Signed off by Utkarsh (data engineering). It replaces week 1's split rule, where Pairs 1–3 pushed CSVs and the GitLab folder kept them local.

## The rule

**Labels and results go in git. People never do. Raw data never does.**

| Kind | Examples | In git? | Where |
|---|---|---|---|
| **Labels**: answer keys and gold sets, made or checked by a person | Django 265 key, Defects4J 130 labels, GitLab cross-team blocks, GitLab stall reasons, hand-check sheets | **Yes**, scrubbed | `DATA PIPELINE/labels/` (new), or the existing `week1/` folders |
| **Results**: small tables behind a number in a README | `edit_sites.csv`, `bisect_vs_fonte.csv`, link precision | **Yes**, scrubbed | next to the script that makes them |
| **Raw data and pipeline output** | clones, Jira pages, GitLab dumps, snapshots, any `.sqlite` | **Never** | local or GCS, with its sha256 on the `run` row |

## Why this is the best rule for us

- **Every number can be checked.** "Every number has a script" only holds if the files behind the numbers are in git. Today the 30+ GitLab blocks and the 57 stall reads, our only evidence for problems #5 and #7, exist on one laptop.
- **The gold sets have to be shared.** Aniket scores the graph against the GitLab gold set, and Chaitali checks it. Both need the same file.
- **The labels are what's ours.** The data is public, but a hand-checked answer key is not. It belongs in version control, with history.
- **GDPR stays simple.** If no committed file names a person, there's nothing to delete, mask, or explain later. That's the same rule a customer deployment needs.

## What "scrubbed" means

A committed CSV must pass [check_csv.py](check_csv.py):

1. **No people columns.** None named like `author`, `email`, `username`, `name`, `by`, `added_by`, `assignee`, `reporter`, `committer`, or `person`. Owners are teams, never people.
2. **No emails** anywhere in a cell.
3. **No @handles.** Inside quotes, `@someone` becomes `@user`.
4. **Quotes ≤ 200 characters**, each with a link to its source (`evidence_url`). Anyone can reread the original. We keep only enough to show the verdict.
5. **≤ 5 MB per file.** Bigger means it's data, so it goes to GCS.
6. **`checked_by` says how a row was judged:** `model`, or the checker's initials (`US`, `VP`, …). That's how we keep model reads apart from human ones.

Team members' own initials are fine. Third-party usernames are not.

## The two GitLab files to bring in

They live on the machines of whoever made them. Scrub, then commit them to `DATA PIPELINE/labels/`.

**`gitlab_cross_team_blocks.csv`**: Vishwa's 30 plus the link pass's 30, merged. Chaitali delivers it by D6.

| Column | Values |
|---|---|
| `blocked_ref` | `gitlab-org/gitlab#537634` |
| `blocker_ref` | same format, any project |
| `waiting_team`, `blocking_team` | `group::` label, without the prefix |
| `link_added_at`, `blocker_closed_at` | `YYYY-MM-DD` |
| `verdict` | `wait`, `planned`, `partial`, `unclear`, `no`, `no_evidence` |
| `source` | `targeted_read`, `link_pass`, `both` |
| `evidence_url` | link to the note or description |
| `evidence_quote` | ≤ 200 characters, `@user` |
| `checked_by` | `model`, or initials |
| `human_verdict` | blank until the spot-check, then the same values as `verdict` |

**`gitlab_stall_reasons.csv`**: the 57 stall reads. Chaitali delivers it by D6.

| Column | Values |
|---|---|
| `ref` | `gitlab-org/gitlab#…` |
| `team` | `group::` label, without the prefix |
| `blocked_from`, `blocked_to` | `YYYY-MM-DD`. The spell ends at the earliest of: label removed, status changed, blocker closed |
| `ended_by` | `label`, `status`, `blocker`, `open` |
| `reason` | `upstream`, `owner`, `risk`, `capacity`, `other`, `unknown` |
| `cross_team` | `0` / `1` |
| `evidence_url`, `evidence_quote`, `checked_by` | as above |

## What changes in the repo

- The root `.gitignore` allows CSVs in any `labels/` folder. Everything else stays blocked by default.
- `week1/cross-team/gitlab/` keeps blocking its raw CSVs. The scrubbed copies go to `DATA PIPELINE/labels/`.
- Run `python3 "DATA PIPELINE/check_csv.py"` before every push that adds a CSV. It checks every tracked or staged CSV, and fails on any problem.
- Pipeline tables with people (`person`, `person_alias`) live only in SQLite, which is never committed. The golden test in CI uses a synthetic fixture repo with made-up authors.

## Already compliant

All 13 CSVs in git today pass the checker. Nothing has to be removed.
