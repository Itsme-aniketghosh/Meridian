"""Jev, the router (04-architecture §6, Rachna): for each LLM task, which agent and which model start it.

    from meridian.jev import Router
    router = Router(llm=llm, cache=Cache(), log=tracking.log_llm_call, teams=teams, people=people)
    pick = router.pick(task)                    # Pick(agent, rung, model, route, why)
    pick = router.next(pick, "gate_miss")       # one model up, or None: ship the template (no diff, for code)

Jev is a general model that reads the task and answers JSON {"agent", "model", "reason"}. Its answer is used only if
every check passes; otherwise the rules pick, and the run carries on:

- the pick must be in TABLE (04 §6): the task's own agent, and a model on that agent's ladder. Anything else is refused
- its reason goes through fact_gate.check like any LLM text: a number, path, ticket or team that isn't a fact, or a
  person, and the pick is refused
- Jev down (no LLM, no cached answer), over its spend cap, or a top-model pick past the top-model budget: rules
- the rules: each agent starts on the smallest model of its ladder

Every Jev answer goes through the LLM cache (llm_cache.py, Vishwa), keyed on (Jev's model, prompt version, the task's
kind and facts), so the same task twice gets the same pick, and a rerun with a warm cache makes 0 calls. Each answer,
cached or called, is logged once through model.tracking.log_llm_call (agent "jev", route jev or rules, the task's
facts_hash), counts and labels only. A rules pick with no Jev answer isn't a call and isn't logged: the writer's own
rows say route "rules".

Settings, constants here and not meridian.toml [llm], for the reason agents.py gives (a new section there changes every
block's config hash and run_id): the router's model (decision 14: the small one), Jev's spend cap per run and the
top-model budget per run. The coding ladder is the diff drafter's own (Utkarsh), read from meridian.diffs; without
it the diff drafter has no LLM rung and Jev routes nothing to it.
"""
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass

from meridian import agents
from meridian.fact_gate import check
from meridian.llm_cache import key

AGENT = "jev"
ROUTER_MODEL = agents.SMALL    # decision 14: the small model unless the mid one gets >= 3 more of 30 held-out right
SPEND_CAP_USD = 2.00           # Jev's own calls, per run. Over it: rules
TOP_BUDGET = 10                # picks of an agent's largest model, per run, by Jev or by climbing. Past it: no top


def _coding_ladder():
    """The diff drafter's ladder (coding small -> mid -> large), from its module once Utkarsh's drafter is in."""
    try:
        from meridian import diffs as diff_drafter   # the drafter is meridian/diffs.py (packets/README.md)
        return tuple(diff_drafter.LADDER)
    except (ImportError, AttributeError):
        return ()


# 04 §6's table: the only picks there are. Each agent's models, smallest first, and the failures that move it up.
TABLE = {
    "writer": {"ladder": tuple(agents.LADDER), "retry_on": ("bad_json", "gate_miss")},
    "diff_drafter": {"ladder": _coding_ladder(), "retry_on": ("no_apply", "no_parse", "no_resolve")},
    "policy_writer": {"ladder": _coding_ladder()[1:], "retry_on": ("tests_fail",)},   # coding mid -> large
}
# Which agent does each task kind (the writer's four, Utkarsh's diffs, the policy writer if time allows)
TASK_AGENT = {"rewrite": "writer", "ticket_draft": "writer", "stall_reason": "writer", "consumer": "writer",
              "diff": "diff_drafter", "policy": "policy_writer"}

SYSTEM = """You route one task for a tool that tracks a code migration across teams. Pick which agent does it and
which model it starts on, only from the table below: the task's own agent, and a model on that agent's list (smallest
first). Start small unless the task looks hard for a small model: many facts, several teams, or long text to write.
Never name or address a person. Your reason must not contain any number, path, ticket key or team name. Reply with
JSON only, no other text:
{"agent": "<agent>", "model": "<model>", "reason": "<one short sentence>"}"""
FRAME = "{system}\n\nTable:\n{table}\n\nTask kind: {kind}\n\nFacts:\n{facts}\n"
_FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.S)


def table_json():
    return json.dumps({a: list(v["ladder"]) for a, v in TABLE.items() if v["ladder"]}, sort_keys=True, indent=1)


def prompt_version():
    """12 hex over the system prompt, the frame and the table: a change to any of them is a new version, so the cache
    never serves a pick made under another table."""
    return hashlib.sha256("\x1f".join((SYSTEM, FRAME, table_json())).encode("utf-8")).hexdigest()[:12]


def prompt(task):
    facts = json.dumps(task.facts["facts"], sort_keys=True, indent=1, ensure_ascii=False)
    return FRAME.format(system=SYSTEM, table=table_json(), kind=task.kind, facts=facts)


@dataclass(frozen=True)
class Pick:
    agent: str
    rung: int        # index on the agent's ladder
    model: str       # None: the agent has no LLM rung (the template ships; for code, the codemod or no diff)
    route: str       # 'jev' or 'rules': who picked
    why: str         # for the report: "jev", "jev off", "refused: ...", "over the spend cap", ...


def rules_pick(kind, why="rules"):
    """The fixed rule: the task's agent, on the smallest model of its ladder."""
    agent = TASK_AGENT[kind]
    ladder = TABLE[agent]["ladder"]
    return Pick(agent, 0, ladder[0] if ladder else None, "rules", why)


def parse(reply):
    """{"agent", "model", "reason"} from Jev's reply (a ```json fence is read inside), else None."""
    if not isinstance(reply, str):
        return None
    fenced = _FENCE.match(reply.strip())
    try:
        obj = json.loads(fenced.group(1) if fenced else reply)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not all(isinstance(obj.get(k), str) for k in ("agent", "model", "reason")):
        return None
    return obj


class Router:
    """One run's router: it keeps the run's spend on Jev's calls and its top-model count."""

    def __init__(self, *, llm=None, cache=None, log=None, teams=(), people=(), model=ROUTER_MODEL,
                 spend_cap_usd=SPEND_CAP_USD, top_budget=TOP_BUDGET):
        self.llm, self.cache, self.log = llm, cache, log
        self.teams, self.people, self.model = list(teams), list(people), model
        self.spend_cap_usd, self.top_budget = spend_cap_usd, top_budget
        self.spent, self.top_used, self.calls = 0.0, 0, 0
        self.pv = prompt_version()
        self._lock = threading.RLock()  # agents.run(workers=) routes from several threads: the budgets stay exact

    def _top(self, pick):
        ladder = TABLE[pick.agent]["ladder"]
        return bool(ladder) and pick.rung == len(ladder) - 1

    def _take_top(self):
        """One pick of an agent's largest model off the run's budget: False when it's spent."""
        with self._lock:
            if self.top_used >= self.top_budget:
                return False
            self.top_used += 1
            return True

    def pick(self, task):
        """The agent and model to start `task` on. Never raises for a bad answer: anything wrong is a rules pick."""
        fallback = rules_pick(task.kind)
        agent = fallback.agent
        ladder = TABLE[agent]["ladder"]
        if not ladder:
            return rules_pick(task.kind, "no LLM rung for this agent yet")
        k = key(self.model, self.pv, {"task": task.kind, "facts": task.facts["facts"]})
        entry = self.cache.get(k) if self.cache else None
        hit, t0 = entry is not None, time.perf_counter()
        if not hit:
            if self.llm is None:
                return rules_pick(task.kind, "jev off")
            with self._lock:
                over = self.spent >= self.spend_cap_usd
            if over:
                return rules_pick(task.kind, "over the spend cap")
            r = self.llm(self.model, prompt(task))
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
        if answer is None:
            chosen = rules_pick(task.kind, "refused: not the JSON asked for")
        elif answer["agent"] != agent or answer["model"] not in ladder:
            chosen = rules_pick(task.kind, "refused: outside the table")
        else:
            try:
                gate_pass = check(answer["reason"], task.facts, teams=self.teams, people=self.people,
                                  vocab=agents.VOCAB).passed
            except ValueError:       # the task's facts name a person: the writer never writes it either
                gate_pass = False
            if not gate_pass:
                chosen = rules_pick(task.kind, "refused: the reason says something not in the facts")
            else:
                chosen = Pick(agent, ladder.index(answer["model"]), answer["model"], "jev", "jev")
                if self._top(chosen) and not self._take_top():
                    chosen = rules_pick(task.kind, "top-model budget spent")
        self._log(task, entry, hit, gate_pass, chosen.route, (time.perf_counter() - t0) * 1000)
        return chosen

    def next(self, pick, failure):
        """After a failed check: one model up the same agent's ladder, or None to stop (the template ships; for code,
        no diff). Only the failures 04 §6 retries on climb, and never past the top-model budget."""
        spec = TABLE[pick.agent]
        if failure not in spec["retry_on"] or pick.model is None or pick.rung + 1 >= len(spec["ladder"]):
            return None
        up = Pick(pick.agent, pick.rung + 1, spec["ladder"][pick.rung + 1], pick.route, f"{failure}: one model up")
        if self._top(up) and not self._take_top():
            return None
        return up

    def ladder(self, task):
        """The models to try for `task`, from Jev's pick up: what an agent iterates over. (models, route)."""
        p = self.pick(task)
        if p.model is None:
            return (), p.route
        return tuple(TABLE[p.agent]["ladder"][p.rung:]), p.route

    def _log(self, task, entry, hit, gate_pass, route, latency_ms):
        if self.log is None:
            return
        paid = not hit
        self.log(agent=AGENT, model=self.model, prompt_version=self.pv,
                 tokens_in=entry["tokens_in"] if paid else 0, tokens_out=entry["tokens_out"] if paid else 0,
                 cost_usd=entry["cost_usd"] if paid else 0.0, cache_hit=hit, gate_pass=gate_pass, route=route,
                 source=None, task=task.kind, repo=task.repo or None, team=task.owner_team,
                 latency_ms=round(latency_ms, 3), facts_hash=agents.facts_hash(task.facts))
