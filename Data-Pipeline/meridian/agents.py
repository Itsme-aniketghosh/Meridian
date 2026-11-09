"""The writer agent (04-architecture §6, Contract 5): text from a block's facts. Rules decide the facts; it only writes.

    python -m meridian.agents --run-id <run_id>                   # LLM off: every text is its template
    python -m meridian.agents --run-id R2 --db /tmp/fixture.sqlite --write --track

Four tasks, each one facts object per target, from KNOWLEDGE-BASE.md's queries:
    rewrite        a vague ticket (staff_vague), rewritten from manager_facts            suggestion kind rewrite
                   (one per ticket, across every symbol its sites call)
    ticket_draft   one draft per team and symbol for its untracked call sites            suggestion kind ticket_draft
                   (maker_drafts)
    stall_reason   a stalled team's reasons, in the order the auditor's served policy ranks them
                                                                                         suggestion kind stall_reason
    consumer       the consumer view's text for a team with sites left (consumer_*)      no suggestion row: schema.sql
                                                                                         has no kind for it yet

Every task has a template, filled only from its facts. With an LLM, each answer is:
    cache or call -> JSON {"text", "used"} -> fact_gate.check -> ship it
and bad JSON or a gate miss moves one model up the ladder (small, mid, large: 04 §6), then ships the template. The
writer never strips a bad part out. With no LLM (no key, as in CI), the cache still answers; on a miss the template
ships, or with strict_cache the run stops (PASS-BARS §6.5: a cache miss in CI fails the job).

A template should pass the gate, and on the fixture every one does (tests/test_agents.py). On a real block a fixed
word in a template can also be a team's name (teams named Bug or Tests), so a template the gate flags still ships
(it can only say facts), and its misses are counted in the run's report: they're what an LLM text would also be
rejected for. A task whose facts name a person is never written at all, and is counted too.

Settings (vishwa-agents decisions, the writer, Nov 9):
- The ladder is LADDER, constants here, not meridian.toml [llm]: a new section there would change every block's
  config hash and run_id. Fixed rules pick the model (route 'rules') until Jev routes (Rachna).
- The cache keeps the raw reply and gates it again every run (llm_cache.py). A reply that names a person is never
  written to it: the entry keeps a redacted marker instead, so a rerun still makes 0 calls.
- A prompt's version is a hash of everything that frames it: the system prompt, the task prompt, the layout and the
  facts format. Any edit is a new version, so the cache never serves an answer written for another prompt.

Every LLM answer, cached or called, goes through `log` (model.tracking.log_llm_call when run with --track): counts
and labels only, never the prompt or the text. A template that ships without an LLM attempt isn't a call and isn't
logged; when the last attempt fails, its row says source 'template'. The fallback counts go in the run's report.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from meridian import auditor as A
from meridian.common import CONFIG, h, handoff, load_config, log as say, write_jsonl
from meridian.fact_gate import _EMAIL, _words, check
from meridian.llm_cache import Cache, canonical, key
from meridian.pushcheck import kb_queries

AGENT = "writer"
SMALL, MID, LARGE = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"
LADDER = (SMALL, MID, LARGE)  # 04 §6 and rachna-models README: the writer is general small -> mid -> large
ROUTE = "rules"               # who picked the model: the fixed ladder, until Jev
KINDS = ("rewrite", "ticket_draft", "stall_reason", "consumer")
ROW_KIND = {"rewrite": "rewrite", "ticket_draft": "ticket_draft", "stall_reason": "stall_reason", "consumer": None}
# the templates' fixed product phrases that can share a word with a team (the real block has a team named Bug):
# the gate reads them as words, for the templates and the LLM's text alike (02-product: "Bug fixes", never "incidents")
VOCAB = ("bug fix", "bug fixes")
REDACTED = {"redacted": "a person"}  # a cached reply that named a person, kept as this so a rerun makes 0 calls

SYSTEM = """You write one short text for a tool that tracks a code migration across teams.
Use only the facts below. Every number, file path, line, date, ticket key and team name you write must be one of
the facts, written the same way. Never name or address a person. Use team names only to name teams, never as
ordinary words (write "across the codebase", not "across the platform"). Don't add priority, intent, likelihood,
deadlines or anything the facts don't say. Reply with JSON only, no other text:
{"text": "<the text>", "used": ["<fact name>", ...]}"""

PROMPTS = {
    "rewrite": "Rewrite the ticket so its owners know what to change and when it's done. Keep the ticket key. List "
               "the call sites with each one's owning team, end with a done condition, and ask about any call inside "
               "an except block.",
    "ticket_draft": "Draft a new ticket for the team's call sites that no ticket covers: what to migrate, where, and "
                    "when it's done.",
    "stall_reason": "Say that the team's migration of the symbol has had no change since the given date and how many "
                    "call sites are left, then list the possible reasons in the order given, each with how much "
                    "evidence it has. Don't pick one or say which is likelier.",
    "consumer": "Tell the team what it still has to migrate: how many call sites, where, how many bug fixes each file "
                "had (with their ticket keys), what blocks them and whom they block.",
}
FACTS_FORMAT = "json, sorted keys, indent 1"  # decision 4 may change this: it's part of the prompt version
FRAME = "{system}\n\nTask: {task}\n\nFacts:\n{facts}"


def _facts_text(facts):
    return json.dumps(facts, sort_keys=True, indent=1, ensure_ascii=False)


def prompt_version(kind):
    """12 hex of sha256 over the system prompt, the task's prompt, the frame and the facts format."""
    parts = (SYSTEM, PROMPTS[kind], FRAME, FACTS_FORMAT)
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def prompt(task):
    return FRAME.format(system=SYSTEM, task=PROMPTS[task.kind], facts=_facts_text(task.facts["facts"]))


def facts_hash(facts):
    """16 hex of sha256 over the facts (sorted keys): the suggestion row's facts_hash, all the text could say."""
    return hashlib.sha256(canonical(facts["facts"]).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Task:
    kind: str
    target_kind: str   # suggestion.target_kind
    target: str
    owner_team: str
    repo: str
    facts: dict        # Contract 5: {"facts": {...}, "source": {...}}
    template: str
    policy_id: str = None


@dataclass
class Written:
    task: Task
    text: str
    source: str        # 'llm' or 'template'
    model: str         # None for a template
    facts_hash: str
    attempts: list = field(default_factory=list)       # (model, cache_hit, outcome): ok, bad_json, gate_miss
    template_misses: tuple = ()                        # what the gate flags in the template, on a real block


def _n(count, word, plural=None):
    return f"{count} {word if count == 1 else plural or word + 's'}"


def _sites(sites):
    """'cart/pricing.py:214, :231, cart/totals.py:57': a path once, then its other lines (02-product)."""
    out, last = [], None
    for s in sites:
        out.append(f":{s['line']}" if s["path"] == last else f"{s['path']}:{s['line']}")
        last = s["path"]
    return ", ".join(out)


def _area(paths):
    """The directory every path sits under, with its '/', or '' when they share none."""
    dirs = [p.split("/")[:-1] for p in paths]
    common = []
    for parts in zip(*dirs):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return "/".join(common) + "/" if common else ""


def _join(words):
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


# ---- the four tasks: facts and template ----

def rewrite_tasks(found, repo):
    """found: {ticket key: (problem, {symbol: [manager_facts rows]})}, gathered across the repo's symbols."""
    out = []
    for k, (problem, by_symbol) in sorted(found.items()):
        sites = sorted(({"path": p, "line": ln, "symbol": sym, "owner_team": team, "in_except": bool(exc)}
                        for sym, rows in by_symbol.items() for p, ln, team, _, exc in rows),
                       key=lambda s: (s["path"], s["line"], s["symbol"]))
        symbols = sorted(by_symbol)
        owners = Counter(s["owner_team"] for s in sites)
        team = sorted(owners, key=lambda t: (-owners[t], t))[0]  # the row's owner: the team with the most sites
        n, exc = len(sites), sum(s["in_except"] for s in sites)
        area = _area([s["path"] for s in sites])
        facts = {"key": k, "problem": problem, "symbols": symbols, "sites": sites, "sites_n": n,
                 "in_except_n": exc, "owners": sorted(owners)}
        if area:
            facts["area"] = area
        lines = [f"{k}: Migrate {area + ' ' if area else ''}off {_join(symbols)}"]
        for t in sorted(owners):  # one line per owning team: a cross-team ticket names every owner
            lines.append(f"- {_sites([s for s in sites if s['owner_team'] == t])} · owner {t}")
        lines.append(f"- Done when no site above calls {_join(symbols)}")
        if exc:
            lines.append("- Question: the call sits inside an except block. Keep the same handling?" if n == 1 else
                         f"- Question: {exc} of the {n} calls {'sits' if exc == 1 else 'sit'} inside an except block. "
                         "Keep the same handling?")
        source = {"key": f"ticket:{k}", "sites": f"manager_facts:{k}"}
        out.append(Task("rewrite", "ticket", k, team, repo, {"facts": facts, "source": source}, "\n".join(lines)))
    return out


def draft_tasks(db, q, repo, symbol):
    out = []
    for team, count, sites in db.execute(q["maker_drafts"]).fetchall():
        facts = {"team": team, "symbol": symbol, "count": count, "sites": sites}
        text = "\n".join([f"Draft ticket for {team}: migrate {_n(count, 'untracked call site')} off {symbol}",
                          f"- {sites}", f"- No ticket covers {'this site' if count == 1 else 'these sites'} yet",
                          f"- Done when {'it no longer calls' if count == 1 else 'none of them calls'} {symbol}"])
        out.append(Task("ticket_draft", "team", team, team, repo,
                        {"facts": facts, "source": {"sites": f"maker_drafts:{team}:{symbol}"}}, text))
    return out


def stall_tasks(db, cfg, run_id, at):
    """The stall job's open stalls, each in the order of the policy it serves (auditor.stall_job), as counts. The
    facts hold the stall's start, never its age, so a stall's text and row stay the same while it lasts."""
    s = A.settings(cfg)
    job = A.stall_job(db, cfg, s, run_id, at, None, A.match_log(db, at))
    known = {r["policy_id"] for r in A.policy_log(db)}
    policies = {p: x for p, x in A.POLICIES.items() if x.job == "stall"}
    out = []
    for x in job.open:
        pid = job.served[x.task_id]
        ranked = policies[pid].rank(x) if pid in policies else sorted(x.facts)
        reasons = [{"reason": A.REASONS[n][0], "evidence": len(x.facts[n])} for n in ranked]
        facts = {"team": x.team, "symbol": x.symbol, "since": x.since, "left": x.left, "reasons": reasons}
        lines = [f"{x.team.title()} has made no change to its {x.symbol} call sites since {x.since[:10]}, "
                 f"with {_n(x.left, 'call site')} left."]
        if reasons:
            lines.append("Possible reasons, in the auditor's order:")
            lines += [f"- {r['reason']} ({_n(r['evidence'], 'piece', 'pieces')} of evidence)" for r in reasons]
        else:
            lines.append("No stall reason fires.")
        out.append(Task("stall_reason", "team", x.team, x.team, x.repo or "",
                        {"facts": facts, "source": {"stall": f"stall:{x.task_id}", "policy": pid}},
                        "\n".join(lines), pid if pid in known else None))
    return out


def consumer_tasks(db, q, repo, symbol):
    out = []
    for team, left, *_ in db.execute(q["owner_teams"]).fetchall():
        if not left:
            continue
        files = []
        for path, n, bug in db.execute(q["consumer_files"], {"team": team}).fetchall():
            keys = db.execute("SELECT fix_keys FROM file_bug_fixes WHERE repo = ? AND path = ? "
                              "ORDER BY window_end DESC LIMIT 1", (repo, path)).fetchone()
            files.append({"path": path, "sites": n, "bug_fixes": bug,
                          "fix_keys": sorted(json.loads(keys[0])) if keys and bug else []})
        blocked = defaultdict(int)
        for _, _, by_team, by_path in db.execute(q["consumer_blocked"], {"team": team}).fetchall():
            blocked[(by_team, by_path)] += 1
        blocked_by = [{"team": t, "path": p, "sites": n} for (t, p), n in sorted(blocked.items())]
        blocking = [{"team": t, "sites": n} for t, n in db.execute(q["consumer_blocking"], {"team": team})]
        facts = {"team": team, "left": left, "symbol": symbol, "files": files, "blocked_by": blocked_by,
                 "blocking": blocking}
        lines = [f"{team.title()}, {_n(left, symbol + ' call site')} remaining."]
        for f in files:
            fixes = (f", {_n(f['bug_fixes'], 'bug fix', 'bug fixes')} in the last year ({', '.join(f['fix_keys'])})"
                     if f["bug_fixes"] else "")
            lines.append(f"- {f['sites']} in {f['path']}{fixes}")
        lines += [f"- {b['sites']} blocked by {b['path']}, which {b['team']} hasn't migrated" for b in blocked_by]
        lines += [f"- You are blocking {b['team']} ({_n(b['sites'], 'call site')})" for b in blocking]
        out.append(Task("consumer", "team", team, team, repo,
                        {"facts": facts, "source": {"files": f"consumer_files:{team}:{symbol}"}}, "\n".join(lines)))
    return out


def tasks(db, cfg, run_id, at, q=None):
    """Every writer task in the block as of `at`, in a fixed order. Drafts, stalls and consumer texts are per
    (repo, symbol), as the views show them; a rewrite is per ticket, across all the symbols its sites call."""
    q = q or kb_queries()
    out = stall_tasks(db, cfg, run_id, at)  # first: the stall job moves asof itself
    vague = defaultdict(dict)  # repo -> key -> (problem, {symbol: rows})
    for repo, symbol in db.execute("SELECT DISTINCT repo, symbol FROM call_site ORDER BY repo, symbol").fetchall():
        A.point(db, at, repo, symbol, run_id, q)
        for k, problem in db.execute(q["staff_vague"]).fetchall():
            rows = db.execute(q["manager_facts"], {"key": k}).fetchall()
            if rows:
                vague[repo].setdefault(k, (problem, {}))[1][symbol] = rows
        out += draft_tasks(db, q, repo, symbol) + consumer_tasks(db, q, repo, symbol)
    for repo, found in sorted(vague.items()):
        out += rewrite_tasks(found, repo)
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(out, key=lambda t: (order[t.kind], t.repo, t.target, facts_hash(t.facts)))


def names(db):
    """(teams, people): the gate's known names from the block. Teams: team_map.team and every owner_team. People:
    every person who isn't a bot and has at least one alias that isn't generic (a placeholder such as 'test' isn't
    anyone's name)."""
    teams = {r[0] for r in db.execute("SELECT team FROM team_map UNION SELECT owner_team FROM call_site") if r[0]}
    people = {r[0] for r in db.execute(
        "SELECT p.name FROM person p WHERE NOT p.is_bot AND EXISTS (SELECT 1 FROM person_alias a "
        "WHERE a.person_id = p.person_id AND NOT a.generic)") if r[0]}
    return sorted(teams), sorted(people)


# ---- writing one task ----

class CacheMiss(RuntimeError):
    pass


class NamesAPerson(ValueError):
    pass


_FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.S)


def parse(reply, facts):
    """The text of a reply that is {"text": str, "used": [fact names]}, else None (bad JSON). A reply in one
    ```json fence is read inside it."""
    if not isinstance(reply, str):
        return None
    fenced = _FENCE.match(reply.strip())
    try:
        obj = json.loads(fenced.group(1) if fenced else reply)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("text"), str) or not obj["text"].strip():
        return None
    used = obj.get("used")
    if not isinstance(used, list) or not all(isinstance(u, str) and u in facts["facts"] for u in used):
        return None
    return obj["text"].strip()


def names_a_person(text, people):
    people_re = _words(people)
    return bool(_EMAIL.search(text) or (people_re and people_re.search(text)))


def write(task, *, teams, people, llm=None, cache=None, log=None, strict_cache=False):
    """One task's text. llm(model, prompt) -> {"reply", "tokens_in", "tokens_out", "cost_usd"}, or None for off.
    Raises NamesAPerson when the task's facts name a person: such a task is never written."""
    fh = facts_hash(task.facts)
    try:
        own = check(task.template, task.facts, teams=teams, people=people, vocab=VOCAB)
    except ValueError as e:
        raise NamesAPerson(f"{task.kind} {task.target}: {e}") from None
    pv = prompt_version(task.kind)
    done = Written(task, task.template, "template", None, fh, template_misses=own.misses)
    ladder = LADDER if (llm or cache) else ()
    for i, model in enumerate(ladder):
        k = key(model, pv, task.facts["facts"])
        entry = cache.get(k) if cache else None
        hit, t0 = entry is not None, time.perf_counter()
        if not hit:
            if llm is None:
                if strict_cache:
                    raise CacheMiss(f"no llm_cache entry for {task.kind} {task.target} on {model} ({k})")
                break
            r = llm(model, prompt(task))
            entry = {"model": model, "prompt_version": pv, "facts_hash": fh, "reply": r["reply"],
                     "tokens_in": int(r.get("tokens_in", 0)), "tokens_out": int(r.get("tokens_out", 0)),
                     "cost_usd": float(r.get("cost_usd", 0.0))}
            if names_a_person(str(r["reply"]), people):  # never into the committed cache
                entry = {**entry, "reply": None, **REDACTED}
            if cache:
                cache.put(k, entry)
        text = parse(entry.get("reply"), task.facts)
        passed = text is not None and check(text, task.facts, teams=teams, people=people, vocab=VOCAB).passed
        done.attempts.append((model, hit, "ok" if passed else "bad_json" if text is None else "gate_miss"))
        last = passed or i == len(ladder) - 1 or (
            llm is None and cache.get(key(ladder[i + 1], pv, task.facts["facts"])) is None)
        source = "llm" if passed else "template" if last else None  # what shipped, on the row that decided it
        _log(log, task, fh, model, pv, entry, hit, None if text is None else passed, source,
             (time.perf_counter() - t0) * 1000)
        if passed:
            done.text, done.source, done.model = text, "llm", model
            return done
    return done


def _log(log, task, fh, model, pv, entry, hit, gate_pass, source, latency_ms):
    if log is None:
        return
    paid = not hit
    log(agent=AGENT, model=model, prompt_version=pv, tokens_in=entry["tokens_in"] if paid else 0,
        tokens_out=entry["tokens_out"] if paid else 0, cost_usd=entry["cost_usd"] if paid else 0.0,
        cache_hit=hit, gate_pass=gate_pass, route=ROUTE, source=source, task=task.kind, repo=task.repo or None,
        team=task.owner_team, latency_ms=round(latency_ms, 3), facts_hash=fh)


def suggestion_row(w, run_id, at):
    """The suggestion row for a written task (Contract 5), or None for a kind schema.sql has no row for."""
    kind = ROW_KIND[w.task.kind]
    if kind is None:
        return None
    t = w.task
    return {"run_id": run_id, "suggestion_id": h(kind, t.target, w.facts_hash), "kind": kind,
            "target_kind": t.target_kind, "target": t.target, "owner_team": t.owner_team, "source": w.source,
            "body": w.text, "facts_hash": w.facts_hash, "offered_at": at, "agent": AGENT, "model": w.model,
            "route": ROUTE, "policy_id": t.policy_id}


@dataclass
class Run:
    written: list     # Written, one per task that was written
    rows: list        # the block's suggestion rows so far plus the new ones, for out/<run_id>/suggestion.jsonl
    skipped: list     # (kind, target, why): tasks never written because their facts name a person

    def __iter__(self):  # written, rows = run(...)
        return iter((self.written, self.rows))


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()


def run(db, cfg, run_id, at, *, llm=None, cache=None, log=None, strict_cache=False):
    """Every task written. Rows are offered once: a suggestion_id already in the block isn't offered again. A block
    from before schema v1.3 has no suggestion table, so no rows so far."""
    teams, people = names(db)
    written, skipped = [], []
    for t in tasks(db, cfg, run_id, at):
        try:
            written.append(write(t, teams=teams, people=people, llm=llm, cache=cache, log=log,
                                 strict_cache=strict_cache))
        except NamesAPerson as e:
            skipped.append((t.kind, t.target, str(e)))
    before = []
    if _table(db, "suggestion"):
        cols = [r[1] for r in db.execute("PRAGMA table_info(suggestion)")]
        before = [dict(zip(cols, r)) for r in db.execute("SELECT * FROM suggestion")]
    seen = {r["suggestion_id"] for r in before}
    rows = list(before)
    for w in written:
        row = suggestion_row(w, run_id, at)
        if row and row["suggestion_id"] not in seen:
            seen.add(row["suggestion_id"])
            rows.append(row)
    return Run(written, sorted(rows, key=lambda r: (r["run_id"], r["suggestion_id"])), skipped)


def report(result):
    """The run's writer report (04 §7, the fallback rate): per task kind, texts, templates, templates the gate
    flags, and tasks skipped because a fact names a person."""
    out = defaultdict(lambda: {"texts": 0, "templates": 0, "template_gate_misses": 0, "skipped_person": 0})
    for w in result.written:
        r = out[w.task.kind]
        r["texts"] += 1
        r["templates"] += w.source == "template"
        r["template_gate_misses"] += bool(w.template_misses)
    for kind, _, _ in result.skipped:
        out[kind]["skipped_person"] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def fallback_counts(written):
    """{kind: (templates, texts)}."""
    out = defaultdict(lambda: [0, 0])
    for w in written:
        out[w.task.kind][0] += w.source == "template"
        out[w.task.kind][1] += 1
    return {k: tuple(v) for k, v in sorted(out.items())}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True, help="the block to read: out/<run_id>/meridian.sqlite")
    ap.add_argument("--config", type=Path, default=CONFIG, help="meridian.toml")
    ap.add_argument("--db", type=Path, help="read this SQLite file instead of the run's block")
    ap.add_argument("--at", help="as of this time (default: the run's snapshot_at). Not with --write")
    ap.add_argument("--write", action="store_true", help="write suggestion.jsonl and writer_report.json")
    ap.add_argument("--out", type=Path, help="where --write puts them (default: out/<run_id>/)")
    ap.add_argument("--cache-dir", type=Path, help="the LLM cache (default: Data-Pipeline/llm_cache/)")
    ap.add_argument("--strict-cache", action="store_true", help="a cache miss stops the run (CI)")
    ap.add_argument("--track", action="store_true", help="log every LLM answer to MLflow meridian-agents")
    ap.add_argument("--quiet", action="store_true", help="print the report only, not every text")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    path = Path(a.db or handoff(cfg, a.run_id) / "meridian.sqlite")
    if not path.exists():
        raise SystemExit(f"no block at {path}: run the writer first, or pass --db")
    cache = Cache(a.cache_dir) if a.cache_dir else Cache()
    tracked, log = nullcontext(), None
    if a.track:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Model-Pipeline"))
        from model import tracking
        tracked = tracking.start_run(AGENT, owner="vishwa", experiment=tracking.AGENTS, block_run_id=a.run_id)
        log = tracking.log_llm_call
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db, tracked:
        run_row = db.execute("SELECT snapshot_at FROM run WHERE run_id = ?", (a.run_id,)).fetchone()
        if not (run_row or a.at):
            raise SystemExit(f"{path} has no run {a.run_id}: pass --at")
        if a.write and a.at and (not run_row or a.at != run_row[0]):
            raise SystemExit("--write offers rows at the run's snapshot_at (schema.sql): leave out --at")
        at = a.at or run_row[0]
        # no LLM client yet: the LLM is off, and the cache answers what it has
        result = run(db, cfg, a.run_id, at, cache=cache, log=log, strict_cache=a.strict_cache)
    rep = report(result)
    for kind, r in rep.items():
        say(f"{kind}: {r['texts']} texts, {r['templates']} templates, {r['template_gate_misses']} templates the gate "
            f"flags, {r['skipped_person']} skipped (a fact names a person)")
    if not a.quiet:
        for w in result.written:
            say(f"\n[{w.task.kind} · {w.task.target} · {w.source}]\n{w.text}")
    if a.write:
        out = a.out or handoff(cfg, a.run_id)
        out.mkdir(parents=True, exist_ok=True)
        write_jsonl(out / "suggestion.jsonl", result.rows, key=("run_id", "suggestion_id"))
        (out / "writer_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8",
                                                newline="\n")
        say(f"\nwrote {len(result.rows)} suggestion rows and writer_report.json to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
