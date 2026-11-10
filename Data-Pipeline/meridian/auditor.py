"""Auditor · step P · 04-architecture §9: versioned policies that choose where to look, and Elo over them.

    python -m meridian.auditor --run-id <run_id> --config meridian.toml    # reads out/<run_id>/meridian.sqlite
    python -m meridian.auditor --run-id R2 --db /tmp/fixture.sqlite --at 2026-10-01T00:00:00Z
    python -m meridian.auditor --run-id <run_id> --write     # also out/<run_id>/policy.jsonl and match.jsonl

Two jobs (packets/rochan-tracking, step P). The stall job ranks why each stalled team stalled: policy v0 and a first
challenger, ε-greedy, the verdict rules, live and replay matches, Elo and the promotion guard. The explore job
nominates unfiled legacy code: policy v0. It reads a block. With --write it also writes the policy and match rows the
next writer run loads.

Facts come from KNOWLEDGE-BASE.md's queries, the text tests/test_kb.py proves on its fixture. `owner_teams` gives each
team's sites left and the days since its last change. A team is stalled when it has sites left and nothing changed for
[rules] stall_days (04 §5), blocked or not. Then `stall_upstream` ... `stall_competing_load` say which of the eight
reasons fire, and their rows are the evidence. Rules decide the facts and a policy only orders them, so every policy
ranks the same facts, and a match compares orderings, never data.

Policy v0 (04 §5): the reasons that fire, by number. Evidence never reorders them, and there are no probabilities.
Policy v1, the first challenger: the same reasons, the one with the most evidence rows first. Every policy ranks every
stall in shadow. ε-greedy (04 §9) picks whose ranking the team sees: the champion with 1 − ε, else a challenger at
random, seeded by (run_id, task_id). ε is 0.2 until the job has 30 live matches, then 0.1.

Elo (04 §9): every policy starts at 1500. The match log is replayed oldest first (decided_at, then match_id), so the
ratings are a function of the log alone: recomputed every run, never stored. A policy's K is 32 for its first 30
matches, then 16, and a replay match moves half as far.

As of a moment (04 §9: "rows with known_at ≤ stall time only", 05-test's known_at bar): point() aims the queries at
one moment through temp views over the block's tables (SQLite reads temp first), so nothing known later is read. A call
site is its newest version known by then (KNOWLEDGE-BASE.md's site_now). Tickets are those created by then, with the
status and resolution they had then, rebuilt from ticket_event, and only their maps and the events up to then. Commits
are those authored by then, with their files. A table a run derives from the whole repo (DERIVED) is known from that
run's snapshot on: the rows of the newest run at or before the moment. A block keeps only its own run's (join carries
call_site's history over, nothing else), so at an earlier run they're unknown, and so is every stall reason or verdict
rule that reads one (NEEDS, RULE_NEEDS). It doesn't fire, and the output says so: an empty module_import would make
7 Test gap fire everywhere. Kept as pulled: a ticket's summary, description, assignee and components.

Verdicts (04 §9): a stall open at the last run that isn't now has ended. If the team changed its code (it resumed),
what happened just before says why it stalled. Each rule names one reason:
    block_removed    1 Upstream block    a site blocking one of theirs was removed at most [auditor] block_days before
    ticket_created   2 Never notified    a ticket on one of their sites was created at most ticket_days before
    ticket_reopened  3 Ticket mismatch   a ticket on one of their sites went from done to open during the stall
    new_author       4 Orphaned owner    the change's author isn't the top author of the file it touched
Tickets reach sites the way "Never notified" does: ticket_map rows of kind call_site. If one reason fires, every pair
of policies plays a live match on it: the one that ranked it higher wins, the same place is a draw. If none fires, or
two fire, a person picks, and there's no match until then: a tie broken by number would favor v0. So does a rule
that can't be checked at the verdict. Live matches are only between policies that existed when the stall was served,
the last run's snapshot. A task is decided live once. A verdict on a reason unknown when the stall was served makes
no match: no policy could have ranked it.

Replay (04 §9): every pair of consecutive runs in the block, oldest first, read the same way. The stalls open at one
run and not at the next get verdicts as of the next, and every pair of policies in the code plays a replay match on
each, whenever the policies were written: that's how a new policy is tried on the past. A pair plays a stall once,
so not in replay after a live match or an earlier replay.

Explore (04 §9): policies nominate unfiled legacy code for the staff engineer's legacy radar (02-product). v0 reads
KNOWLEDGE-BASE.md's radar_deprecated_with_callers, one repo at a time: deprecation-marked functions with callers, the
most called first. Unfiled: no present call site resolves to it, so it isn't a symbol Meridian tracks already. The
fan-in and twin signals wait for gaps G3 and G4. At most [auditor] explore_budget nominations per repo and run. With a
challenger, the radar shows the champion's and a challenger's lists team-draft interleaved, and the side whose picks
get more accepts wins; no reply in [auditor] reply_days is no match. Nothing records radar replies yet (no users), so
there are no explore matches, and the output says so.

Promotion (04 §9): a challenger replaces the champion when it has at least 30 live matches, at least 50 Elo over the
champion and a winning live record against v0. A policy an LLM wrote also needs a person's approval. Replay matches
never count toward the 30. Like Elo, the champion is replayed from the log: v0 to start, the guard checked after each
batch of matches decided at the same time.

Settings: meridian.toml [auditor] when the section exists, else 04 §9's numbers.
"""
import argparse
import itertools
import random
import sqlite3
from collections import Counter, namedtuple
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from meridian.common import CONFIG, days_before, h, handoff, load_config, log, write_jsonl
from meridian.pushcheck import kb_queries

# 04 §5's stall reasons by number, and the KNOWLEDGE-BASE.md query behind each. Each query's first column is the team
REASONS = {1: ("Upstream block", "stall_upstream"), 2: ("Never notified", "stall_never_notified"),
           3: ("Ticket mismatch", "stall_ticket_mismatch"), 4: ("Orphaned owner", "stall_orphaned_owner"),
           5: ("Semantics unclear", "stall_semantics_unclear"), 6: ("Fragility freeze", "stall_fragility_freeze"),
           7: ("Test gap", "stall_test_gap"), 8: ("Competing load", "stall_competing_load")}

# The tables a run derives from the whole repo: known from that run's snapshot on (see point()). Then what reads them,
# besides call sites, tickets and commits: the stall reasons by number, and the verdict rules
DERIVED = ("blocked_by", "file_owner", "person_alias", "team_map", "module_import", "resolution", "deprecation",
           "call_edge")
NEEDS = {1: ("blocked_by",), 4: ("file_owner", "person_alias"), 7: ("module_import",), 8: ("person_alias", "team_map")}
RULE_NEEDS = {"block_removed": ("blocked_by",), "new_author": ("file_owner", "person_alias")}

# 04 §9's numbers. meridian.toml [auditor] can set any of them
DEFAULTS = {"elo_start": 1500, "k": 32, "k_after": 16, "k_after_matches": 30, "replay_k": 0.5,
            "eps": 0.2, "eps_after": 0.1, "eps_after_matches": 30, "promote_matches": 30, "promote_elo": 50,
            "block_days": 7, "ticket_days": 14, "explore_budget": 200, "reply_days": 14}
TS = "%Y-%m-%dT%H:%M:%SZ"
ELO = ("elo_start", "k", "k_after", "k_after_matches", "replay_k")
SCORE = {"a": 1.0, "b": 0.0, "draw": 0.5}  # policy_a's score. policy_b's is 1 minus it
MODES = ("live", "replay")
MATCH_COLUMNS = ("match_id", "task_id", "policy_a", "policy_b", "result", "mode", "verdict_source", "decided_by",
                 "decided_at")

# One stalled team in one (repo, symbol), as of one moment. facts: {reason number: evidence rows}, only the reasons
# that fire. since is the team's last change, so task_id stays the same while the stall lasts, and a change starts a
# new stall, with a new task
Stall = namedtuple("Stall", "task_id repo symbol team since days left facts")

# A stall open at one run and not at the next. resumed: the team's first change since then (None: it ended with no
# change by the team, say its sites moved to another owner). fired: {reason: (rule, evidence)}. reason and rule: the
# verdict, when exactly one reason fired and every rule could be checked. matches: the match rows it made. unknown: the
# reasons unknown when it was served, so no policy ranked them. unchecked: the rules that couldn't be checked
Outcome = namedtuple("Outcome", "stall resumed fired reason rule matches unknown unchecked", defaults=((), ()))

# Named parameters: the stall's repo, symbol, team and since; t2, when it resumed; at, the run's snapshot. The tables
# are the as-of views, aimed at `at` (verdict()), and call_site's versions are read up to `at`
RESUMED = """SELECT min(effective_date) FROM call_site
WHERE repo = :repo AND symbol = :symbol AND owner_team = :team AND known_at <= :at
  AND julianday(effective_date) > julianday(:since)"""
VERDICTS = (
    (1, "block_removed", """SELECT DISTINCT k.call_site_id, k.effective_date FROM blocked_by b
JOIN call_site a ON a.call_site_id = b.call_site_id JOIN call_site k ON k.call_site_id = b.blocker_id
WHERE a.repo = :repo AND a.symbol = :symbol AND a.owner_team = :team AND a.known_at <= :at
  AND k.status = 'removed' AND k.known_at <= :at
  AND julianday(:t2) - julianday(k.effective_date) BETWEEN 0 AND :block_days"""),
    (2, "ticket_created", """SELECT DISTINCT t.key, t.created_at FROM ticket t
JOIN ticket_map m ON m.key = t.key AND m.target_kind = 'call_site' JOIN call_site s ON s.call_site_id = m.target
WHERE s.repo = :repo AND s.symbol = :symbol AND s.owner_team = :team AND s.known_at <= :at
  AND julianday(:t2) - julianday(t.created_at) BETWEEN 0 AND :ticket_days"""),
    (3, "ticket_reopened", """SELECT DISTINCT e.key, e.at FROM ticket_event e
JOIN ticket_map m ON m.key = e.key AND m.target_kind = 'call_site' JOIN call_site s ON s.call_site_id = m.target
WHERE s.repo = :repo AND s.symbol = :symbol AND s.owner_team = :team AND s.known_at <= :at AND e.field = 'status'
  AND lower(coalesce(e.from_value, '')) IN ('done', 'closed', 'resolved')
  AND lower(coalesce(e.to_value, '')) NOT IN ('done', 'closed', 'resolved')
  AND julianday(e.at) > julianday(:since) AND julianday(e.at) <= julianday(:t2)"""),
    (4, "new_author", """SELECT DISTINCT s.path, p.person_id FROM call_site s
JOIN git_commit g ON g.repo = s.repo AND g.sha = s.effective_sha JOIN person_alias p ON p.email = g.author_email
JOIN file_owner f ON f.repo = s.repo AND f.path = s.path
WHERE s.repo = :repo AND s.symbol = :symbol AND s.owner_team = :team AND s.known_at <= :at AND s.effective_date = :t2
  AND f.top_person_id IS NOT NULL AND p.person_id != f.top_person_id"""))


def stall_v0(stall):
    """04 §5: the reasons that fire, by number. Evidence never reorders them, and there are no probabilities."""
    return sorted(stall.facts)


def stall_v1(stall):
    """The first challenger: the reasons that fire, the one with the most evidence rows first, then by number. Its bet
    is that the reason with more evidence (more files, modules or sites) is likelier the cause."""
    return sorted(stall.facts, key=lambda n: (-len(stall.facts[n]), n))


# One legacy-radar candidate as of one moment: a deprecation-marked function, and its calls in call_edge
Nomination = namedtuple("Nomination", "repo path scope qualname marker callers")


def explore_v0(found):
    """04 §9's first explore signal: deprecation-marked functions with callers, the most called first."""
    return sorted(found, key=lambda x: (-x.callers, x.repo, x.path, x.scope))


@dataclass(frozen=True)
class Policy:
    policy_id: str    # '<job>-v<version>', the policy table's key
    job: str          # 'stall' or 'explore'
    version: int
    author: str       # a person's initials, or 'llm:<model>': never promoted without a person (04 §9)
    rank: Callable    # stall job: a Stall -> its reason numbers, most likely first. explore: Nominations, best first
    created_at: str   # when it was written. A block from before then has no row for it, and it has no matches
    approved_by: str = ""  # a person's initials: what an 'llm:' policy needs before it can be promoted


POLICIES = {p.policy_id: p for p in [Policy("stall-v0", "stall", 0, "rh", stall_v0, "2026-11-07T00:00:00Z"),
                                     Policy("stall-v1", "stall", 1, "rh", stall_v1, "2026-11-07T00:00:00Z"),
                                     Policy("explore-v0", "explore", 0, "rh", explore_v0, "2026-11-09T00:00:00Z")]}


def point(db, at, repo=None, symbol=None, run_id=None, q=None):
    """Aim KNOWLEDGE-BASE.md's queries at one moment: the one-row asof table they read (repo or symbol None means
    all), the run each derived table is known from then (known_run), and the setup and as-of views over them, made
    once per connection. Temp objects only: a read-only block is fine."""
    q = q or kb_queries()
    db.execute("CREATE TEMP TABLE IF NOT EXISTS asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT)")
    db.execute("DELETE FROM asof")
    db.execute("INSERT INTO asof VALUES (?, ?, ?, ?)", (run_id, at, repo, symbol))
    db.execute("CREATE TEMP TABLE IF NOT EXISTS known_run (tbl TEXT PRIMARY KEY, run_id TEXT NOT NULL)")
    db.execute("DELETE FROM known_run")
    db.executemany("INSERT INTO known_run VALUES (?, ?)", sorted(known_runs(db, at).items()))
    if not db.execute("SELECT 1 FROM sqlite_temp_master WHERE type = 'view' AND name = 'site_now'").fetchone():
        db.executescript(q["setup"] + AS_OF)
    return db


def run_snapshots(db):
    """{run_id: snapshot_at} for every run the block has rows of: the run table, and call_site's known_at for one it
    doesn't list."""
    runs = dict(db.execute("SELECT run_id, snapshot_at FROM main.run"))
    for run_id, at in db.execute("SELECT run_id, max(known_at) FROM main.call_site GROUP BY run_id"):
        runs.setdefault(run_id, at)
    return runs


def known_runs(db, at):
    """{table: run_id} for each DERIVED table a run at or before `at` wrote rows to: the newest such run."""
    runs = sorted(((snap, r) for r, snap in run_snapshots(db).items() if snap <= at), reverse=True)
    out = {}
    for t in DERIVED:
        for _, run_id in runs:
            if db.execute(f"SELECT 1 FROM main.{t} WHERE run_id = ? LIMIT 1", (run_id,)).fetchone():
                out[t] = run_id
                break
    return out


def unknown(db, needs):
    """The keys of `needs` (reason numbers or rule names) that read a table no run had written where asof points."""
    known = {t for (t,) in db.execute("SELECT tbl FROM known_run")}
    return sorted(k for k, tables in needs.items() if not set(tables) <= known)


def unknown_at(db, at, needs=NEEDS, q=None):
    """(the keys of `needs` unknown at `at`, the tables behind them). Points the views at `at`, every repo."""
    point(db, at, None, None, None, q)
    keys = unknown(db, needs)
    known = {t for (t,) in db.execute("SELECT tbl FROM known_run")}
    return keys, sorted({t for k in keys for t in needs[k]} - known)


def _as_then(field, pulled):
    """SQL: a ticket's `field` at asof.at, from ticket_event. The newest change at or before then, else what the first
    later change changed it from (NULL when that isn't on record), else `pulled`: a field with no change on record."""
    ev = f"FROM main.ticket_event e WHERE e.run_id = t.run_id AND e.key = t.key AND e.field = '{field}'"
    return (f"CASE WHEN EXISTS (SELECT 1 {ev} AND e.at <= a.at)\n"
            f"       THEN (SELECT e.to_value {ev} AND e.at <= a.at ORDER BY e.at DESC, e.seq DESC LIMIT 1)\n"
            f"     WHEN EXISTS (SELECT 1 {ev})\n"
            f"       THEN (SELECT e.from_value {ev} AND e.at > a.at ORDER BY e.at, e.seq LIMIT 1)\n"
            f"     ELSE {pulled} END")


# Temp views shadow the block's tables (SQLite reads temp first), so the queries never read a row known after asof.at:
# at an earlier moment than the snapshot, a later ticket would say "never notified" was false, a later commit that its
# owner wasn't away, a later import that a test covered the module. A ticket's status, resolution and resolved_at are
# as of the pull, so they're rebuilt; without a status change on record, a ticket resolved after then wasn't done then
# (NULL: its status then isn't known). ticket_event's key is (run_id, key, at, seq), so each lookup uses it.
# ticket_map drops the maps of later tickets as one list, built once: per map row (NOT EXISTS on key) it scanned all of
# ticket, since ticket's key is (run_id, key). Over 7 minutes on a real block's 246,428 maps, 0.3 s this way. So does
# commit_file. Each DERIVED table keeps the rows of the run known_run names, and none when it names none. As IN, not
# a join: on the real block a join kept SQLite from indexing person_alias, and stall_competing_load took 4.9 s a
# (repo, symbol) pair instead of 3.2 s, the same as before these views
AS_OF = f"""
CREATE TEMP VIEW ticket AS
SELECT t.run_id, t.key, t.tracker, t.type, t.effective_type, t.parent_key,
       {_as_then("status", "CASE WHEN t.resolved_at > a.at THEN NULL ELSE t.status END")} AS status,
       {_as_then("resolution", "CASE WHEN t.resolved_at > a.at THEN NULL ELSE t.resolution END")} AS resolution,
       t.created_at, CASE WHEN t.resolved_at <= a.at THEN t.resolved_at END AS resolved_at, t.components, t.assignee,
       t.reporter, t.bulk_closed, t.desc_empty, t.summary, t.description
FROM main.ticket t, asof a WHERE t.created_at <= a.at;
CREATE TEMP VIEW ticket_map AS SELECT m.* FROM main.ticket_map m
WHERE m.key NOT IN (SELECT t.key FROM main.ticket t, asof a WHERE t.created_at > a.at);
CREATE TEMP VIEW ticket_event AS SELECT e.* FROM main.ticket_event e, asof WHERE e.at <= asof.at;
CREATE TEMP VIEW git_commit AS SELECT g.* FROM main.git_commit g, asof WHERE g.authored_at <= asof.at;
CREATE TEMP VIEW commit_file AS SELECT f.* FROM main.commit_file f
WHERE f.sha NOT IN (SELECT g.sha FROM main.git_commit g, asof a WHERE g.authored_at > a.at);
""" + "".join(f"CREATE TEMP VIEW {t} AS SELECT x.* FROM main.{t} x "
              f"WHERE x.run_id IN (SELECT k.run_id FROM known_run k WHERE k.tbl = '{t}');\n" for t in DERIVED)


def _key(row):
    """Evidence rows sort the same everywhere: numbers by value, the rest as text."""
    return tuple((0, v, "") if isinstance(v, (int, float)) else (1, 0, "" if v is None else str(v)) for v in row)


def stalls(db, cfg, q=None):
    """The stalled teams where asof points, by team, each with the reasons that fire and their evidence rows. A
    reason that reads a table unknown then (NEEDS) is left out: unknown isn't true or false."""
    q = q or kb_queries()
    _, repo, symbol = db.execute("SELECT at, repo, symbol FROM asof").fetchone()
    stalled = {team: (left, days) for team, left, _, days, _ in db.execute(q["owner_teams"])
               if left and days is not None and days >= cfg["rules"]["stall_days"]}
    since = dict(db.execute("SELECT owner_team, max(effective_date) FROM site_now GROUP BY owner_team"))
    facts = {team: {} for team in stalled}
    hidden = set(unknown(db, NEEDS))
    for n, (_, name) in REASONS.items():
        if n in hidden:
            continue
        for team, *evidence in db.execute(q[name]):
            if team in facts:
                facts[team].setdefault(n, []).append(tuple(evidence))
    return [Stall(h("stall", repo or "", symbol or "", team, since[team]), repo, symbol, team, since[team], days, left,
                  {n: sorted(rows, key=_key) for n, rows in sorted(facts[team].items())})
            for team, (left, days) in sorted(stalled.items())]


def block_stalls(db, cfg, at, run_id=None, q=None):
    """Every stall in a block as of `at`: each (repo, symbol) it tracks on its own, as the views do."""
    q = q or kb_queries()
    out = []
    for repo, symbol in db.execute("SELECT DISTINCT repo, symbol FROM call_site ORDER BY repo, symbol").fetchall():
        point(db, at, repo, symbol, run_id, q)
        out += stalls(db, cfg, q)
    return out


def stalls_at(db, cfg, at, run_id=None, q=None, cache=None):
    """block_stalls(), read once per moment when `cache` (a dict) is given: live and replay share moments."""
    if cache is None:
        return block_stalls(db, cfg, at, run_id, q)
    if at not in cache:
        cache[at] = block_stalls(db, cfg, at, run_id, q)
    return cache[at]


def snapshots(db, at):
    """Every run's snapshot in the block at or before `at`, oldest first: the moments replay walks."""
    return sorted({snap for snap in run_snapshots(db).values() if snap <= at})


def previous_snapshot(db, at):
    """The last run before `at` in this block: call_site keeps every run's rows, and each known_at is a run's
    snapshot. None when the block has one run."""
    return db.execute("SELECT max(known_at) FROM call_site WHERE known_at < ?", (at,)).fetchone()[0]


def _args(stall, at, s, t2=None):
    return {"repo": stall.repo, "symbol": stall.symbol, "team": stall.team, "since": stall.since, "t2": t2, "at": at,
            "block_days": s["block_days"], "ticket_days": s["ticket_days"]}


def resumed(db, stall, at):
    """When the stalled team first changed its code again, from what the block knows by `at`. None: not yet."""
    return db.execute(RESUMED, _args(stall, at, DEFAULTS)).fetchone()[0]


def verdict(db, stall, t2, at, s, q=None):
    """The verdict rules that fire for a stall that resumed at t2, from what the block knows by `at`: {reason number:
    (rule, evidence rows)}. Points the views at `at`, where unknown(db, RULE_NEEDS) then says which couldn't fire."""
    point(db, at, stall.repo, stall.symbol, None, q)
    fired = {}
    for n, rule, sql in VERDICTS:
        rows = db.execute(sql, _args(stall, at, s, t2)).fetchall()
        if rows:
            fired[n] = (rule, sorted(rows, key=_key))
    return fired


def shadow_matches(stall, reason, policies, mode, source, decided_by, decided_at):
    """04 §9: every pair of policies on one decided stall. The one that ranked `reason` higher wins. The same place,
    or neither ranking it, is a draw. Pairs in policy_id order; match_id as schema.sql defines it."""
    place = {}
    for p in policies:
        ranked = list(p.rank(stall))
        place[p.policy_id] = ranked.index(reason) if reason in ranked else len(REASONS)
    return [{"match_id": h(stall.task_id, mode, a, b, decided_at), "task_id": stall.task_id, "policy_a": a,
             "policy_b": b, "result": "a" if place[a] < place[b] else "b" if place[b] < place[a] else "draw",
             "mode": mode, "verdict_source": source, "decided_by": decided_by, "decided_at": decided_at}
            for a, b in itertools.combinations(sorted(place), 2)]


def ended(db, cfg, s, run_id, before, after, players, mode, done=(), played=(), q=None, cache=None):
    """The stalls open at `before` and not at `after` (they ended), each with its verdict as of `after`. When it's one
    reason, known when the stall was served, every pair of `players` plays a `mode` match on it. done: tasks decided
    already, left out. played: (task_id, policy_a, policy_b) decided already, no match again."""
    q = q or kb_queries()
    now = {x.task_id for x in stalls_at(db, cfg, after, run_id, q, cache)}
    then = stalls_at(db, cfg, before, run_id, q, cache)
    hidden = tuple(unknown_at(db, before, NEEDS, q)[0])
    out = []
    for x in then:
        if x.task_id in now or x.task_id in done:
            continue
        t2 = resumed(db, x, after)
        fired, unchecked = {}, ()
        if t2:
            fired = verdict(db, x, t2, after, s, q)
            unchecked = tuple(unknown(db, RULE_NEEDS))
        reason, rule, rows = None, None, []
        if len(fired) == 1 and not unchecked:
            [(reason, (rule, _))] = fired.items()
            if reason not in hidden and len(players) > 1:
                rows = [m for m in shadow_matches(x, reason, players, mode, "rule", rule, after)
                        if (m["task_id"], m["policy_a"], m["policy_b"]) not in played]
        out.append(Outcome(x, t2, fired, reason, rule, rows, hidden, unchecked))
    return out


Job = namedtuple("Job", "league eps live open served outcomes matches")


def stall_job(db, cfg, s, run_id, at, prev_at, log, policies=None, q=None, cache=None):
    """One run of the stall job (04 §9), on a block as of `at`. `log`: the match rows decided so far.

    open: the stalls now, each served the champion's ranking or, ε of the time, a challenger's (served).
    outcomes: the stalls open at `prev_at` that aren't now, with their verdicts. matches: the new live match rows,
    between the policies that existed at `prev_at`, when the stall was served.
    """
    q = q or kb_queries()
    policies = {p: x for p, x in (POLICIES if policies is None else policies).items() if x.job == "stall"}
    league = standings(log, s, policies)
    live = sum(league.live.values()) // 2
    eps = epsilon(live, s)
    challengers = [p for p in sorted(policies) if p != league.champion]
    found = stalls_at(db, cfg, at, run_id, q, cache)
    served = {x.task_id: serve(run_id, x.task_id, league.champion, challengers, eps) for x in found}
    outcomes, new = [], []
    if prev_at:
        players = [policies[p] for p in sorted(policies) if policies[p].created_at <= prev_at]
        outcomes = ended(db, cfg, s, run_id, prev_at, at, players, "live", {m["task_id"] for m in log}, (), q, cache)
        new = [m for o in outcomes for m in o.matches]
    return Job(league, eps, live, found, served, outcomes, new)


# pairs: (one run's snapshot, the next's), oldest first. outcomes: {pair: [Outcome]}. matches: the new replay rows
Replay = namedtuple("Replay", "pairs outcomes matches")


def replay_job(db, cfg, s, run_id, at, log, policies=None, q=None, cache=None):
    """04 §9's replay over the block up to `at`: past stalls, rows known by then only, every policy in the code.

    For each pair of consecutive runs, the stalls open at the first and not at the second, with verdicts as of the
    second, and a replay match between every pair of policies on each one that pair hasn't played (`log`, live or
    replay). A replay match moves Elo half as far and never counts toward promotion (League).
    """
    q = q or kb_queries()
    players = [x for _, x in sorted((POLICIES if policies is None else policies).items()) if x.job == "stall"]
    played = {(m["task_id"], m["policy_a"], m["policy_b"]) for m in log}
    runs = snapshots(db, at)
    pairs = list(zip(runs, runs[1:]))
    outcomes, new = {}, []
    for before, after in pairs:
        outcomes[before, after] = ended(db, cfg, s, run_id, before, after, players, "replay", (), played, q, cache)
        rows = [m for o in outcomes[before, after] for m in o.matches]
        played |= {(m["task_id"], m["policy_a"], m["policy_b"]) for m in rows}
        new += rows
    return Replay(pairs, outcomes, new)


# What Meridian tracks already: what its present call sites resolve to, the name deprecation.qualname gives a function
TRACKED = """SELECT DISTINCT r.resolved_to FROM site_now s JOIN resolution r
  ON r.repo = s.repo AND r.path = s.path AND r.line = s.line AND r.col = s.col
WHERE s.status = 'present' AND r.resolved_to IS NOT NULL"""


def nominations(db, at, run_id=None, q=None):
    """Unfiled legacy code as of `at`: KNOWLEDGE-BASE.md's radar_deprecated_with_callers, one repo at a time, less
    what a present call site resolves to. (nominations, filed): filed counts the radar's rows Meridian tracks."""
    q = q or kb_queries()
    point(db, at, None, None, run_id, q)
    tracked = {r for (r,) in db.execute(TRACKED)}
    found, filed = [], 0
    for (repo,) in db.execute("SELECT DISTINCT repo FROM deprecation ORDER BY repo").fetchall():
        point(db, at, repo, None, run_id, q)
        names = {(p, sc): qn for p, sc, qn in db.execute(
            "SELECT path, scope, qualname FROM deprecation WHERE repo = ?", (repo,))}
        for path, scope, marker, callers in db.execute(q["radar_deprecated_with_callers"]).fetchall():
            if names[path, scope] in tracked:
                filed += 1
            else:
                found.append(Nomination(repo, path, scope, names[path, scope], marker, callers))
    return found, filed


def team_draft(seed, lists, n):
    """Team-draft interleaving (Radlinski, Kurup and Joachims, 2008) of two rankings, {policy_id: items best first}:
    the side with fewer picks so far, or a seeded coin when they're even, adds its best item not shown yet. Returns
    [(item, the policy credited)], at most n: an item both rank is credited to the side that picked it."""
    rng = random.Random(int(seed, 16))
    (a, la), (b, lb) = sorted(lists.items())
    shown, picks, out = set(), {a: 0, b: 0}, []
    while len(out) < n:
        rest = {p: [x for x in items if x not in shown] for p, items in ((a, la), (b, lb))}
        if not (rest[a] or rest[b]):
            break
        first, second = (a, b) if picks[a] < picks[b] or (picks[a] == picks[b] and rng.random() < 0.5) else (b, a)
        p = first if rest[first] else second
        shown.add(rest[p][0])
        picks[p] += 1
        out.append((rest[p][0], p))
    return out


# One radar list. shown: the policies whose picks it holds. served: [(Nomination, policy_id credited)], best first
ExploreTask = namedtuple("ExploreTask", "task_id repo shown served")
Explore = namedtuple("Explore", "league found filed deprecated tasks")


def explore_job(db, cfg, s, run_id, at, log, policies=None, q=None):
    """One run of the explore job (04 §9) as of `at`: per repo, the champion's nominations, at most explore_budget,
    team-draft interleaved with a challenger's when there is one (picked at random, seeded by run and task)."""
    q = q or kb_queries()
    policies = {p: x for p, x in (POLICIES if policies is None else policies).items() if x.job == "explore"}
    league = standings(log, s, policies, "explore")
    found, filed = nominations(db, at, run_id, q)
    deprecated = db.execute("SELECT count(*) FROM (SELECT DISTINCT repo, path, scope FROM deprecation)").fetchone()[0]
    tasks, n = [], s["explore_budget"]
    for repo, rows in itertools.groupby(sorted(found, key=lambda x: x.repo), key=lambda x: x.repo):
        rows = list(rows)
        task_id = h("explore", repo, at)
        lists = {league.champion: list(policies[league.champion].rank(rows))[:n]}
        if rivals := sorted(p for p in policies if p != league.champion):
            rival = random.Random(int(h(run_id, task_id, "rival"), 16)).choice(rivals)
            lists[rival] = list(policies[rival].rank(rows))[:n]
            served = team_draft(h(run_id, task_id), lists, n)
        else:
            served = [(x, league.champion) for x in lists[league.champion]]
        tasks.append(ExploreTask(task_id, repo, sorted(lists), served))
    return Explore(league, found, filed, deprecated, tasks)


def explore_match(task, replies, offered_at, s):
    """04 §9's explore verdict on one interleaved radar list: the side whose picks got more accepts wins, the same
    number is a draw. replies: {(repo, path, scope): ('accept' or 'reject', at)}. Only replies to its items within
    [auditor] reply_days of the offer count; none is no match (None). Decided when that window closes."""
    if len(task.shown) < 2:
        return None
    end = days_before(offered_at, -s["reply_days"], TS)
    heard = {}
    for x, _ in task.served:
        answer, at = replies.get((x.repo, x.path, x.scope), (None, None))
        if answer and offered_at <= at <= end:
            heard[x.repo, x.path, x.scope] = answer
    if not heard:
        return None
    accepts = Counter(p for x, p in task.served if heard.get((x.repo, x.path, x.scope)) == "accept")
    a, b = task.shown
    return {"match_id": h(task.task_id, "live", a, b, end), "task_id": task.task_id, "policy_a": a, "policy_b": b,
            "result": "a" if accepts[a] > accepts[b] else "b" if accepts[b] > accepts[a] else "draw",
            "mode": "live", "verdict_source": "rule", "decided_by": "more_accepts", "decided_at": end}


def policy_log(db):
    """The block's policy rows as dicts. A block written before schema v1.3 has none."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'policy'").fetchone():
        return []
    cols = ("policy_id", "job", "version", "author", "created_at")
    return [dict(zip(cols, r)) for r in db.execute(f"SELECT {', '.join(cols)} FROM policy")]


def write_tracking(out, at, policies, before, log, new):
    """policy.jsonl and match.jsonl for the next writer run: every row so far that's known by `at` (the writer
    refuses a row from after its snapshot), sorted by key. `before`: the block's policy rows; the code's win. A match
    is kept only with both its policies. Returns (policy rows, match rows) written."""
    rows = {r["policy_id"]: r for r in before}
    rows.update({p.policy_id: {"policy_id": p.policy_id, "job": p.job, "version": p.version, "author": p.author,
                               "created_at": p.created_at} for p in policies.values()})
    kept = sorted((r for r in rows.values() if r["created_at"] <= at), key=lambda r: r["policy_id"])
    known = {r["policy_id"] for r in kept}
    matches = sorted({m["match_id"]: m for m in [*log, *new]
                      if m["decided_at"] <= at and {m["policy_a"], m["policy_b"]} <= known}.values(),
                     key=lambda m: m["match_id"])
    out.mkdir(parents=True, exist_ok=True)
    for name, table in (("policy", kept), ("match", matches)):
        write_jsonl(out / f"{name}.jsonl", table)  # '\n' on every OS: write_text gave '\r\n' on Windows
    return len(kept), len(matches)


def settings(cfg):
    """meridian.toml [auditor] over 04 §9's numbers. A key the auditor doesn't read stops the run."""
    given = cfg.get("auditor", {})
    if unknown := sorted(set(given) - set(DEFAULTS)):
        raise SystemExit(f"meridian.toml [auditor] has keys the auditor doesn't read: {', '.join(unknown)}")
    return {**DEFAULTS, **given}


def epsilon(live, s):
    """04 §9: `eps` until the job has `eps_after_matches` live matches, then `eps_after`."""
    return s["eps"] if live < s["eps_after_matches"] else s["eps_after"]


def serve(run_id, task_id, champion, challengers, eps):
    """04 §9's ε-greedy: whose ranking the team sees for one task. The champion with 1 − ε, else a challenger at
    random. Seeded by (run_id, task_id): a rerun serves the same, and each task draws on its own."""
    rng = random.Random(int(h(run_id, task_id), 16))
    if challengers and rng.random() < eps:
        return rng.choice(sorted(challengers))
    return champion


class League:
    """Elo and the promotion guard over one job's match log, one match at a time, oldest first (04 §9).

    rating, played: Elo and matches played, live and replay alike (what K counts). live: live matches only (what the
    guard counts). vs_v0: each policy's live (points, matches) against v0. champion: v0 until `promote` moves it.
    """

    def __init__(self, s, policies=None, job="stall"):
        self.s, self.policies, self.v0 = s, dict(policies or {}), f"{job}-v0"
        self.rating, self.played, self.live, self.vs_v0 = {}, {}, {}, {}
        self.champion, self.promotions = self.v0, []

    def add(self, m):
        a, b = m["policy_a"], m["policy_b"]
        if a == b or m["result"] not in SCORE or m["mode"] not in MODES:
            raise ValueError(f"match {m['match_id']} can't be scored: {a} v {b}, result {m['result']!r}, "
                             f"mode {m['mode']!r}")
        s, score = self.s, SCORE[m["result"]]
        ra, rb = self.rating.get(a, s["elo_start"]), self.rating.get(b, s["elo_start"])
        expect = 1 / (1 + 10 ** ((rb - ra) / 400))
        weight = s["replay_k"] if m["mode"] == "replay" else 1
        ka = (s["k"] if self.played.get(a, 0) < s["k_after_matches"] else s["k_after"]) * weight
        kb = (s["k"] if self.played.get(b, 0) < s["k_after_matches"] else s["k_after"]) * weight
        self.rating[a] = ra + ka * (score - expect)
        self.rating[b] = rb - kb * (score - expect)
        self.played[a], self.played[b] = self.played.get(a, 0) + 1, self.played.get(b, 0) + 1
        if m["mode"] == "live":
            self.live[a], self.live[b] = self.live.get(a, 0) + 1, self.live.get(b, 0) + 1
            if self.v0 in (a, b):
                other, points = (b, 1 - score) if a == self.v0 else (a, score)
                p, n = self.vs_v0.get(other, (0.0, 0))
                self.vs_v0[other] = (p + points, n + 1)

    def guard(self, policy_id):
        """Why `policy_id` can't replace the champion now: one line per unmet condition. Empty means it can."""
        s, why = self.s, []
        live = self.live.get(policy_id, 0)
        if live < s["promote_matches"]:
            why.append(f"{live} live match(es), needs {s['promote_matches']}")
        gap = self.rating.get(policy_id, s["elo_start"]) - self.rating.get(self.champion, s["elo_start"])
        if gap < s["promote_elo"]:
            why.append(f"{gap:+.1f} Elo over {self.champion}, needs +{s['promote_elo']}")
        points, n = self.vs_v0.get(policy_id, (0.0, 0))
        if policy_id != self.v0 and not (n and points / n > 0.5):
            why.append(f"hasn't beaten {self.v0}: {points:g} of {n} live")
        p = self.policies.get(policy_id)
        if p is None:
            why.append("not a policy in the code")
        elif p.author.startswith("llm:") and not p.approved_by:
            why.append("an LLM wrote it and no person has approved it")
        return why

    def promote(self, at):
        """After the matches decided at `at`: the challenger with the highest Elo that passes the guard, if any, takes
        over. Returns it, or None."""
        start = self.s["elo_start"]
        for p in sorted(self.policies, key=lambda p: (-self.rating.get(p, start), p)):
            if p != self.champion and not self.guard(p):
                self.promotions.append((at, self.champion, p))
                self.champion = p
                return p
        return None


def elo(matches, elo_start=1500, k=32, k_after=16, k_after_matches=30, replay_k=0.5):
    """({policy_id: rating}, {policy_id: matches played}) from the match log alone.

    Oldest first, by (decided_at, match_id): the same rows in any order give the same ratings, to the last bit.
    Expected score is 1 / (1 + 10^((R_other - R_self) / 400)). Each side moves by its own K: `k` until it has played
    `k_after_matches` matches, live and replay alike, then `k_after`; times `replay_k` in a replay match. A policy
    that never played isn't in the result: it's at `elo_start`.
    """
    league = League({**DEFAULTS, "elo_start": elo_start, "k": k, "k_after": k_after,
                     "k_after_matches": k_after_matches, "replay_k": replay_k})
    for m in sorted(matches, key=lambda m: (m["decided_at"], m["match_id"])):
        league.add(m)
    return league.rating, league.played


def standings(matches, s, policies=None, job="stall"):
    """The League after the job's whole log, replayed oldest first: Elo, live counts and the champion. Promotion is
    checked after each batch of matches decided at the same time (one run's verdicts), as a live run would."""
    policies = {p: x for p, x in (POLICIES if policies is None else policies).items() if x.job == job}
    league = League(s, policies, job)
    rows = sorted((m for m in matches if m["policy_a"].startswith(f"{job}-")),
                  key=lambda m: (m["decided_at"], m["match_id"]))
    for at, batch in itertools.groupby(rows, key=lambda m: m["decided_at"]):
        for m in batch:
            league.add(m)
        league.promote(at)
    return league


def match_log(db, at=None):
    """The block's match rows as dicts, only those decided at or before `at` when it's given. A block written before
    schema v1.3 has no match table: its log is empty."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'match'").fetchone():
        return []
    sql = f"SELECT {', '.join(MATCH_COLUMNS)} FROM match" + (" WHERE decided_at <= ?" if at else "")
    return [dict(zip(MATCH_COLUMNS, r)) for r in db.execute(sql, (at,) if at else ())]


def describe_outcome(o, no_match="fewer than two policies existed when it was served, so no match"):
    x = o.stall
    head = f"  {x.repo} {x.symbol} · {x.team}, stalled since {x.since}: "
    if not o.resumed:
        return head + "ended with no change by the team: no verdict"
    head += f"resumed {o.resumed} · "
    if o.reason:
        said = f"{o.rule} → {o.reason} {REASONS[o.reason][0]}"
        if o.reason in o.unknown:
            return head + said + ": unknown when it was served, so no policy ranked it: no match"
        return head + said + (f": {len(o.matches)} match(es)" if o.matches else f": {no_match}")
    if o.unchecked:
        fired = ", ".join(f"{rule} → {n}" for n, (rule, _) in sorted(o.fired.items())) or "none"
        return head + f"fired: {fired}, but {', '.join(o.unchecked)} couldn't be checked then: a person picks"
    if o.fired:
        rules = ", ".join(f"{rule} → {n}" for n, (rule, _) in sorted(o.fired.items()))
        return head + f"rules disagree ({rules}): a person picks"
    return head + "no rule fired: a person picks"


def describe(stall, policy):
    ranked = policy.rank(stall)
    reasons = ", ".join(f"{n} {REASONS[n][0]} ({len(stall.facts[n])})" for n in ranked) or "no reason fires"
    return (f"  {stall.repo} {stall.symbol} · {stall.team}: {stall.days} days, {stall.left} left · "
            f"{policy.policy_id}: {reasons}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True, help="the block to read: out/<run_id>/meridian.sqlite")
    ap.add_argument("--config", type=Path, default=CONFIG, help="meridian.toml")
    ap.add_argument("--db", type=Path, help="read this SQLite file instead of the run's block")
    ap.add_argument("--at", help="as of this time, YYYY-MM-DDTHH:MM:SSZ (default: the run's snapshot_at)")
    ap.add_argument("--prev-at", help="the last run's snapshot: its stalls that ended get verdicts (default: the "
                                      "block's run before --at)")
    ap.add_argument("--write", action="store_true", help="also write policy.jsonl and match.jsonl for the writer")
    ap.add_argument("--out", type=Path, help="where --write puts them (default: out/<run_id>/)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    s = settings(cfg)
    path = Path(a.db or handoff(cfg, a.run_id) / "meridian.sqlite")
    if not path.exists():
        raise SystemExit(f"no block at {path}: run the writer first, or pass --db")
    out = (a.out or handoff(cfg, a.run_id)) if a.write else None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        report(db, cfg, s, a.run_id, a.at, path, a.prev_at, out)
    return 0


def report(db, cfg, s, run_id, at, path, prev_at=None, out=None):
    run = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (run_id,)).fetchone()
    if not (run or at):
        raise SystemExit(f"{path} has no run {run_id}: pass --at")
    at = at or run[0]

    matches = match_log(db, at)
    prev_at = prev_at or previous_snapshot(db, at)
    q, cache = kb_queries(), {}
    job = stall_job(db, cfg, s, run_id, at, prev_at, matches, q=q, cache=cache)
    pairs = len({(x.repo, x.symbol) for x in job.open})
    log(f"run {run_id} as of {at}: {len(job.open)} stalled team(s) in {pairs} tracked repo and symbol pair(s)")
    for stall in job.open:
        log(f"{describe(stall, POLICIES['stall-v0'])} · served: {job.served[stall.task_id]}")
    log(f"stall job: champion {job.league.champion}, ε {job.eps:g} ({job.live} live match(es) so far): the team sees "
        f"the champion's ranking, or a challenger's ε of the time")
    if prev_at:
        log(f"since the last run ({prev_at}): {len(job.outcomes)} stall(s) ended, {len(job.matches)} new match(es)")
        say_unknown(db, prev_at, q)
        for o in job.outcomes:
            log(describe_outcome(o))
    else:
        log("no earlier run in this block, so no stall can have ended yet")

    rep = replay_job(db, cfg, s, run_id, at, matches + job.matches, q=q, cache=cache)
    if not rep.pairs:
        log("replay: the block holds one run, so there's no past stall to replay")
    else:
        n_ended = sum(len(v) for v in rep.outcomes.values())
        log(f"replay over {len(rep.pairs)} pair(s) of runs, every policy in the code: {n_ended} stall(s) ended, "
            f"{len(rep.matches)} new replay match(es), each at half K and never toward promotion")
        for (before, after), outcomes in rep.outcomes.items():
            log(f"  {before} → {after}: {len(outcomes)} ended, {sum(len(o.matches) for o in outcomes)} match(es)")
            if (before, after) != (prev_at, at):  # the live part above said that pair's
                say_unknown(db, before, q)
            for o in outcomes:
                if o.matches or (before, after) != (prev_at, at):
                    log("  " + describe_outcome(o, "every pair of policies has played it already, so no new match"))

    new = job.matches + rep.matches
    league = standings(matches + new, s)
    live = sum(league.live.values()) // 2
    log(f"Elo from {len(matches)} match(es) in the log" + (f" and {len(new)} new" if new else "")
        + f", {live} live" + ("" if live else ": no live verdicts yet (no users), so promotion can't happen"))
    stall_ids = {p for p, x in POLICIES.items() if x.job == "stall"}
    for pid in sorted(stall_ids | set(league.rating), key=lambda p: (-league.rating.get(p, s["elo_start"]), p)):
        log(f"  {pid:<12} {league.rating.get(pid, s['elo_start']):7.1f}  {league.played.get(pid, 0)} match(es)")
    for at_, was, now in league.promotions:
        log(f"promoted at {at_}: {now} replaced {was}")
    for p in sorted(league.policies):
        if p != league.champion:
            log(f"  {p} stays a challenger: {'; '.join(league.guard(p))}")

    report_explore(explore_job(db, cfg, s, run_id, at, matches, q=q), at)

    if out:
        n_policies, n_matches = write_tracking(out, at, POLICIES, policy_log(db), matches, new)
        newer = sorted(p for p, x in POLICIES.items() if x.created_at > at)
        log(f"wrote {out}/policy.jsonl ({n_policies} row(s)) and match.jsonl ({n_matches})"
            + (f": {', '.join(newer)} came after this block's snapshot, so they're left out" if newer else ""))


def say_unknown(db, at, q=None):
    """Log the stall reasons unknown at `at`, if any, and why."""
    reasons, tables = unknown_at(db, at, NEEDS, q)
    if reasons:
        names = ", ".join(f"{n} {REASONS[n][0]}" for n in reasons)
        log(f"  as of {at}, reasons {names} are unknown: no run by then wrote {', '.join(tables)} (a block keeps "
            f"only its own run's), so no policy ranked them then")


def report_explore(ex, at):
    shown = sum(len(t.served) for t in ex.tasks)
    log(f"explore job: champion {ex.league.champion}, {len(ex.found)} unfiled legacy candidate(s) as of {at} "
        f"(deprecation-marked, with callers), {ex.filed} more already tracked, so filed; {shown} shown")
    if not (ex.found or ex.filed):
        log(f"  {ex.deprecated} deprecation-marked function(s) in the block, none with a caller in call_edge: it holds "
            f"only the tracked symbols' call graph (gap G3)")
    for t in ex.tasks:
        how = (f"{' and '.join(t.shown)} team-draft interleaved" if len(t.shown) > 1
               else f"{t.shown[0]} only: no challenger")
        log(f"  {t.repo}: {len(t.served)} shown, {how}")
        for x, p in t.served[:5]:
            log(f"    {x.path} {x.scope} · {x.marker} · {x.callers} call(s) · picked by {p}")
    log("explore matches need accept or reject replies on the radar, and nothing records them yet (no users): none")


if __name__ == "__main__":
    raise SystemExit(main())
