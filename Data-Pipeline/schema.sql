-- Meridian graph data block · schema v1.2 (v1.1 + call_site.effective_sha, call_edge.introduced_sha: UPDATE-PLAN.md #17. v1.1 = v1 + description, in_except, receiver_from, module_import, deprecation; see FIXES.md)
-- The contract every part writes to. Change it only with Utkarsh and Aniket's OK.
--
-- Conventions (full list in CONTRACTS.md):
--   every table carries run_id · times are UTC text 'YYYY-MM-DDTHH:MM:SSZ' · paths are repo-relative POSIX
--   booleans are 0/1 · lists are sorted JSON arrays · IDs are content hashes (16 hex chars)
--   rows are inserted in primary-key order · WITHOUT ROWID so the file layout follows the key

PRAGMA page_size = 4096;
PRAGMA journal_mode = DELETE;

-- ───────────────────────── Stage 0 · run ─────────────────────────  owner: Vishwa

CREATE TABLE run (
  run_id          TEXT PRIMARY KEY,          -- h(manifest without run_id)
  snapshot_at     TEXT NOT NULL,             -- the only "now" any stage may use
  repo_shas       TEXT NOT NULL,             -- JSON {"owner/name": "<40-char sha>"}
  source_hashes   TEXT NOT NULL,             -- JSON {"jira/SPARK": "<sha256 of raw pages>", ...}
  config_hash     TEXT NOT NULL,             -- sha256 of meridian.toml, normalized
  image_digest    TEXT NOT NULL,             -- container or lockfile digest
  model_versions  TEXT NOT NULL DEFAULT '{}',-- empty in step 1
  status          TEXT NOT NULL CHECK (status IN ('complete','partial','halted')),
  notes           TEXT NOT NULL DEFAULT ''
) WITHOUT ROWID;

-- ───────────────────────── Stage 1 · Vishwa · collect + bug history ─────────────────────────

CREATE TABLE git_commit (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,             -- 'owner/name'
  sha             TEXT NOT NULL,
  parent_sha      TEXT,                      -- first parent; NULL for root
  seq             INTEGER NOT NULL,          -- 0.. along first-parent history
  first_parent    INTEGER NOT NULL CHECK (first_parent IN (0,1)),
  is_merge        INTEGER NOT NULL CHECK (is_merge IN (0,1)),
  authored_at     TEXT NOT NULL,
  author_email    TEXT NOT NULL,             -- lowercased. Ownership uses author, never committer
  committer_email TEXT NOT NULL,
  subject         TEXT NOT NULL,
  PRIMARY KEY (run_id, repo, sha)
) WITHOUT ROWID;

CREATE TABLE commit_file (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  sha             TEXT NOT NULL,
  path            TEXT NOT NULL,             -- path after the commit
  old_path        TEXT,                      -- set on rename
  added           INTEGER,                   -- NULL for binary
  deleted         INTEGER,
  PRIMARY KEY (run_id, repo, sha, path)
) WITHOUT ROWID;

CREATE TABLE ticket (
  run_id          TEXT NOT NULL,
  key             TEXT NOT NULL,             -- 'SPARK-1234' or 'gitlab-org/gitlab#537634'
  tracker         TEXT NOT NULL CHECK (tracker IN ('jira','gitlab')),
  type            TEXT NOT NULL,
  effective_type  TEXT NOT NULL,             -- sub-tasks take the parent's type
  parent_key      TEXT,
  status          TEXT NOT NULL,             -- as of snapshot_at
  resolution      TEXT,                      -- as of snapshot_at
  created_at      TEXT NOT NULL,
  resolved_at     TEXT,                      -- as of snapshot_at
  components      TEXT NOT NULL DEFAULT '[]',-- JSON, sorted. Jira components or GitLab group:: labels
  assignee        TEXT,
  reporter        TEXT,
  bulk_closed     INTEGER NOT NULL CHECK (bulk_closed IN (0,1)),
  desc_empty      INTEGER NOT NULL CHECK (desc_empty IN (0,1)),
  summary         TEXT NOT NULL,
  description     TEXT NOT NULL DEFAULT '', -- as of the pull. Ticket checks (done condition, repro) and stack-trace mapping read it
  PRIMARY KEY (run_id, key)
) WITHOUT ROWID;
-- Tickets created after snapshot_at are not written.

CREATE TABLE ticket_event (
  run_id          TEXT NOT NULL,
  key             TEXT NOT NULL,
  at              TEXT NOT NULL,             -- <= snapshot_at only
  field           TEXT NOT NULL,             -- status, resolution, label, link
  from_value      TEXT,
  to_value        TEXT,
  author          TEXT,
  seq             INTEGER NOT NULL,          -- order within (key, at)
  PRIMARY KEY (run_id, key, at, seq)
) WITHOUT ROWID;

CREATE TABLE ticket_link (
  run_id          TEXT NOT NULL,
  key             TEXT NOT NULL,             -- the waiting ticket
  link_type       TEXT NOT NULL CHECK (link_type IN ('blocked_by','relates','duplicates','parent','other')),
  other_key       TEXT NOT NULL,
  added_at        TEXT,
  removed_at      TEXT,                      -- kept: links are deleted once work is done
  PRIMARY KEY (run_id, key, link_type, other_key)
) WITHOUT ROWID;

CREATE TABLE commit_ticket (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  sha             TEXT NOT NULL,
  key             TEXT NOT NULL,             -- must exist in ticket
  source          TEXT NOT NULL CHECK (source IN ('subject','body')),
  PRIMARY KEY (run_id, repo, sha, key)
) WITHOUT ROWID;

CREATE TABLE bug_inducing (                  -- SZZ output, kept for scoring
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  fix_sha         TEXT NOT NULL,
  key             TEXT NOT NULL,
  path            TEXT NOT NULL,
  inducing_sha    TEXT NOT NULL,
  PRIMARY KEY (run_id, repo, fix_sha, path, inducing_sha)
) WITHOUT ROWID;

CREATE TABLE file_bug_fixes (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,
  bug_fixes       INTEGER NOT NULL,          -- Bug+Fixed fix commits in the window
  fix_keys        TEXT NOT NULL,             -- JSON, sorted. Fragility always ships with ticket IDs
  window_start    TEXT NOT NULL,
  window_end      TEXT NOT NULL,             -- = snapshot_at
  PRIMARY KEY (run_id, repo, path)
) WITHOUT ROWID;

-- ───────────────────────── Stage 1 · Rachna · call sites ─────────────────────────

CREATE TABLE candidate (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,
  line            INTEGER NOT NULL,          -- 1-based
  col             INTEGER NOT NULL,          -- 0-based, start of the identifier
  symbol          TEXT NOT NULL,             -- tracked symbol from meridian.toml, old name
  role            TEXT NOT NULL CHECK (role IN ('import','call','alias','definition','attribute','reference')),
  scope           TEXT NOT NULL,             -- dotted enclosing scope, '<module>' at top level
  text            TEXT NOT NULL,             -- the source line, stripped
  junk            TEXT NOT NULL CHECK (junk IN ('none','test','vendored','migration','example','docstring','generated')),
  file_kind       TEXT NOT NULL CHECK (file_kind IN ('py','embedded')), -- embedded = Python outside .py
  in_except       INTEGER NOT NULL DEFAULT 0 CHECK (in_except IN (0,1)), -- inside an except/finally block: stall reason #5, no LLM diff
  PRIMARY KEY (run_id, repo, path, line, col)
) WITHOUT ROWID;

CREATE TABLE module_import (                -- every import statement in every scanned file, tracked or not
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,             -- the importing file
  line            INTEGER NOT NULL,
  module          TEXT NOT NULL,             -- absolute dotted module, relative imports resolved against path
  name            TEXT NOT NULL DEFAULT '',  -- the imported name for 'from m import n', '' for 'import m'
  is_test         INTEGER NOT NULL CHECK (is_test IN (0,1)), -- importer is a test file ([scanner] junk test)
  PRIMARY KEY (run_id, repo, path, line, module, name)
) WITHOUT ROWID;
-- Stall reason #7 (no test imports the module) and the push check's scope (changed files + their importers).

CREATE TABLE deprecation (                  -- functions and classes that announce they are going away
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,
  line            INTEGER NOT NULL,          -- the warn() call or the decorator
  scope           TEXT NOT NULL,             -- the deprecated def/class, dotted
  qualname        TEXT NOT NULL,             -- what a call to it resolves to: module (no __init__) + scope (no .__init__), as resolution.resolved_to
  marker          TEXT NOT NULL,             -- the matching [scanner] deprecation_markers text, e.g. RemovedInDjango40Warning
  PRIMARY KEY (run_id, repo, path, line)
) WITHOUT ROWID;
-- Push check P2 (new call to a deprecation-marked symbol) and the legacy radar.

CREATE TABLE parse_failure (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,
  error           TEXT NOT NULL,
  PRIMARY KEY (run_id, repo, path)
) WITHOUT ROWID;

-- ───────────────────────── Stage 1 · Rochan · resolver ─────────────────────────

CREATE TABLE resolution (                    -- one row per candidate, same key
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,
  line            INTEGER NOT NULL,
  col             INTEGER NOT NULL,
  symbol          TEXT NOT NULL,
  detection       TEXT NOT NULL CHECK (detection IN ('ast_resolved','ast_unresolved','string_url','config','dropped')),
  via             TEXT NOT NULL CHECK (via IN ('direct','import_alias','assign_alias','reexport','none')),
  resolved_to     TEXT,                      -- fully qualified target, NULL when unresolved
  resolver        TEXT NOT NULL,             -- 'jedi==<pinned version>'
  receiver_from   TEXT,                      -- for recv.name(...): F as 'repo:path:scope' when recv = F(...) in the same scope. blocked_by reads it
  PRIMARY KEY (run_id, repo, path, line, col)
) WITHOUT ROWID;
-- Alias calls (the 542 _() calls) arrive here as extra rows the scanner never saw: role comes from Rochan.

CREATE TABLE call_edge (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  caller_path     TEXT NOT NULL,
  caller_scope    TEXT NOT NULL,
  line            INTEGER NOT NULL,          -- where the call happens
  col             INTEGER NOT NULL,
  callee_path     TEXT NOT NULL,             -- may be in another repo: see callee_repo
  callee_repo     TEXT NOT NULL,
  callee_scope    TEXT NOT NULL,
  introduced_sha  TEXT NOT NULL,             -- 40 hex: the commit that last changed the caller's line at the run's SHA (blame --first-parent -w)
  PRIMARY KEY (run_id, repo, caller_path, line, col, callee_repo, callee_path, callee_scope)
) WITHOUT ROWID;
-- Resolved calls only. Imports are never edges.

-- ───────────────────────── Stage 1 · Chaitali · owners ─────────────────────────

CREATE TABLE person (
  run_id          TEXT NOT NULL,
  person_id       TEXT NOT NULL,             -- h(smallest email in the group): new id if an added email sorts first or groups merge
  name            TEXT NOT NULL,             -- most common name in the group
  is_bot          INTEGER NOT NULL CHECK (is_bot IN (0,1)),
  PRIMARY KEY (run_id, person_id)
) WITHOUT ROWID;

CREATE TABLE person_alias (
  run_id          TEXT NOT NULL,
  email           TEXT NOT NULL,             -- lowercased
  person_id       TEXT NOT NULL,
  name            TEXT NOT NULL,
  generic         INTEGER NOT NULL CHECK (generic IN (0,1)),  -- never merged on
  source          TEXT NOT NULL CHECK (source IN ('mailmap','name_email','manual','single')),
  PRIMARY KEY (run_id, email)
) WITHOUT ROWID;

CREATE TABLE team_map (
  run_id          TEXT NOT NULL,
  dataset         TEXT NOT NULL,             -- key under [datasets] in meridian.toml
  subject_kind    TEXT NOT NULL CHECK (subject_kind IN ('person','path_prefix','repo','component','label')),
  subject         TEXT NOT NULL,             -- person_id, path prefix, repo, component or label
  team            TEXT NOT NULL,
  rule            TEXT NOT NULL,             -- which [teams] rule produced it
  PRIMARY KEY (run_id, dataset, subject_kind, subject)
) WITHOUT ROWID;

CREATE TABLE file_owner (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,
  owner_team      TEXT NOT NULL,             -- 'unowned' when share < min_share
  share           REAL NOT NULL,             -- owner team's share of commits in the window, 4 dp
  top_person_id   TEXT,
  n_commits       INTEGER NOT NULL,
  last_commit_at  TEXT,
  basis           TEXT NOT NULL CHECK (basis IN ('author','path','repo','none')),
  PRIMARY KEY (run_id, repo, path)
) WITHOUT ROWID;

-- ───────────────────────── Stage 2 · Utkarsh · join ─────────────────────────

CREATE TABLE call_site (                     -- append-only: a row is written only on change
  call_site_id    TEXT NOT NULL,             -- h(repo, symbol, path, scope, normalized line, occurrence)
  run_id          TEXT NOT NULL,             -- run that wrote this version
  repo            TEXT NOT NULL,
  path            TEXT NOT NULL,
  line            INTEGER NOT NULL,
  col             INTEGER NOT NULL,
  scope           TEXT NOT NULL,
  symbol          TEXT NOT NULL,
  role            TEXT NOT NULL,
  detection       TEXT NOT NULL,
  junk            TEXT NOT NULL,
  in_except       INTEGER NOT NULL DEFAULT 0 CHECK (in_except IN (0,1)),
  owner_team      TEXT NOT NULL,
  bug_fixes       INTEGER NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('present','removed')),
  effective_date  TEXT NOT NULL,             -- first first-parent commit where this became true
  effective_sha   TEXT NOT NULL,             -- 40 hex: that commit. Present: blame of the line. Removed: the removing commit
  known_at        TEXT NOT NULL,             -- = run.snapshot_at of the writing run
  PRIMARY KEY (call_site_id, run_id)
) WITHOUT ROWID;

CREATE TABLE ticket_map (
  run_id          TEXT NOT NULL,
  key             TEXT NOT NULL,
  target_kind     TEXT NOT NULL CHECK (target_kind IN ('call_site','path','unmapped')),
  target          TEXT NOT NULL,             -- call_site_id, 'repo:path', or '' when unmapped
  method          TEXT NOT NULL CHECK (method IN ('commit_key','path','stack_trace','symbol','none')),
  PRIMARY KEY (run_id, key, target_kind, target, method)
) WITHOUT ROWID;

CREATE TABLE coverage (
  run_id          TEXT NOT NULL,
  metric          TEXT NOT NULL,             -- e.g. call_sites_resolved, tickets_mapped, files_owned, parse_failures
  numerator       INTEGER NOT NULL,
  denominator     INTEGER NOT NULL,
  note            TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (run_id, metric)
) WITHOUT ROWID;

-- ───────────────────────── Stage 3 · Aniket · graph ─────────────────────────

CREATE TABLE blocked_by (
  run_id          TEXT NOT NULL,
  call_site_id    TEXT NOT NULL,             -- A: the waiting site
  blocker_id      TEXT NOT NULL,             -- B: call_site_id inside the shared function
  via_function    TEXT NOT NULL,             -- F, 'repo:path:scope'
  depth           INTEGER NOT NULL CHECK (depth >= 1),  -- <= [graph] blocked_by_max_depth: a setting, not a schema rule
  status          TEXT NOT NULL CHECK (status IN ('resolved','unknown')),
  PRIMARY KEY (run_id, call_site_id, blocker_id)
) WITHOUT ROWID;

CREATE TABLE blast_radius (
  run_id          TEXT NOT NULL,
  repo            TEXT NOT NULL,
  symbol          TEXT NOT NULL,
  direct_sites    INTEGER NOT NULL,          -- call sites (burndown)
  invocations     INTEGER NOT NULL,          -- every resolved invocation reached, depth <= 3
  files           INTEGER NOT NULL,
  by_depth        TEXT NOT NULL,             -- JSON {"1": n, "2": n, "3": n}
  PRIMARY KEY (run_id, repo, symbol)
) WITHOUT ROWID;

CREATE TABLE chain (
  run_id          TEXT NOT NULL,
  from_key        TEXT NOT NULL,
  to_key          TEXT NOT NULL,             -- '' for untracked
  type            TEXT NOT NULL CHECK (type IN ('depends_on','collides','duplicates','untracked')),
  evidence        TEXT NOT NULL,             -- JSON, sorted list of call_site_id / blocked_by keys
  in_cycle        INTEGER NOT NULL CHECK (in_cycle IN (0,1)),
  PRIMARY KEY (run_id, from_key, to_key, type)
) WITHOUT ROWID;
