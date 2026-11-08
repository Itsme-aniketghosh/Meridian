"""Simulations behind the auditor's decisions 9–11 and its "Ranks right" bar (Model-Pipeline/packets/rochan-tracking:
decisions.md, pass-bars.md §4). They run the auditor's own code: serve, epsilon, League and its promotion guard.

    python -m meridian.auditor_sim --decide 9 10 11              # from Data-Pipeline/: 20 seeds, then 2,000
    python -m meridian.auditor_sim --decide 9 --seeds 200        # one size, for a quick look

The world: stall policies of known skill on the Elo scale. Each decided stall draws one performance per policy, its
skill plus Gumbel noise of scale 400 / ln 10. Then a policy beats another 1 / (1 + 10^(-gap / 400)) of the time, the
curve Elo assumes, and one stall's results agree with each other. Every policy ranks every stall in shadow (04 §9), so
a stall makes a match for every pair. Every option of a decision runs on the same seeds, so on the same matches.

With Model-Pipeline on the path and mlflow installed, each decision logs a run tagged decision=rochan-<n> in
meridian-auditor (Contract 3). Without them it says so and only prints.
"""
import argparse
import itertools
import math
import random
import statistics
import sys
import time
from pathlib import Path

from meridian import auditor as A

SKILLS = {"stall-v0": 1500, "stall-v1": 1700, "stall-v2": 1300}  # v1 the best, v0 the rule table, v2 the worst
SAME = {"stall-v0": 1500, "stall-v1": 1500, "stall-v2": 1500}    # any promotion here is luck
BEST, RIGHT = "stall-v1", ["stall-v1", "stall-v0", "stall-v2"]
SCALE = 400 / math.log(10)
STALLS = 67  # 201 matches: the packet's 200

EPS = {"0.2 then 0.1": {"eps": 0.2, "eps_after": 0.1}, "0.1": {"eps": 0.1, "eps_after": 0.1}}
K = {"32 then 16": {"k": 32, "k_after": 16}, "24 flat": {"k": 24, "k_after": 24}}
REPLAY = {"half K": 0.5, "full K": 1.0, "no replay": None}
WRONG = (0.3, 0.0, 0.5)  # the share of replay verdicts that are wrong: 0.3 decides


def stall_results(skills, rng):
    """One decided stall: (policy_a, policy_b, 'a' or 'b') for every pair, from one performance per policy."""
    perf = {p: s - SCALE * math.log(-math.log(rng.random() or 1e-12)) for p, s in sorted(skills.items())}
    return [(a, b, "a" if perf[a] > perf[b] else "b") for a, b in itertools.combinations(sorted(skills), 2)]


def row(i, a, b, result, mode="live"):
    """Match i as the match table holds it. Three to a stall, each stall decided after the last."""
    return {"match_id": f"m{i:06d}", "task_id": f"t{i // 3:05d}", "policy_a": a, "policy_b": b, "result": result,
            "mode": mode, "verdict_source": "rule", "decided_by": "simulation", "decided_at": f"{i // 3:06d}"}


def shadow(skills, n, seed):
    """The first n matches of simulated stalls, every policy in shadow."""
    rng, out = random.Random(seed), []
    while len(out) < n:
        out += [row(len(out) + j, a, b, r) for j, (a, b, r) in enumerate(stall_results(skills, rng))]
    return out[:n]


def policies(skills):
    return {p: A.Policy(p, "stall", int(p.rsplit("v", 1)[1]), "rh", A.stall_v0, "2026-01-01T00:00:00Z")
            for p in skills}


def order(rating):
    return sorted(rating, key=lambda p: (-rating[p], p))


def settings(**over):
    return {**A.DEFAULTS, **over}


# Decision 9: ε schedule


def eps_run(seed, s, stalls=STALLS):
    """One seed: (matches until v1 is champion, None if never; the share of stalls with a worse champion; the share
    of stalls where the team saw a worse ranking than v1's)."""
    rng, league = random.Random(seed), A.League(s, policies(SKILLS))
    first, worse_champion, worse_seen, i = None, 0, 0, 0
    for k in range(stalls):
        live = sum(league.live.values()) // 2
        challengers = [p for p in sorted(SKILLS) if p != league.champion]
        seen = A.serve(f"seed-{seed}", f"stall-{k}", league.champion, challengers, A.epsilon(live, s))
        worse_champion += league.champion != BEST
        worse_seen += seen != BEST
        for a, b, r in stall_results(SKILLS, rng):
            league.add(row(i, a, b, r))
            i += 1
        if league.promote(f"{k:06d}") == BEST and first is None:
            first = i
    return first, worse_champion / stalls, worse_seen / stalls


def decide_9(seeds):
    out = {}
    for name, eps in EPS.items():
        runs = [eps_run(seed, settings(**eps)) for seed in seeds]
        firsts = [r[0] for r in runs if r[0] is not None]
        out[name] = {"per_seed": [r[:2] for r in runs],
                     "matches_to_best": statistics.median(firsts) if firsts else None,
                     "never_best": sum(r[0] is None for r in runs),
                     "worse_champion": statistics.mean(r[1] for r in runs),
                     "worse_seen": statistics.mean(r[2] for r in runs)}
    return out


def pick_9(res):
    """decisions.md 9: the shadow numbers can't depend on ε; then the lower share of worse rankings seen, unless
    within 0.5 points of 04's schedule."""
    a, b = res["0.2 then 0.1"], res["0.1"]
    if a["per_seed"] != b["per_seed"]:
        raise SystemExit("decision 9: promotion moved with ε, so the simulation is wrong")
    if abs(a["worse_seen"] - b["worse_seen"]) <= 0.005:
        return "0.2 then 0.1"
    return min(res, key=lambda name: res[name]["worse_seen"])


# Decision 10: Elo K


def right_orders(seed, s, checkpoints, skills=SKILLS):
    """{n: Elo's order of the three is right after n matches}, on one seed's matches."""
    league, out = A.League(s), {}
    for n, m in enumerate(shadow(skills, max(checkpoints), seed), 1):
        league.add(m)
        if n in checkpoints:
            out[n] = order(league.rating) == RIGHT
    return out


def lucky(seed, s, stalls=STALLS):
    """True if any policy passes the guard within `stalls` stalls when all three have the same skill."""
    return first_promotion(SAME, seed, s, stalls)[0] is not None


def decide_10(seeds, checkpoints=(30, 60, 100)):
    out = {}
    for name, k in K.items():
        s = settings(**k)
        runs = [right_orders(seed, s, checkpoints) for seed in seeds]
        wrong = {n: sum(not r[n] for r in runs) / len(runs) for n in checkpoints}
        out[name] = {"wrong": wrong, "wrong_mean": statistics.mean(wrong.values()),
                     "lucky": sum(lucky(seed, s) for seed in seeds) / len(seeds)}
    return out


def pick_10(res):
    """decisions.md 10: out if it promotes in over 5% of same-skill seeds; then the lower mean wrong-order share,
    unless within 1 point of 04's K. The rule doesn't say what happens if every option is out: then no option has
    won, and 04's K stays (show_10 says so)."""
    ok = {name: r for name, r in res.items() if r["lucky"] <= 0.05}
    if not ok:
        return "32 then 16"
    best = min(ok, key=lambda name: ok[name]["wrong_mean"])
    if "32 then 16" in ok and ok["32 then 16"]["wrong_mean"] - ok[best]["wrong_mean"] <= 0.01:
        return "32 then 16"
    return best


# Decision 11: replay weight


def replay_rows(seed, wrong, replay=100, live=60):
    """One seed's matches: `replay` replay matches first, from past stalls whose verdict is wrong with probability
    `wrong` (each of its matches then a coin flip), then `live` live matches with right verdicts."""
    rng, rows = random.Random(seed), []
    while len(rows) < replay:
        results, bad = stall_results(SKILLS, rng), rng.random() < wrong
        for a, b, r in results:
            rows.append(row(len(rows), a, b, ("a" if rng.random() < 0.5 else "b") if bad else r, mode="replay"))
    rows = rows[:replay]
    while len(rows) < replay + live:
        rows += [row(len(rows) + j, a, b, r) for j, (a, b, r) in enumerate(stall_results(SKILLS, rng))]
    return rows[:replay], rows[replay:replay + live]


def decide_11(seeds, checkpoints=(30, 60)):
    out = {}
    for wrong in WRONG:
        data = [replay_rows(seed, wrong, live=max(checkpoints)) for seed in seeds]
        for name, weight in REPLAY.items():
            right = {n: 0 for n in checkpoints}
            for replay, live in data:
                league = A.League(settings(replay_k=weight or 0))
                for m in replay if weight is not None else ():
                    league.add(m)
                for n, m in enumerate(live, 1):
                    league.add(m)
                    if n in checkpoints:
                        right[n] += order(league.rating) == RIGHT
            wrong_share = {n: 1 - right[n] / len(data) for n in checkpoints}
            out[(wrong, name)] = {"wrong": wrong_share, "wrong_mean": statistics.mean(wrong_share.values())}
    return out


def pick_11(res):
    """decisions.md 11: the lower mean wrong-order share at 30% wrong replay verdicts; half K if within 1 point."""
    at = {name: res[(0.3, name)]["wrong_mean"] for name in REPLAY}
    best = min(at, key=at.get)
    return "half K" if at["half K"] - at[best] <= 0.01 else best


# Printing and logging


def pct(x):
    return f"{100 * x:.1f}%"


def show_9(res):
    print("| Schedule | Matches until v1 is champion (median) | Seeds where it never is | Stalls with a worse champion "
          "| Stalls where the team sees a worse ranking |")
    print("|---|---|---|---|---|")
    for name, r in res.items():
        print(f"| {name} | {r['matches_to_best']} | {r['never_best']} | {pct(r['worse_champion'])} "
              f"| {pct(r['worse_seen'])} |")


def show_10(res):
    print("| K | Wrong order after 30 | 60 | 100 | Mean | Same skill: seeds with a promotion |")
    print("|---|---|---|---|---|---|")
    for name, r in res.items():
        w = r["wrong"]
        print(f"| {name} | {pct(w[30])} | {pct(w[60])} | {pct(w[100])} | {pct(r['wrong_mean'])} | {pct(r['lucky'])} |")
    if all(r["lucky"] > 0.05 for r in res.values()):
        print("No K passes the luck check: every one promotes a same-skill policy in over 5% of seeds, so none is "
              "picked and 04's stays. The guard, not K, lets luck through: see guard_luck.")


def guard_luck(seeds, guards=((30, 50), (30, 100), (60, 100), (90, 150)), horizons=(67, 200)):
    """Not part of a decision's rule: how often the promotion guard promotes by luck. For each (live matches, Elo
    margin) and number of stalls: the share of same-skill seeds with a promotion, and of 200-apart seeds where v1
    is promoted, with the median match at which it is."""
    out = {}
    for need, margin in guards:
        s = settings(promote_matches=need, promote_elo=margin)
        for stalls in horizons:
            same = [first_promotion(SAME, seed, s, stalls) for seed in seeds]
            best = [first_promotion(SKILLS, seed, s, stalls) for seed in seeds]
            at = [n for n, p in best if p == BEST]
            out[(need, margin, stalls)] = {"same_promoted": sum(n is not None for n, _ in same) / len(seeds),
                                           "best_promoted": len(at) / len(seeds),
                                           "best_at": statistics.median(at) if at else None,
                                           "worst_promoted": sum(p == "stall-v2" for _, p in best) / len(seeds)}
    return out


def first_promotion(skills, seed, s, stalls):
    """(the match count at the first promotion, the policy promoted), or (None, None)."""
    rng, league, i = random.Random(seed), A.League(s, policies(skills)), 0
    for k in range(stalls):
        for a, b, r in stall_results(skills, rng):
            league.add(row(i, a, b, r))
            i += 1
        promoted = league.promote(f"{k:06d}")
        if promoted:
            return i, promoted
    return None, None


def show_guard(res):
    print("| Guard: live matches, Elo margin | Stalls (matches) | Same skill: promoted by luck "
          "| 200 apart: v1 promoted | at match (median) | v2 promoted |")
    print("|---|---|---|---|---|---|")
    for (need, margin, stalls), r in res.items():
        print(f"| {need}, +{margin} | {stalls} ({3 * stalls}) | {pct(r['same_promoted'])} | {pct(r['best_promoted'])} "
              f"| {r['best_at']} | {pct(r['worst_promoted'])} |")


def show_11(res):
    print("| Wrong replay verdicts | Replay | Wrong order after 30 live | 60 live | Mean |")
    print("|---|---|---|---|---|")
    for (wrong, name), r in res.items():
        print(f"| {pct(wrong)} | {name} | {pct(r['wrong'][30])} | {pct(r['wrong'][60])} | {pct(r['wrong_mean'])} |")


def flat(res):
    """Numbers only, for MLflow metric names."""
    out = {}
    for key, r in res.items():
        name = "_".join(str(k) for k in (key if isinstance(key, tuple) else (key,))).replace(" ", "_")
        for metric, v in r.items():
            if isinstance(v, dict):
                out.update({f"{name}.{metric}_{n}": x for n, x in v.items()})
            elif isinstance(v, (int, float)):
                out[f"{name}.{metric}"] = v
    return out


def log_run(n, res, picked, seeds):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Model-Pipeline"))
        import mlflow
        from model import tracking
    except ImportError as e:
        print(f"not logged to MLflow ({e.name} isn't installed here): run from Model-Pipeline's environment to log")
        return
    with tracking.start_run(f"decision-{n}", owner="rochan", experiment=tracking.AUDITOR,
                            block_run_id="simulation", decision=f"rochan-{n}"):
        mlflow.log_params({"seeds": len(seeds), "picked": picked})
        mlflow.log_metrics(flat(res))


DECIDE = {9: (decide_9, pick_9, show_9), 10: (decide_10, pick_10, show_10), 11: (decide_11, pick_11, show_11)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--decide", type=int, nargs="+", choices=sorted(DECIDE), required=True)
    ap.add_argument("--seeds", type=int, nargs="+", default=[20, 2000], help="seed counts to run (default: 20 2000)")
    ap.add_argument("--no-log", action="store_true", help="print only")
    a = ap.parse_args(argv)
    for n in a.decide:
        decide, pick, show = DECIDE[n]
        if n == 10:
            print(f"\nthe promotion guard and luck, seeds 1–{max(a.seeds)} (not part of a rule)")
            show_guard(guard_luck(range(1, max(a.seeds) + 1)))
        for count in a.seeds:
            seeds = range(1, count + 1)
            t0 = time.perf_counter()
            res = decide(seeds)
            picked = pick(res)
            print(f"\ndecision {n}, seeds 1–{count} ({time.perf_counter() - t0:.1f} s): picked {picked}")
            show(res)
            if count == max(a.seeds) and not a.no_log:
                log_run(n, res, picked, seeds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
