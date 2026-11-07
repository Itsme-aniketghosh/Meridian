"""Auditor · step P · 04-architecture §9: versioned policies that choose where to look, and Elo over them.

Built so far (packets/rochan-tracking, Nov 7): Elo over the match log. Policy v0 and the command come next.

Elo (04 §9): every policy starts at 1500. The match log is replayed oldest first (decided_at, then match_id), so the
ratings are a function of the log alone: recomputed every run, never stored. A policy's K is 32 for its first 30
matches, then 16, and a replay match moves half as far. Settings: meridian.toml [auditor] when the section exists,
else 04 §9's numbers.
"""
# 04 §9's numbers. meridian.toml [auditor] can set any of them
DEFAULTS = {"elo_start": 1500, "k": 32, "k_after": 16, "k_after_matches": 30, "replay_k": 0.5}
SCORE = {"a": 1.0, "b": 0.0, "draw": 0.5}  # policy_a's score. policy_b's is 1 minus it
MODES = ("live", "replay")
MATCH_COLUMNS = ("match_id", "task_id", "policy_a", "policy_b", "result", "mode", "verdict_source", "decided_by",
                 "decided_at")


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
