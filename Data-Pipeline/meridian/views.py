"""Views and API (Contract 6): the four screens of 02-product and the ticket tools, read-only, from one block.

    python -m meridian.views --run-id <run_id> --serve 8765                 # out/<run_id>/meridian.sqlite
    python -m meridian.views --run-id 70dd1dabfed544b8 --db ../out/70dd1dabfed544b8/meridian.sqlite --serve 8765
    python -m meridian.views --fixture --serve 8765                         # acme/shop, KNOWLEDGE-BASE.md's fixture

    /                     the staff view            /api/staff
    /owner/<symbol>       the API owner view        /api/owner/<symbol>
    /consumer/<team>      the consumer view         /api/consumer/<team>
    /push/<sha>           the push-check notes      /api/push/<sha>       (/push lists the pushes)
    /tickets              the ticket tools          /api/tickets
    ?repo=&symbol=        which (repo, symbol) a view shows; the default is the one with the most call sites

Built on tests/dashboard.py's pattern: the standard library, no web framework, served on 127.0.0.1 only, GET only.
The block is opened read-only and every number comes from KNOWLEDGE-BASE.md's named queries (rows) or from the
writer's texts, whose numbers the fact gate checked against rows. A view is a dict of sections, and the page and its
JSON are both rendered from that dict, so a page can't show a number its JSON doesn't have (tests/test_views.py).

Every page and JSON response carries run_id, snapshot_at, the block's coverage rows and whether the numbers come
from a real block or the fixture. Decision 8: each view comes from the real block where its tables have rows there,
else from the fixture, with a banner that says why (Site; --real-only turns this off, --fixture shows the fixture).

The writer's text is the block's suggestion row where one exists for the same facts, else the text built from those
facts on the spot: the committed llm_cache's reply if it passes the gate again, else the template (the LLM is never
called here, and the cache is only read). Diffs are the block's diff and legacy suggestion rows, as stored. The push
page shows the P1-P9 notes stored in push_alert: no checkout, nothing recomputed.

As of the run's snapshot, everywhere: call sites are site_now's, suggestion rows are those offered by then, a push
shows only if its base run is known by then, and an outcome decided after the snapshot shows as open.

One block is one SQLite connection, and A.point() aims its temp tables at one (repo, symbol) per request, so the
server answers one request at a time (a lock): parallel requests would re-aim each other's queries.
"""
import argparse
import html
import http.server
import json
import sqlite3
import sys
import threading
import traceback
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from meridian import agents as G
from meridian import auditor as A
from meridian.common import CONFIG, h, handoff, load_config, log
from meridian.llm_cache import Cache
from meridian.pushcheck import NAMES as CHECKS, kb_queries
from meridian.writer import missing_indexes

VIEWS = ("staff", "owner", "consumer", "push", "tickets")
FIXTURE_NOTE = "fixture acme/shop (KNOWLEDGE-BASE.md): a made-up repo that tells 02-product's story, not real data"


class NotFound(LookupError):
    pass


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()


class Block:
    """One block, read once: its run, its (repo, symbol) pairs and the writer's texts. The views read only this."""

    def __init__(self, db, cfg, run_id, fixture=False, cache=None):
        self.db, self.cfg, self.q, self.fixture, self.cache = db, cfg, kb_queries(), fixture, cache
        row = db.execute("SELECT run_id, snapshot_at, status FROM run WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise SystemExit(f"the block has no run {run_id}")
        self.run_id, self.at, self.status = row
        A.point(db, self.at, None, None, run_id, self.q)  # every repo, as of the snapshot
        counts, self.team_pairs = {}, {}
        for repo, symbol, team, n in db.execute(
                "SELECT repo, symbol, owner_team, count(*) FROM site_now GROUP BY repo, symbol, owner_team"):
            counts[(repo, symbol)] = counts.get((repo, symbol), 0) + n
            self.team_pairs.setdefault(team, set()).add((repo, symbol))
        self.pairs = sorted(((r, s, n) for (r, s), n in counts.items()), key=lambda x: (-x[2], x[0], x[1]))
        self.snapshots = A.run_snapshots(db)
        self.coverage = [dict(zip(("metric", "numerator", "denominator", "note"), r)) for r in db.execute(
            "SELECT metric, numerator, denominator, note FROM coverage WHERE run_id = ? ORDER BY metric",
            (run_id,))] if _table(db, "coverage") else []
        self.texts = self._texts()

    def _texts(self):
        """{(kind, repo, symbol, target): text dict}: the block's suggestion row for the same facts, else the template.
        A rewrite spans symbols, so it's filed under each of its symbols."""
        stored = {}
        if _table(self.db, "suggestion"):
            q = "SELECT suggestion_id, body, source, model FROM suggestion WHERE offered_at <= ?"
            for sid, body, source, model in self.db.execute(q, (self.at,)):
                stored[sid] = (body, source, model)
        result = G.run(self.db, self.cfg, self.run_id, self.at, cache=self.cache)  # LLM off: the cache is only read
        out = {}
        for w in result.written:
            t = w.task
            body, source, model = w.text, w.source, w.model
            if source == "template":  # PLANTED (PASS-BARS 6.5, never merge): a view that needs the LLM
                raise RuntimeError(f"{t.kind} {t.target}: no LLM text")
            kind = G.ROW_KIND[t.kind]
            if kind and (hit := stored.get(h(kind, t.target, w.facts_hash))):
                body, source, model = hit
            text = {"kind": t.kind, "target": t.target, "team": t.owner_team, "source": source, "model": model,
                    "body": body, "facts": t.facts["facts"]}
            for symbol in t.facts["facts"].get("symbols") or [t.facts["facts"].get("symbol")]:
                out[(t.kind, t.repo, symbol, t.target)] = text
        return out

    def scope(self, repo=None, symbol=None, team=None):
        """The (repo, symbol) a view shows: as asked, else the one with the most call sites (for the team, if given)."""
        pairs = [(r, s) for r, s, _ in self.pairs if (repo is None or r == repo) and (symbol is None or s == symbol)]
        if team is not None:
            pairs = [p for p in pairs if p in self.team_pairs.get(team, ())]
        if not pairs:
            raise NotFound(f"no call sites for repo={repo} symbol={symbol} team={team}")
        A.point(self.db, self.at, *pairs[0], self.run_id, self.q)
        return pairs[0]

    def rows(self, name, **params):
        cur = self.db.execute(self.q[name], params)
        return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]

    def text(self, kind, repo, symbol, target):
        return self.texts.get((kind, repo, symbol, target))

    def suggestions(self, kind):
        """The block's stored rows of one kind (diff, legacy) offered by the snapshot, for the scope point() set."""
        if not _table(self.db, "suggestion"):
            return []
        return [list(r) for r in self.db.execute(
            "SELECT g.target, coalesce(s.path || ':' || s.line, g.target), g.owner_team, g.source, g.body "
            "FROM suggestion g LEFT JOIN site_now s ON g.target_kind = 'call_site' AND s.call_site_id = g.target "
            "WHERE g.kind = ? AND g.offered_at <= ? AND (g.target_kind != 'call_site' OR s.status = 'present') "
            "ORDER BY g.owner_team, 2", (kind, self.at))]

    def pushes(self):
        """The push_alert rows whose base run is known at the snapshot (a block before schema v1.3 has none)."""
        if not _table(self.db, "push_alert"):
            return []
        known = {r for r, snap in self.snapshots.items() if snap <= self.at}
        return [r for r in self.db.execute(
            "SELECT push_sha, base_run_id, check_id, target, outcome, decided_at FROM push_alert "
            "ORDER BY push_sha, check_id, target") if r[1] in known]

    def meta(self):
        return {"run_id": self.run_id, "snapshot_at": self.at, "status": self.status, "coverage": self.coverage,
                "data": "fixture" if self.fixture else "real", "note": FIXTURE_NOTE if self.fixture else ""}


class Site:
    """Decision 8: each view from the real block where its tables have rows there, else from the fixture, whose
    banner says so. rows: {view: rows its tables return on the real block, at its default scope}."""

    FALLBACK = "shown from the fixture because the real block has no rows for this view"

    def __init__(self, real, fixture):
        self.real, self.fixture = real, fixture
        self.rows = {v: view_rows(real, v) for v in VIEWS}
        self.source = {v: real if n or fixture is None else fixture for v, n in self.rows.items()}

    def pick(self, view):
        return self.source[view]


def default_path(b, view):
    """A view's page at the block's default scope; the consumer view's is the team with the most sites left there."""
    if view == "staff":
        return "/"
    if view in ("push", "tickets"):
        return "/" + view
    repo, symbol = b.scope()
    if view == "owner":
        return href("owner", symbol, repo=repo)
    team = max(((n, t) for t, n in b.db.execute(
        "SELECT owner_team, count(*) FROM site_now WHERE status = 'present' GROUP BY owner_team")), default=(0, ""))[1]
    return href("consumer", team, repo=repo, symbol=symbol)


def view_rows(b, view):
    """The rows a view's tables return on its default page (none if it has no page there)."""
    try:
        v = route(b, default_path(b, view))[0]
    except NotFound:
        return 0
    return sum(len(s["rows"]) for s in v["sections"] if "rows" in s)


def table(title, cols, rows, note="", headline=""):
    return {"title": title, "columns": cols, "rows": rows, "note": note, "headline": headline}


def diffs(title, rows, note=""):
    """Stored diffs, exactly as offered: [call site id, where, team, source, body]."""
    return {"title": title, "diffs": [dict(zip(("target", "where", "team", "source", "body"), r)) for r in rows],
            "note": note}


def texts(title, items, note=""):
    return {"title": title, "texts": [t for t in items if t], "note": note}


def page(b, view, title, scope, sections, links=()):
    return {"view": view, "title": title, "scope": scope, "meta": b.meta(), "sections": sections,
            "links": list(links)}


# ---- the five views ----

def staff(b, repo=None, symbol=None):
    repo, symbol = b.scope(repo, symbol)
    _, (head,) = b.rows("staff_headline")
    sections = [table("Headline", ["call sites remaining in code", "open tickets mapped to code"], [head])]
    sections.append(table("Hidden chains", ["type", "chains", "that Jira links"], b.rows("staff_chains")[1]))
    sections.append(table("Collisions: two open tickets edit the same function", ["ticket", "ticket", "function"],
                          b.rows("derive_collides")[1]))
    sections.append(table("Duplicates: two tickets map the same call site", ["ticket", "ticket", "call site"],
                          b.rows("derive_duplicates")[1]))
    sections.append(table("Untracked work: call sites no ticket covers", ["call site", "team"],
                          b.rows("staff_untracked")[1]))
    sections.append(table("Teams not connected: no ticket maps any of their call sites", ["team", "call sites"],
                          b.rows("stall_never_notified")[1]))
    sections.append(table("Vague tickets: failed a ticket check", ["ticket", "problem"], b.rows("staff_vague")[1],
                          "suggested rewrites on /tickets"))
    sections.append(table("Link coverage", ["tickets mapped", "tickets", "metric", "numerator", "denominator"],
                          b.rows("staff_link_coverage")[1]))
    sections.append(table("Legacy radar: deprecated functions still called", ["file", "function", "marker", "callers"],
                          b.rows("radar_deprecated_with_callers")[1]))
    sections.append(diffs("Legacy radar: unfiled legacy code", b.suggestions("legacy"),
                          "the block's legacy suggestion rows"))
    teams = sorted({r[0] for r in b.db.execute("SELECT DISTINCT owner_team FROM site_now WHERE status = 'present'")})
    links = [("API owner view", href("owner", symbol, repo=repo)),
             ("Ticket tools", href("tickets", repo=repo, symbol=symbol))]
    links += [(f"Consumer: {t}", href("consumer", t, repo=repo, symbol=symbol)) for t in teams]
    return page(b, "staff", f"{symbol} in {repo}", {"repo": repo, "symbol": symbol}, sections, links)


def owner(b, symbol, repo=None):
    repo, symbol = b.scope(repo, symbol)
    _, (done,) = b.rows("owner_done")
    sections = [table("Done", ["removed", "call sites"], [done], headline=f"{done[0]} of {done[1]} done")]
    eta = b.rows("owner_eta")[1]
    sections.append(table("ETA", ["left", "removed recently", "estimated done"], eta,
                          note="" if eta and eta[0][2] else
                          "no ETA when too few call sites were removed recently (meridian.toml [rules])"))
    teams = b.rows("owner_teams")[1]
    stalled = [t[0] for t in teams if t[4] == "stalled"]
    waiting = {}
    for team, _, _, blocker_team in b.rows("stall_upstream")[1]:
        waiting.setdefault(team, set()).add(blocker_team)
    teams = [t[:4] + ["STALLED" if t[4] == "stalled" else t[4],  # as 02-product shows it
                      ", ".join(sorted(waiting.get(t[0], ()))) if t[4] == "blocked" else ""] for t in teams]
    sections.append(table("Per team", ["team", "left", "risky", "days since a change", "status", "waiting on"], teams))
    stalls = [b.text("stall_reason", repo, symbol, t) for t in stalled]
    sections.append(texts("Possible reasons, for each stalled team", stalls,
                          "in the auditor's served policy's order; reasons, never probabilities"))
    links = [(f"Consumer: {t[0]}", href("consumer", t[0], repo=repo, symbol=symbol)) for t in teams if t[1]]
    return page(b, "owner", f"{symbol} in {repo}: API owner", {"repo": repo, "symbol": symbol}, sections, links)


def consumer(b, team, repo=None, symbol=None):
    repo, symbol = b.scope(repo, symbol, team)
    sections = [texts("Summary", [b.text("consumer", repo, symbol, team)])]
    sections.append(table("Call sites per file", ["path", "call sites", "bug fixes"],
                          b.rows("consumer_files", team=team)[1]))
    sections.append(table("Blocked by", ["call site", "blocker", "blocker's team", "blocker's path"],
                          b.rows("consumer_blocked", team=team)[1]))
    sections.append(table("You are blocking", ["team", "call sites"], b.rows("consumer_blocking", team=team)[1]))
    sections.append(diffs("Diffs", [r for r in b.suggestions("diff") if r[2] == team],
                          "one-line swaps, as the block's diff rows offered them"))
    return page(b, "consumer", f"{team}: {symbol} in {repo}", {"repo": repo, "symbol": symbol, "team": team},
                sections, [("Staff view", href(repo=repo, symbol=symbol))])


def push(b, sha=None):
    """/push: every push with notes. /push/<sha>: its notes. A block from before schema v1.3 has no push_alert."""
    alerts = b.pushes()
    if sha is None:
        counts = {}
        for r in alerts:
            counts[(r[0], r[1])] = counts.get((r[0], r[1]), 0) + 1
        rows = [[s, base, n] for (s, base), n in sorted(counts.items())]
        return page(b, "push", "Push-check notes", {}, [table("Pushes", ["push", "base run", "notes"], rows)],
                    [(f"Push {r[0]}", href("push", r[0])) for r in rows])
    rows = [[c, CHECKS.get(c, c), target, outcome if outcome and decided and decided <= b.at else "open"]
            for s, _, c, target, outcome, decided in alerts if s == sha]
    if not rows:
        raise NotFound(f"no push-check notes for push {sha}")
    return page(b, "push", f"Push {sha}: nothing blocks this merge", {"sha": sha},
                [table("Notes", ["check", "name", "target", "outcome"], rows,
                       "stored in push_alert by the push check; it never blocks a merge")],
                [("All pushes", "/push")])


def tickets(b, repo=None, symbol=None):
    repo, symbol = b.scope(repo, symbol)
    vague = b.rows("staff_vague")[1]
    sections = [table("Vague tickets", ["ticket", "problem"], vague),
                texts("Suggested rewrites", [b.text("rewrite", repo, symbol, k) for k, _ in vague])]
    drafts = b.rows("maker_drafts")[1]
    sections.append(table("Untracked work, one draft per team", ["team", "call sites", "where"], drafts))
    sections.append(texts("Drafts", [b.text("ticket_draft", repo, symbol, t) for t, *_ in drafts]))
    sections.append(table("Wrong owner: a ticket's sites belong to a team it doesn't name", ["ticket", "team"],
                          b.rows("check_wrong_owner")[1]))
    return page(b, "tickets", f"Ticket tools: {symbol} in {repo}", {"repo": repo, "symbol": symbol}, sections,
                [("Staff view", href(repo=repo, symbol=symbol))])


def href(*segments, **query):
    """A link: each path segment is escaped on its own (a real team is named like "org/repo"), then the query."""
    path = "/" + "/".join(quote(str(s), safe="") for s in segments)
    q = urlencode({k: v for k, v in query.items() if v is not None})
    return path + ("?" + q if q else "")


def route(b, path):
    """(view dict, as_json) for a request path, or NotFound. b: a Block, or a Site (decision 8)."""
    if isinstance(b, Site):
        url = urlsplit(path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        parts = parts[1:] if parts[:1] == ["api"] else parts
        view = "staff" if not parts or parts == ["staff"] else parts[0]
        if view not in VIEWS:
            raise NotFound(f"no view at {url.path}")
        src = b.pick(view)
        if src is b.real:
            return route(src, path)
        try:  # the fixture's own page if it has this one (its push list links to its pushes), else its default
            v, as_json = route(src, path)
        except NotFound:  # a link from a real page names the real block's repo, symbol, team or push
            v, as_json = route(src, ("/api" if url.path.startswith("/api") else "") + default_path(src, view))
        v["meta"] = {**v["meta"], "note": f"{v['meta']['note']}; {Site.FALLBACK}"}
        return v, as_json
    url = urlsplit(path)
    args = {k: v[0] for k, v in parse_qs(url.query).items()}
    parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
    as_json = bool(parts) and parts[0] == "api"
    if as_json:
        parts = parts[1:]
    if parts == ["staff"]:
        parts = []
    repo, symbol = args.get("repo"), args.get("symbol")
    if not parts:
        return staff(b, repo, symbol), as_json
    view, rest = parts[0], parts[1:]
    if view == "owner" and len(rest) == 1:
        return owner(b, rest[0], repo), as_json
    if view == "consumer" and len(rest) == 1:
        return consumer(b, rest[0], repo, symbol), as_json
    if view == "push" and len(rest) <= 1:
        return push(b, rest[0] if rest else None), as_json
    if view == "tickets" and not rest:
        return tickets(b, repo, symbol), as_json
    raise NotFound(f"no view at {url.path}")


# ---- rendering ----

def esc(v):
    return html.escape("" if v is None else str(v))


CSS = """:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--grid:#e1e0d9;
--ring:rgba(11,11,11,.10);--link:#1c5cab;--warn:#fff4d6}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--link:#86b6ef;--warn:#3a2f10}}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header,section,nav{max-width:1100px;margin:0 auto;padding:12px 16px}h1{font-size:20px;margin:0}
h2{font-size:15px;margin:6px 0}.meta,.note{color:var(--ink2);font-size:12px}a{color:var(--link)}
.banner{background:var(--warn);padding:6px 10px;border-radius:6px;margin-top:8px}
.scroll{overflow-x:auto;background:var(--surface);box-shadow:0 0 0 1px var(--ring);border-radius:8px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:5px 10px;border-bottom:1px solid var(--grid)}
th{color:var(--ink2);font-weight:500;font-size:12px}pre{white-space:pre-wrap;background:var(--surface);margin:0 0 8px;
padding:8px 10px;border-radius:8px;box-shadow:0 0 0 1px var(--ring);font:13px/1.5 ui-monospace,Consolas,monospace}
nav a{margin-right:14px}.headline{font-size:18px;font-weight:600;margin:4px 0 8px}"""


def to_html(v):
    m = v["meta"]
    cov = " · ".join(f"{c['metric']} {c['numerator']} of {c['denominator']}" for c in m["coverage"])
    cov = cov or "no coverage rows"
    banner = f'<p class="banner">{esc(m["note"])}</p>' if m["data"] == "fixture" else ""
    out = [f"<header><h1>{esc(v['title'])}</h1>",
           f'<p class="meta">run <code>{esc(m["run_id"])}</code> · snapshot_at {esc(m["snapshot_at"])} · '
           f'{esc(m["data"])} block · coverage: {esc(cov)}</p>{banner}</header>']
    if v["links"]:
        out.append("<nav>" + "".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in v["links"]) + "</nav>")
    for s in v["sections"]:
        out.append(f"<section><h2>{esc(s['title'])}</h2>")
        if s.get("note"):
            out.append(f'<p class="note">{esc(s["note"])}</p>')
        if s.get("headline"):
            out.append(f'<p class="headline">{esc(s["headline"])}</p>')
        if "diffs" in s:
            for d in s["diffs"]:
                out.append(f'<p class="note">{esc(d["where"])} · {esc(d["team"])} · {esc(d["source"])}</p>'
                           f"<pre>{esc(d['body'])}</pre>")
            if not s["diffs"]:
                out.append('<p class="note">no rows on this block</p>')
        elif "rows" in s:
            if s["rows"]:
                head = "".join(f"<th>{esc(c)}</th>" for c in s["columns"])
                body = "".join("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in r) + "</tr>" for r in s["rows"])
                out.append(f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>')
            else:
                out.append('<p class="note">no rows on this block</p>')
        else:
            for t in s["texts"]:
                out.append(f'<p class="note">{esc(t["kind"])} · {esc(t["target"])} · {esc(t["source"])}</p>'
                           f"<pre>{esc(t['body'])}</pre>")
            if not s["texts"]:
                out.append('<p class="note">nothing to say on this block</p>')
        out.append("</section>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>Meridian · {esc(v["title"])}</title>'
            f"<style>{CSS}</style></head><body>{''.join(out)}</body></html>")


def to_json(v):
    return json.dumps(v, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def serve(b, port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def reply(self, code, body, kind):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                with lock:  # one request at a time: see the module docstring
                    v, as_json = route(b, self.path)
                    code, body, kind = (200, to_json(v), "application/json") if as_json else \
                        (200, to_html(v), "text/html")
            except NotFound as e:
                code, body, kind = 404, str(e), "text/plain"
            except Exception:  # any other failure is a 500, never a dropped connection
                log(f"GET {self.path} failed:\n{traceback.format_exc()}")
                code, body, kind = 500, "the view failed; the server log has the traceback", "text/plain"
            self.reply(code, body, kind)

        def log_message(self, *a):  # one line per request on stdout, like the rest of the pipeline
            log(f"{self.command} {self.path}")

    lock = threading.Lock()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    return srv


def fixture_db():
    """KNOWLEDGE-BASE.md's fixture, acme/shop at R2, in memory (tests/test_kb.py builds it)."""
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    import importlib.util
    spec = importlib.util.spec_from_file_location("kb_fixture", Path(__file__).resolve().parents[1] / "tests" /
                                                  "test_kb.py")
    K = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(K)
    src, db = K.build(), sqlite3.connect(":memory:", check_same_thread=False)  # the server reads it from its threads
    src.commit()
    src.backup(db)
    src.close()
    return db, K.R2[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", help="the block: out/<run_id>/meridian.sqlite")
    ap.add_argument("--config", type=Path, default=CONFIG, help="meridian.toml")
    ap.add_argument("--db", type=Path, help="read this SQLite file instead of the run's block")
    ap.add_argument("--fixture", action="store_true", help="every view from KNOWLEDGE-BASE.md's fixture acme/shop")
    ap.add_argument("--real-only", action="store_true",
                    help="every view from the real block, even one with no rows there (default: decision 8)")
    ap.add_argument("--serve", type=int, metavar="PORT", help="serve on http://127.0.0.1:PORT/")
    ap.add_argument("--path", default="/", help="without --serve: print this page's JSON (e.g. /api/tickets)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    dbs = []
    try:
        fixture = None
        if a.fixture or not a.real_only:
            db, run_id = fixture_db()
            dbs.append(db)
            fixture = Block(db, cfg, run_id, fixture=True, cache=Cache())
        if a.fixture:
            site = fixture
        else:
            if not a.run_id:
                raise SystemExit("pass --run-id, or --fixture")
            path = Path(a.db or handoff(cfg, a.run_id) / "meridian.sqlite")
            if not path.exists():
                raise SystemExit(f"no block at {path}: run the writer first, or pass --db")
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
            dbs.append(db)
            if a.serve is not None:  # the log is stdout: --path prints only the page's JSON there
                if missing := missing_indexes(db):  # a block written before them starts several times slower
                    log(f"the block has no {', '.join(missing)}: add them with "
                        f"python -m meridian.writer --run-id {a.run_id} --index --db {path}")
                log(f"run {a.run_id}: writing the texts, templates or the committed cache (the LLM is off here)")
            site = Site(Block(db, cfg, a.run_id, cache=Cache()), fixture)
            for v in VIEWS if a.serve is not None else ():  # decision 8's measure, logged on every start
                log(f"  {v}: {site.rows[v]} rows on the real block, so from the "
                    f"{'real block' if site.pick(v) is site.real else 'fixture'}")
        if a.serve is None:
            try:
                v, _ = route(site, a.path)
            except NotFound as e:
                raise SystemExit(str(e)) from None
            sys.stdout.write(to_json(v))
            return 0
        srv = serve(site, a.serve)
        log(f"http://127.0.0.1:{a.serve}/ · {'fixture' if a.fixture else 'run ' + a.run_id}")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
    finally:
        for db in dbs:
            db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
