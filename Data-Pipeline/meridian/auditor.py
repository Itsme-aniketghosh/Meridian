"""Auditor · step P · 04-architecture §9: versioned policies that choose where to look, and Elo over them.

    python -m meridian.auditor --run-id <run_id> --config meridian.toml    # reads out/<run_id>/meridian.sqlite
    python -m meridian.auditor --run-id R2 --db /tmp/fixture.sqlite --at 2026-10-01T00:00:00Z

Built so far (packets/rochan-tracking, Nov 7): the stall job's facts, policy v0, and Elo. It reads a block and writes
nothing. Next: the stall job's ε-greedy pick, verdicts and match rows (Nov 8), then the explore job and replay (Nov 10).

Facts come from KNOWLEDGE-BASE.md's queries, the text tests/test_kb.py proves on its fixture. `owner_teams` gives each
team's sites left and the days since its last change. A team is stalled when it has sites left and nothing changed for
[rules] stall_days (04 §5), blocked or not. Then `stall_upstream` ... `stall_competing_load` say which of the eight
reasons fire, and their rows are the evidence. Rules decide the facts and a policy only orders them, so every policy
ranks the same facts, and a match compares orderings, never data.

Policy v0 (04 §5): the reasons that fire, by number. Evidence never reorders them, and there are no probabilities.

Elo (04 §9): every policy starts at 1500. The match log is replayed oldest first (decided_at, then match_id), so the
ratings are a function of the log alone: recomputed every run, never stored. A policy's K is 32 for its first 30
matches, then 16, and a replay match moves half as far. Settings: meridian.toml [auditor] when the section exists,
else 04 §9's numbers.
"""
import argparse
import sqlite3
from collections import namedtuple
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from meridian.common import CONFIG, h, handoff, load_config, log
from meridian.pushcheck import kb_queries

# 04 §5's stall reasons by number, and the KNOWLEDGE-BASE.md query behind each. Each query's first column is the team
REASONS = {1: ("Upstream block", "stall_upstream"), 2: ("Never notified", "stall_never_notified"),
           3: ("Ticket mismatch", "stall_ticket_mismatch"), 4: ("Orphaned owner", "stall_orphaned_owner"),
           5: ("Semantics unclear", "stall_semantics_unclear"), 6: ("Fragility freeze", "stall_fragility_freeze"),
           7: ("Test gap", "stall_test_gap"), 8: ("Competing load", "stall_competing_load")}

# 04 §9's numbers. meridian.toml [auditor] can set any of them
DEFAULTS = {"elo_start": 1500, "k": 32, "k_after": 16, "k_after_matches": 30, "replay_k": 0.5}
SCORE = {"a": 1.0, "b": 0.0, "draw": 0.5}  # policy_a's score. policy_b's is 1 minus it
MODES = ("live", "replay")
MATCH_COLUMNS = ("match_id", "task_id", "policy_a", "policy_b", "result", "mode", "verdict_source", "decided_by",
                 "decided_at")

# One stalled team in one (repo, symbol), as of one moment. facts: {reason number: evidence rows}, only the reasons
# that fire. since is the team's last change, so task_id stays the same while the stall lasts, and a change starts a
# new stall, with a new task
Stall = namedtuple("Stall", "task_id repo symbol team since days left facts")


def stall_v0(stall):
    """04 §5: the reasons that fire, by number. Evidence never reorders them, and there are no probabilities."""
    return sorted(stall.facts)


@dataclass(frozen=True)
class Policy:
    policy_id: str   # '<job>-v<version>', the policy table's key
    job: str         # 'stall' or 'explore'
    version: int
    author: str      # a person's initials, or 'llm:<model>': never promoted without a person (04 §9)
    rank: Callable   # stall job: a Stall -> its reason numbers, most likely first


POLICIES = {p.policy_id: p for p in [Policy("stall-v0", "stall", 0, "rh", stall_v0)]}


def point(db, at, repo=None, symbol=None, run_id=None, q=None):
    """Aim KNOWLEDGE-BASE.md's queries at one moment: the one-row asof table they read (repo or symbol None means
    all), and the setup views over it, made once per connection. Temp objects only: a read-only block is fine."""
    q = q or kb_queries()
    db.execute("CREATE TEMP TABLE IF NOT EXISTS asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT)")
    db.execute("DELETE FROM asof")
    db.execute("INSERT INTO asof VALUES (?, ?, ?, ?)", (run_id, at, repo, symbol))
    if not db.execute("SELECT 1 FROM sqlite_temp_master WHERE type = 'view' AND name = 'site_now'").fetchone():
        db.executescript(q["setup"])
    return db


def _key(row):
    """Evidence rows sort the same everywhere: numbers by value, the rest as text."""
    return tuple((0, v, "") if isinstance(v, (int, float)) else (1, 0, "" if v is None else str(v)) for v in row)


def stalls(db, cfg, q=None):
    """The stalled teams where asof points, by team, each with the reasons that fire and their evidence rows."""
    q = q or kb_queries()
    _, repo, symbol = db.execute("SELECT at, repo, symbol FROM asof").fetchone()
    stalled = {team: (left, days) for team, left, _, days, _ in db.execute(q["owner_teams"])
               if left and days is not None and days >= cfg["rules"]["stall_days"]}
    since = dict(db.execute("SELECT owner_team, max(effective_date) FROM site_now GROUP BY owner_team"))
    facts = {team: {} for team in stalled}
    for n, (_, name) in REASONS.items():
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


def settings(cfg):
    """meridian.toml [auditor] over 04 §9's numbers. A key the auditor doesn't read stops the run."""
    given = cfg.get("auditor", {})
    if unknown := sorted(set(given) - set(DEFAULTS)):
        raise SystemExit(f"meridian.toml [auditor] has keys the auditor doesn't read: {', '.join(unknown)}")
    return {**DEFAULTS, **given}


def elo(matches, elo_start=1500, k=32, k_after=16, k_after_matches=30, replay_k=0.5):
    """({policy_id: rating}, {policy_id: matches played}) from the match log alone.

    Oldest first, by (decided_at, match_id): the same rows in any order give the same ratings, to the last bit.
    Expected score is 1 / (1 + 10^((R_other - R_self) / 400)). Each side moves by its own K: `k` until it has played
    `k_after_matches` matches, live and replay alike, then `k_after`; times `replay_k` in a replay match. A policy
    that never played isn't in the result: it's at `elo_start`.
    """
    rating, played = {}, {}
    for m in sorted(matches, key=lambda m: (m["decided_at"], m["match_id"])):
        a, b = m["policy_a"], m["policy_b"]
        if a == b or m["result"] not in SCORE or m["mode"] not in MODES:
            raise ValueError(f"match {m['match_id']} can't be scored: {a} v {b}, result {m['result']!r}, "
                             f"mode {m['mode']!r}")
        ra, rb = rating.get(a, elo_start), rating.get(b, elo_start)
        expect = 1 / (1 + 10 ** ((rb - ra) / 400))
        weight = replay_k if m["mode"] == "replay" else 1
        ka = (k if played.get(a, 0) < k_after_matches else k_after) * weight
        kb = (k if played.get(b, 0) < k_after_matches else k_after) * weight
        rating[a] = ra + ka * (SCORE[m["result"]] - expect)
        rating[b] = rb - kb * (SCORE[m["result"]] - expect)
        played[a], played[b] = played.get(a, 0) + 1, played.get(b, 0) + 1
    return rating, played


def match_log(db, at=None):
    """The block's match rows as dicts, only those decided at or before `at` when it's given. A block written before
    schema v1.3 has no match table: its log is empty."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'match'").fetchone():
        return []
    sql = f"SELECT {', '.join(MATCH_COLUMNS)} FROM match" + (" WHERE decided_at <= ?" if at else "")
    return [dict(zip(MATCH_COLUMNS, r)) for r in db.execute(sql, (at,) if at else ())]


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
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    s = settings(cfg)
    path = Path(a.db or handoff(cfg, a.run_id) / "meridian.sqlite")
    if not path.exists():
        raise SystemExit(f"no block at {path}: run the writer first, or pass --db")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        report(db, cfg, s, a.run_id, a.at, path)
    return 0


def report(db, cfg, s, run_id, at, path):
    run = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (run_id,)).fetchone()
    if not (run or at):
        raise SystemExit(f"{path} has no run {run_id}: pass --at")
    at = at or run[0]

    found = block_stalls(db, cfg, at, run_id)
    pairs = len({(x.repo, x.symbol) for x in found})
    log(f"run {run_id} as of {at}: {len(found)} stalled team(s) in {pairs} tracked repo and symbol pair(s)")
    for stall in found:
        log(describe(stall, POLICIES["stall-v0"]))

    matches = match_log(db, at)
    live = sum(m["mode"] == "live" for m in matches)
    rating, played = elo(matches, **s)
    log(f"Elo from {len(matches)} match(es) in the log, {live} live"
        + ("" if live else ": no live verdicts yet (no users), so promotion can't happen"))
    for pid in sorted(set(POLICIES) | set(rating), key=lambda p: (-rating.get(p, s["elo_start"]), p)):
        log(f"  {pid:<12} {rating.get(pid, s['elo_start']):7.1f}  {played.get(pid, 0)} match(es)")


if __name__ == "__main__":
    raise SystemExit(main())
