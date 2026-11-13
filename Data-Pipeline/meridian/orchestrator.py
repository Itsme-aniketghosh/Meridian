"""The orchestrator (GAPS.md gap 15, Aniket): for each stalled team, which writer steps run. A planner on a fixed menu.

    python -m meridian.orchestrator --run-id <run_id>                       # LLM off: the rules plan for every stall
    python -m meridian.orchestrator --run-id <run_id> --llm --jev --workers 8

The stall rule (KNOWLEDGE-BASE owner_teams, stall_days) finds the stalled teams and the auditors rank each one's
reasons under the served policy (seeded ε: auditor.py decides explore or exploit, never this). For each stall the
orchestrator's model works one fixed list, in order, and then stops:

    1. identify   the problems: only the stall's own reasons (the auditors' closed list), each once
    2. push       the steps that address them: only from MENU, only ones this team has tasks for, each once
    3. finish     each pushed step is written once, in a for loop over that tuple. No step adds steps

So a stall ends after at most len(MENU) steps, each tried at most once per rung of its ladder (3), with one planning
call. Nothing loops back to the model. Its answer {"problems", "steps", "reason"} is used only if every check passes;
otherwise the rules plan runs:

- problems must be among the stall's reasons (none when no reason fired), with no repeats
- steps must start with stall_reason, be on MENU, be available for the team, with no repeats
- its reason goes through fact_gate.check like any LLM text: anything not in the facts, or a person, and it's refused
- LLM off with no cached answer, or over the spend cap: rules. The rules plan is every available step, in MENU order

Then each picked task is written as the writer always writes it (agents.write: Jev's or the fixed ladder, the gate,
the template on failure). The orchestrator never writes text a person sees, and decides no fact.

Graph walkers (gap 17) are the last resort, and code decides when, never the model: a stall where no reason fires is
a tool that returned nothing usable, so its plan is marked walk. The walkers aren't built yet; the run counts them.

Every answer goes through the LLM cache, keyed on (model, prompt version, the stall's facts and available steps), so
a rerun with a warm cache makes 0 calls and the same plans. Logged like Jev's, agent "orchestrator", counts only.
"""
import argparse
import hashlib
import json
import sqlite3
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from meridian import agents
from meridian.common import CONFIG, handoff, load_config, log as say
from meridian.fact_gate import check
from meridian.llm_cache import Cache, key

AGENT = "orchestrator"
# The best model we can reach, always: the orchestrator never climbs a ladder or drops to a cheaper model. A bad answer
# is the rules plan. Gemini 3.1 Pro on our billing (GAPS.md gap 15); Claude needs Marketplace billing
MODEL = "gemini-pro"
SPEND_CAP_USD = 5.00     # the orchestrator's own calls, per run. Over it: rules
MENU = ("stall_reason", "ticket_draft", "consumer", "rewrite")   # writer tasks for a stalled team, stall_reason first

SYSTEM = """You plan the work for one stalled team in a tool that tracks a code migration across teams. Work in this
order. First, identify the problems: only from the reasons in the facts, by their exact names, most important first.
Then list the steps that address them, only from the available steps below, starting with stall_reason: a ticket
draft when sites have no ticket, a consumer note when the team needs to know what is left, a rewrite when an existing
ticket is too vague. Then stop. Never name or address a person. Your reason must not contain any number, path, ticket
key or team name. Reply with JSON only, no other text:
{"problems": ["<reason>", ...], "steps": ["stall_reason", ...], "reason": "<one short sentence>"}"""
FRAME = "{system}\n\nMenu:\n{menu}\n\nAvailable for this team:\n{available}\n\nFacts:\n{facts}\n"


def prompt_version():
    """12 hex over the system prompt, the frame and the menu: any change is a new version, so the cache never serves a
    plan made under another menu."""
    return hashlib.sha256("\x1f".join((SYSTEM, FRAME, json.dumps(MENU))).encode("utf-8")).hexdigest()[:12]


def prompt(stall, available):
    return FRAME.format(system=SYSTEM, menu=json.dumps(MENU), available=json.dumps(available),
                        facts=json.dumps(stall.facts["facts"], sort_keys=True, indent=1, ensure_ascii=False))


@dataclass(frozen=True)
class Plan:
    """One stall's plan, in the order it's worked: problems identified, steps pushed, then done. Both are tuples from
    closed lists, at most len(MENU) steps, and nothing adds to them after planning, so a plan always ends."""
    team: str
    problems: tuple  # identified: the stall's reasons it addresses, by name, from the auditors' list only
    steps: tuple     # pushed: task kinds to write, stall_reason first, each once
    walk: bool       # code's call: the auditors found no reason, so the graph walkers should look (gap 17)
    route: str       # 'orchestrator' or 'rules': who planned
    why: str         # for the report: "orchestrator", "llm off", "refused: ...", "over the spend cap"

    def __post_init__(self):  # the backstop: a plan can never hold more work than the menu
        assert len(self.steps) == len(set(self.steps)) <= len(MENU), self.steps


def reasons(stall):
    return [r["reason"] for r in stall.facts["facts"]["reasons"]]


def rules_plan(stall, available, why="rules"):
    """Every reason in the auditors' order, every available step in MENU order."""
    found = reasons(stall)
    return Plan(stall.owner_team, tuple(found), tuple(available), not found, "rules", why)


def parse(reply):
    """{"problems": [str], "steps": [str], "reason": str} from the reply (a ```json fence is read inside), else None."""
    if not isinstance(reply, str):
        return None
    fenced = agents._FENCE.match(reply.strip())
    try:
        obj = json.loads(fenced.group(1) if fenced else reply)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("reason"), str) or not all(
            isinstance(obj.get(f), list) and all(isinstance(s, str) for s in obj[f]) for f in ("problems", "steps")):
        return None
    return obj


def refusal(answer, stall, available):
    """Why the model's plan can't be used, or None. Problems only from the stall's reasons, each once (none when no
    reason fired); steps only from what's available, stall_reason first, each once."""
    problems, steps, found = answer["problems"], answer["steps"], reasons(stall)
    if len(set(problems)) != len(problems) or not set(problems) <= set(found) or bool(problems) != bool(found):
        return "refused: problems not among the stall's reasons"
    if not steps or steps[0] != "stall_reason" or len(set(steps)) != len(steps) or not set(steps) <= set(available):
        return "refused: steps outside the menu or not available"
    return None


class Orchestrator:
    """One run's planner: it keeps the run's spend on its own calls. Safe from several threads."""

    def __init__(self, *, llm=None, cache=None, log=None, teams=(), people=(), model=MODEL,
                 spend_cap_usd=SPEND_CAP_USD):
        self.llm, self.cache, self.log, self.model = llm, cache, log, model
        self.teams, self.people, self.spend_cap_usd = list(teams), list(people), spend_cap_usd
        self.spent, self.calls, self.pv, self._lock = 0.0, 0, prompt_version(), threading.Lock()

    def plan(self, stall, available):
        """The steps for one stalled team. Never raises for a bad answer: anything wrong is the rules plan."""
        available = [k for k in MENU if k in available]
        k = key(self.model, self.pv, {"stall": stall.facts["facts"], "available": available})
        entry = self.cache.get(k) if self.cache else None
        hit, t0 = entry is not None, time.perf_counter()
        if not hit:
            if self.llm is None:
                return rules_plan(stall, available, "llm off")
            with self._lock:
                if self.spent >= self.spend_cap_usd:
                    return rules_plan(stall, available, "over the spend cap")
            r = self.llm(self.model, prompt(stall, available))
            entry = {"model": self.model, "prompt_version": self.pv, "reply": r["reply"],
                     "tokens_in": int(r.get("tokens_in", 0)), "tokens_out": int(r.get("tokens_out", 0)),
                     "cost_usd": float(r.get("cost_usd", 0.0))}
            if agents.names_a_person(str(r["reply"]), self.people):   # never into the committed cache
                entry = {**entry, "reply": None, **agents.REDACTED}
            with self._lock:
                self.spent += entry["cost_usd"]
                self.calls += 1
            if self.cache:
                self.cache.put(k, entry)
        answer, gate_pass = parse(entry.get("reply")), None
        why = "refused: not the JSON asked for" if answer is None else refusal(answer, stall, available)
        if why:
            chosen = rules_plan(stall, available, why)
        else:
            try:
                gate_pass = check(answer["reason"], stall.facts, teams=self.teams, people=self.people,
                                  vocab=agents.VOCAB).passed
            except ValueError:          # the stall's facts name a person: the writer never writes it either
                gate_pass = False
            chosen = Plan(stall.owner_team, tuple(answer["problems"]), tuple(answer["steps"]), not reasons(stall),
                          AGENT, AGENT) if gate_pass \
                else rules_plan(stall, available, "refused: the reason says something not in the facts")
        self._log(stall, entry, hit, gate_pass, chosen.route, (time.perf_counter() - t0) * 1000)
        return chosen

    def select(self, todo, plans=None, workers=1):
        """agents.run(plan=...): the stalled teams' tasks the plans pick, in task order. Other teams' tasks don't run.
        plans: a list the plans are appended to, in stall order, for the report. workers: stalls planned at once."""
        by_team = {}
        for t in todo:
            by_team.setdefault(t.owner_team, []).append(t)
        stalls = [t for t in todo if t.kind == "stall_reason"]
        with ThreadPoolExecutor(max(1, workers)) as pool:
            made = list(pool.map(lambda s: self.plan(s, {t.kind for t in by_team[s.owner_team]}), stalls))
        keep = set()
        for stall, p in zip(stalls, made):
            keep |= {id(t) for t in by_team[stall.owner_team]
                     if t.kind in p.steps and (t.kind != "stall_reason" or t is stall)}
        if plans is not None:
            plans.extend(made)
        return [t for t in todo if id(t) in keep]

    def _log(self, stall, entry, hit, gate_pass, route, latency_ms):
        if self.log is None:
            return
        paid = not hit
        self.log(agent=AGENT, model=self.model, prompt_version=self.pv,
                 tokens_in=entry["tokens_in"] if paid else 0, tokens_out=entry["tokens_out"] if paid else 0,
                 cost_usd=entry["cost_usd"] if paid else 0.0, cache_hit=hit, gate_pass=gate_pass, route=route,
                 source=None, task=stall.kind, repo=stall.repo or None, team=stall.owner_team,
                 latency_ms=round(latency_ms, 3), facts_hash=agents.facts_hash(stall.facts))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True, help="the block to read: out/<run_id>/meridian.sqlite")
    ap.add_argument("--config", type=Path, default=CONFIG, help="meridian.toml")
    ap.add_argument("--db", type=Path, help="read this SQLite file instead of the run's block")
    ap.add_argument("--cache-dir", type=Path, help="the LLM cache (default: Data-Pipeline/llm_cache/)")
    ap.add_argument("--strict-cache", action="store_true", help="a cache miss stops the run (CI)")
    ap.add_argument("--llm", action="store_true", help="call models live (meridian.llm.Client, $MERIDIAN_GCP_PROJECT)")
    ap.add_argument("--jev", action="store_true", help="Jev picks each written task's starting model")
    ap.add_argument("--models", help="the writer's ladder for the steps it writes, comma-separated, smallest first "
                                     "(default agents.LADDER). The orchestrator itself always plans on MODEL")
    ap.add_argument("--workers", type=int, default=8, help="tasks written at once (default 8)")
    ap.add_argument("--max-usd", type=float, help="the client's spend cap for this run (default settings.toml [llm])")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    path = Path(a.db or handoff(cfg, a.run_id) / "meridian.sqlite")
    if not path.exists():
        raise SystemExit(f"no block at {path}: run the writer first, or pass --db")
    cache = Cache(a.cache_dir) if a.cache_dir else Cache()
    llm = None
    if a.llm:
        from meridian.llm import Client
        llm = Client(max_usd=a.max_usd)
    plans = []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        at = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (a.run_id,)).fetchone()[0]
        teams, people = agents.names(db)
        orch = Orchestrator(llm=llm, cache=cache, teams=teams, people=people)
        router = None
        if a.jev:
            from meridian.jev import Router
            router = Router(llm=llm, cache=cache, teams=teams, people=people)
        ladder = tuple(m.strip() for m in a.models.split(",") if m.strip()) if a.models else None
        result = agents.run(db, cfg, a.run_id, at, llm=llm, cache=cache, strict_cache=a.strict_cache, router=router,
                            ladder=ladder, workers=a.workers, plan=lambda todo: orch.select(todo, plans, a.workers))
    say(f"{len(plans)} stalled teams planned: {dict(Counter(p.route for p in plans))}; "
        f"{sum(p.walk for p in plans)} need the graph walkers (no reason fired; gap 17)")
    for why, n in sorted(Counter(p.why for p in plans).items()):
        say(f"  {n} {why}")
    say(f"steps written: {dict(Counter(w.task.kind for w in result.written))}, "
        f"{sum(w.source == 'llm' for w in result.written)} LLM texts, "
        f"{sum(w.source == 'template' for w in result.written)} templates")
    if llm is not None:
        say(f"{llm.calls} calls, ${llm.spent:.4f} (orchestrator ${orch.spent:.4f})")
    return 0


if __name__ == "__main__":
    main()
