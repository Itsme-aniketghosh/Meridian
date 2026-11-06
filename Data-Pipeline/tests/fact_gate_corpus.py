"""The fact gate's test set: 200 clean texts and 200 with one injected error, from the fixture acme/shop
(KNOWLEDGE-BASE.md, "The fixture"). Seeded, so every run builds the same 400 texts.

    python -m tests.fact_gate_corpus      # from Data-Pipeline/: decisions 1 and 2, every option, as a table

Each text is a phrasing with slots, filled from one task's facts. A clean text fills every slot with its fact. An
injected text changes one thing, 40 of each kind:
- wrong_number:  a number slot gets a value that isn't a fact
- made_up_path:  a path slot gets a path or line that isn't a fact
- swapped_team:  a team slot (or an added clause) names a real team that isn't in this task's facts
- fake_ticket:   a ticket slot (or an added clause) gets a key that doesn't exist
- other_task:    an added clause carries a real fact from another task

The texts are ours, not an LLM's (decisions.md, known limits).
"""
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian.fact_gate import KIND_SETS, NUMBER_MODES, check

SEED = 5
TEAMS = ["platform", "checkout", "fulfillment", "payments", "billing", "search"]
PEOPLE = ["Ann", "Bob"]
TICKETS = ["PLAT-2291", "CHK-311", "CHK-298", "FUL-7", "PAY-10", "PAY-12", "OPS-1"]
SHA = "3f9a2c1d8e7b6a5f4c3d2e1f0a9b8c7d6e5f4a3b"
KINDS = ("wrong_number", "made_up_path", "swapped_team", "fake_ticket", "other_task")
FAKE_PATHS = ["cart/discount.py:88", "pay/wallet.py:14", "ship/rates.py:3", "billing/tax.py:41", "auth/session.py:9"]

# Each task: its facts (Contract 5's inner object) and phrasings. Slots: {num:name}, {pct:name}, {path:name},
# {line:name} (a bare ":231"), {team:name}, {ticket:name}, {date:name}, {sha:name}. A path slot names a dict fact
# with path and line, or a "path:line" string.
TASKS = [
    {"name": "rewrite CHK-311",
     "facts": {"key": "CHK-311", "team": "checkout",
               "s0": {"path": "cart/pricing.py", "line": 214, "owner_team": "checkout", "bug_fixes": 4, "in_except": 0},
               "s1": {"path": "cart/pricing.py", "line": 231, "owner_team": "checkout", "bug_fixes": 4, "in_except": 1},
               "bug_fixes": 4, "left": 2},  # "Two calls left" is a count, so it's a fact too
     "say": ["{ticket:key}: replace verify_token in {team:team}'s {path:s0} and {line:s1}. The second call sits inside "
             "`except AuthError`. Keep the same handling?",
             "{ticket:key} has no description. Done when {path:s0} and {line:s1} no longer call verify_token.",
             "Two calls left for {team:team}: {path:s0} and {path:s1}. The file has had {num:bug_fixes} bug fixes.",
             "Rewrite of {ticket:key}: {team:team} owns {path:s0} and {line:s1}; {num:bug_fixes} past bug fixes "
             "make this file risky.",
             "Move {path:s0} to the new call first, then {line:s1}, which handles AuthError."]},
    {"name": "maker billing",
     "facts": {"team": "billing", "count": 1, "sites": "billing/invoice.py:12"},
     "say": ["{team:team} has {num:count} untracked call at {path:sites}. Draft ticket: replace it with the new API.",
             "No ticket covers {path:sites}. Suggested ticket for {team:team}: migrate {num:count} call.",
             "Draft for {team:team}: {num:count} site, {path:sites}, still calls verify_token.",
             "Untracked: {path:sites} ({team:team}). Done when it no longer calls verify_token."]},
    {"name": "maker checkout",
     "facts": {"team": "checkout", "count": 1, "sites": "cart/totals.py:57"},
     "say": ["{team:team} has {num:count} untracked call at {path:sites}.",
             "Suggested ticket for {team:team}: {path:sites} is the {num:count} call no ticket covers.",
             "Draft: migrate {path:sites}. Owner: {team:team}.",
             "No ticket names {path:sites}. Add it to {team:team}'s board?"]},
    {"name": "maker payments",
     "facts": {"team": "payments", "count": 1, "sites": "pay/charge.py:30", "added": "2026-09-28"},
     "say": ["{team:team} added a new call at {path:sites} on {date:added}. No ticket covers it.",
             "New since the last run: {path:sites}, added {date:added}. Draft ticket for {team:team}.",
             "{team:team} has {num:count} untracked call: {path:sites}.",
             "Draft for {team:team}: {path:sites} was added {date:added} and still calls verify_token."]},
    {"name": "staff headline",
     "facts": {"present": 8, "open_tickets": 5},
     "say": ["{num:present} call sites remain, across {num:open_tickets} open tickets.",
             "Still to migrate: {num:present} sites. Open tickets: {num:open_tickets}.",
             "There are {num:present} live calls to verify_token and {num:open_tickets} tickets still open.",
             "{num:open_tickets} open tickets cover the {num:present} remaining sites, with gaps."]},
    {"name": "coverage",
     "facts": {"mapped": 6, "tickets": 7, "share": 0.857},
     "say": ["{num:mapped} of {num:tickets} tickets map to code ({pct:share}).",
             "Ticket coverage is {pct:share}: {num:mapped} mapped, out of {num:tickets}.",
             "{pct:share} of tickets point at a call site.",
             "Of {num:tickets} tickets, {num:mapped} are linked to code."]},
    {"name": "consumer checkout",
     "facts": {"team": "checkout", "done": 31, "total": 52, "share": 0.596, "status": "STALLED"},
     "say": ["{team:team}: {num:done} of {num:total} done. STALLED.",
             "{team:team} has finished {num:done} of {num:total} call sites ({pct:share}) and has stalled.",
             "Progress for {team:team}: {pct:share}, {num:done} done, {num:total} in all. No commits lately.",
             "{num:done}/{num:total} done for {team:team}; the rest haven't moved."]},
    {"name": "blocked fulfillment",
     "facts": {"team": "fulfillment", "blocker": "platform", "ticket": "FUL-7", "blocker_ticket": "PLAT-2291",
               "site": {"path": "shared/client.py", "line": 40}},
     "say": ["{team:team} is blocked: its call goes through {team:blocker}'s client at {path:site}. Waiting on "
             "{ticket:blocker_ticket}.",
             "{ticket:ticket} can't finish until {team:blocker} lands {ticket:blocker_ticket}.",
             "Blocked by {team:blocker}: {path:site} is untouched, and {team:team}'s {ticket:ticket} depends on it.",
             "{team:team} waits on {ticket:blocker_ticket} ({team:blocker}) before {ticket:ticket} can close."]},
    {"name": "stall checkout",
     "facts": {"team": "checkout", "path": "cart/pricing.py", "bug_fixes": 4, "last": "2026-07-01", "sha": SHA},
     "say": ["{team:team}'s top author on {path:path} last committed {date:last}. The file has {num:bug_fixes} "
             "bug fixes.",
             "Likely stall reason: nobody has touched {path:path} since {date:last} (commit {sha:sha}).",
             "{path:path} is fragile ({num:bug_fixes} bug fixes) and quiet since {date:last}.",
             "Last change to {path:path}: {sha:sha}, on {date:last}. {team:team} is busy elsewhere."]},
    {"name": "payments closed",
     "facts": {"team": "payments", "ticket": "PAY-10", "removed": 2,
               "s0": {"path": "pay/charge.py", "line": 5}, "s1": {"path": "pay/charge.py", "line": 9}},
     "say": ["{ticket:ticket} is closed Fixed, but {path:s0} still calls verify_token.",
             "{team:team} removed {num:removed} calls, yet {ticket:ticket} was closed with {path:s0} and {line:s1} "
             "still there.",
             "Reopen {ticket:ticket}? {path:s0} and {path:s1} are live.",
             "{team:team}: {num:removed} calls gone, {ticket:ticket} closed, {path:s1} left."]},
    {"name": "search done",
     "facts": {"team": "search", "removed": 2, "s0": {"path": "search/q.py", "line": 1},
               "s1": {"path": "search/q.py", "line": 7}},
     "say": ["{team:team} is done: {num:removed} calls removed, {path:s0} and {line:s1}.",
             "Nothing left for {team:team}. Both calls ({path:s0}, {path:s1}) are gone.",
             "{team:team} removed all {num:removed} of its calls.",
             "Done: {team:team}, {num:removed} of {num:removed}."]},
    {"name": "platform client",
     "facts": {"team": "platform", "ticket": "PLAT-2291", "site": {"path": "shared/client.py", "line": 40},
               "since": "2026-01-10", "rows": 1526, "reach": 0.712},
     "say": ["{team:team}'s {path:site} hasn't changed since {date:since}. {ticket:ticket} is open.",
             "Fixing {path:site} unblocks the most: it reaches {num:rows} lines ({pct:reach} of the callers).",
             "{ticket:ticket}: {team:team} owns {path:site}, untouched since {date:since}.",
             "Blast radius of {path:site}: {num:rows} lines, {pct:reach} of callers. Start with {ticket:ticket}."]},
]

# Sentences with no facts, added to about half the texts. One uses a team name as a plain word on purpose: an LLM
# will write that, and decision 1 has to count what it costs.
FILLERS = ["Please confirm before merging.", "Reply here if the owner is wrong.", "This is a suggestion only.",
           "The new API takes the same arguments.", "Tests should pass unchanged.", "Ping the owners if unsure.",
           "Keep the same error handling across the platform.", "No other files need to change.",
           "Run the suite after the change.", "This came from the nightly map."]

_SLOT = re.compile(r"\{(num|pct|path|line|team|ticket|date|sha):(\w+)\}")
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _path_line(v):
    if isinstance(v, dict):
        return v["path"], v["line"]
    if ":" not in v:
        return v, None  # a file, no line
    p, l = v.rsplit(":", 1)
    return p, int(l)


def _fmt(kind, v, rng):
    """One way to write a fact, picked by the seeded rng: commas or not, a tail path, a month-name date."""
    if kind == "num":
        return f"{v:,}" if v >= 1000 and rng.random() < 0.5 else str(v)
    if kind == "pct":
        return f"{v * 100:.0f}%" if rng.random() < 0.6 else f"{v * 100:.1f}%"
    if kind == "path":
        p, l = _path_line(v)
        p = p.split('/')[-1] if rng.random() < 0.25 else p
        return p if l is None else f"{p}:{l}"
    if kind == "line":
        return f":{_path_line(v)[1]}"
    if kind == "date":
        y, m, d = map(int, v.split("-"))
        return v if rng.random() < 0.5 else f"{_MON[m - 1]} {d}"
    return str(v)


def _render(phrase, facts, rng, swap=None):
    """Fill the slots. `swap` = (slot index, text) puts that text in one slot instead of its fact."""
    out, i = [], 0
    for n, m in enumerate(_SLOT.finditer(phrase)):
        out.append(phrase[i:m.start()])
        out.append(swap[1] if swap and swap[0] == n else _fmt(m.group(1), facts[m.group(2)], rng))
        i = m.end()
    out.append(phrase[i:])
    return "".join(out)


def _values(facts):
    """Every number, path:line, team and ticket in a task's facts, to keep an injected value out of them."""
    nums, paths, words = set(), set(), set()
    for k, v in facts.items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            nums.add(v)
        elif isinstance(v, dict) and "path" in v:
            paths.add(f"{v['path']}:{v['line']}")
            nums.update(x for x in v.values() if isinstance(x, int))
        elif isinstance(v, str):
            words.add(v)
            if re.search(r"\.py:\d+$", v):
                paths.add(v)
    return nums, paths, words


def build():
    """(clean, injected): lists of dicts with text, facts, task, and kind for the injected ones."""
    rng = random.Random(SEED)
    clean, seen = [], set()
    i = 0
    while len(clean) < 200:
        task = TASKS[i % len(TASKS)]
        i += 1
        phrase = rng.choice(task["say"])
        filler = " " + rng.choice(FILLERS) if rng.random() < 0.5 else ""
        text = _render(phrase, task["facts"], rng) + filler
        if text in seen:
            continue
        seen.add(text)
        clean.append({"text": text, "facts": task["facts"], "task": task["name"], "phrase": phrase,
                      "filler": filler})

    injected = []
    for kind in KINDS:
        n = 0
        for c in rng.sample(clean, len(clean)):
            if n == 40:
                break
            t = _inject(kind, c, rng)
            if t and t != c["text"]:
                injected.append({"text": t, "facts": c["facts"], "task": c["task"], "kind": kind, "clean": c["text"]})
                n += 1
        assert n == 40, f"only {n} {kind} texts"
    return clean, injected


def _inject(kind, c, rng):
    facts, phrase = c["facts"], c["phrase"]
    nums, paths, words = _values(facts)
    slots = [(n, m.group(1), m.group(2)) for n, m in enumerate(_SLOT.finditer(phrase))]

    def slot_of(*kinds):
        hits = [s for s in slots if s[1] in kinds]
        return rng.choice(hits) if hits else None

    def rerender(swap):
        return _render(phrase, facts, rng, swap) + c["filler"]

    if kind == "wrong_number":
        s = slot_of("num", "pct")
        if s is None:
            return rerender(None) + f" That leaves {rng.choice([3, 6, 11, 17])} sites."
        v = facts[s[2]]
        if s[1] == "pct":
            return rerender((s[0], f"{round(v * 100) + rng.choice([-9, -4, 3, 7, 12])}%"))
        bad = v + rng.choice([1, 2, 3, 5, 9, 40])
        while bad in nums:
            bad += 1
        return rerender((s[0], f"{bad:,}" if bad >= 1000 else str(bad)))
    if kind == "made_up_path":
        s = slot_of("path", "line")
        if s is None or rng.random() < 0.3:
            return rerender(None) + f" It also touches {rng.choice(FAKE_PATHS)}."
        p, l = _path_line(facts[s[2]])
        if l is None:
            return rerender((s[0], rng.choice(FAKE_PATHS).split(":")[0]))
        bad = l + rng.choice([1, 2, 10])
        while f"{p}:{bad}" in paths:
            bad += 1
        return rerender((s[0], f":{bad}" if s[1] == "line" else f"{p}:{bad}"))
    if kind == "swapped_team":
        other = rng.choice([t for t in TEAMS if t not in words])
        s = slot_of("team")
        return rerender((s[0], other)) if s else rerender(None) + f" Ask {other} to review."
    if kind == "fake_ticket":
        s = slot_of("ticket")
        base = facts[s[2]] if s else rng.choice(TICKETS)
        proj, num = base.split("-")
        fake = f"{proj}-{int(num) + rng.choice([1, 3, 17, 50])}"
        while fake in TICKETS:
            fake = f"{proj}-{int(fake.split('-')[1]) + 1}"
        return rerender((s[0], fake)) if s else rerender(None) + f" Tracked in {fake}."
    if kind == "other_task":
        pool = []
        for t in TASKS:
            if t["facts"] is facts:
                continue
            o_nums, o_paths, o_words = _values(t["facts"])
            pool += [f"It also touches {p}." for p in o_paths - paths]
            pool += [f"See {w}." for w in o_words if re.fullmatch(r"[A-Z]+-\d+", w) and w not in words]
            pool += [f"That makes {x:,} in all." for x in o_nums if isinstance(x, int) and x > 1 and x not in nums]
        return rerender(None) + " " + rng.choice(sorted(pool))
    raise ValueError(kind)


def score(clean, injected, **kw):
    """Catch rate on the injected texts, false-reject rate on the clean ones, and misses per injected kind."""
    def flagged(text, facts):
        return {(m.kind, m.token) for m in check(text, facts, teams=TEAMS, people=PEOPLE, **kw).misses}

    # caught only when the gate flags something its clean text doesn't: a filler the gate already rejects
    # in the clean text doesn't count as catching the injected error
    caught = {k: 0 for k in KINDS}
    for t in injected:
        caught[t["kind"]] += bool(flagged(t["text"], t["facts"]) - flagged(t["clean"], t["facts"]))
    rejected = [t for t in clean if not check(t["text"], t["facts"], teams=TEAMS, people=PEOPLE, **kw)]
    return {"caught": sum(caught.values()), "injected": len(injected), "by_kind": caught,
            "false_rejects": len(rejected), "clean": len(clean), "rejected": rejected}


def main():
    clean, injected = build()
    print(f"{len(clean)} clean, {len(injected)} injected (seed {SEED})\n")
    print("Decision 1: kinds (numbers = round)")
    for kinds in KIND_SETS:
        r = score(clean, injected, kinds=kinds, numbers="round")
        per = ", ".join(f"{k} {v}/40" for k, v in r["by_kind"].items())
        print(f"  {kinds:8} caught {r['caught']}/{r['injected']}  false rejects {r['false_rejects']}/{r['clean']}"
              f"  [{per}]")
    print("\nDecision 2: numbers (kinds = teams)")
    for mode in NUMBER_MODES:
        r = score(clean, injected, kinds="teams", numbers=mode)
        print(f"  {mode:8} caught {r['caught']}/{r['injected']}  false rejects {r['false_rejects']}/{r['clean']}")
    r = score(clean, injected)
    if r["rejected"]:
        print("\nClean texts the default gate rejects:")
        for t in r["rejected"]:
            print("  -", t["text"])


if __name__ == "__main__":
    main()
