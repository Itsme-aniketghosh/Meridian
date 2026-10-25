"""A dashboard of one run, for testing: how the data behaves, and which planned features have data on it. Local only.

    python -m tests.dashboard --run-id <run_id> --config meridian.toml     # -> out/<run_id>/dashboard.html
    python -m tests.dashboard --run-id <run_id> --config meridian.toml --serve 8765   # and serve it, with the button

Reads out/<run_id>/meridian.sqlite, as validate built it; builds it with meridian.writer (so it also proves the block
loads) only when it is missing or older than any out/<run_id>/*.jsonl or schema.sql. Opens it read-only, adds the
tracking tables KNOWLEDGE-BASE.md proposes as TEMP tables (suggestion, outcome, push_alert: empty until stage 6
writes them), then runs every named query in KNOWLEDGE-BASE.md against it. The doc's parameters name fixture teams
and files, so team, path, scope and key are swapped for this run's biggest. Both files sit under out/, which git
ignores: keep them there, they carry ticket keys and, in the stall queries, people's names.

The header says how stale the published block is (meridian.status). Its "Update graph" button works when the page is
served (--serve, on 127.0.0.1 only): a click triggers meridian_update through Airflow's REST API (meridian.status
trigger), and says whether it queued a run or one was queued already.
"""
import html, http.server, json, re, sqlite3, time
from pathlib import Path

import meridian.status as S
import meridian.writer as W
from meridian.common import data_root, handoff, load_config, log, parser

DOC = Path(__file__).resolve().parents[1] / "KNOWLEDGE-BASE.md"
QUERY_SECONDS = 20  # a planned query this slow on real data is a finding, not a hang
PREVIEW = 5


def doc_queries():
    """[(section, name, sql, params, is_query)] in doc order. is_query: it has an -- expect: line (else setup DDL)."""
    out, section = [], ""
    for part in re.split(r"(```sql\n.*?```)", DOC.read_text(encoding="utf-8"), flags=re.S):
        if not part.startswith("```sql"):
            section = ([m for m in re.findall(r"^#{2,3} (.+)$", part, re.M)] or [section])[-1]
            continue
        name = re.search(r"^-- name: (\S+)", part, re.M)
        if name:
            params = re.search(r"^-- params: (.*)$", part, re.M)
            out.append((section, name[1], part[7:-3], json.loads(params[1]) if params else {},
                        bool(re.search(r"^-- expect:", part, re.M))))
    return out


def run_query(db, sql, params):
    start = time.monotonic()
    db.set_progress_handler(lambda: time.monotonic() - start > QUERY_SECONDS, 10_000)
    try:
        cur = db.execute(sql, params)
        return [d[0] for d in cur.description], cur.fetchall()
    finally:
        db.set_progress_handler(None, 0)


def picks(db, run):
    """Parameters from this run's data, standing in for the doc's fixture names."""
    one = lambda sql: (db.execute(sql).fetchone() or [None])[0]
    top = "SELECT {} FROM site_now WHERE status = 'present' GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"
    path = one(top.format("path"))
    return {"team": one(top.format("owner_team")), "path": path, "t": run["snapshot_at"], "base": run["snapshot_at"],
            "scope": one(f"SELECT scope FROM site_now WHERE path = {sql_str(path)} GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"),
            "key": one("SELECT key FROM ticket_map WHERE target_kind != 'unmapped' GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1"),
            "sha": one("SELECT sha FROM git_commit ORDER BY authored_at DESC, sha LIMIT 1")}


def sql_str(v):
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


# ───────────── html ─────────────

def esc(v):
    return html.escape("NULL" if v is None else f"{v:,}" if isinstance(v, int) else str(v))


def table(head, rows, bars=()):
    """rows of cells; columns in bars get a bar scaled to that column's max."""
    top = {i: max([r[i] for r in rows if isinstance(r[i], (int, float))] or [0]) or 1 for i in bars}
    cell = lambda i, v: (f'<td class="num"><span class="bar" style="width:{120 * v / top[i]:.0f}px" title="{esc(v)}"></span>{esc(v)}</td>'
                         if i in bars else f'<td class="num">{esc(v)}</td>' if isinstance(v, (int, float)) else f"<td>{esc(v)}</td>")
    body = "".join("<tr>" + "".join(cell(i, v) for i, v in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{"".join(f"<th>{esc(h)}</th>" for h in head)}</tr></thead><tbody>{body}</tbody></table></div>'


def depth_bar(by_depth):
    total = sum(by_depth.values()) or 1
    segs = "".join(f'<span class="seg d{min(int(d), 3)}" style="width:{100 * n / total:.1f}%" title="depth {d}: {n:,}"></span>'
                   for d, n in sorted(by_depth.items(), key=lambda x: int(x[0])) if n)  # shortcut: depth 4+ shares depth 3's step
    return f'<span class="stack">{segs}</span>'


def tile(label, value, note=""):
    return f'<div class="tile"><div class="label">{esc(label)}</div><div class="value">{esc(value)}</div><div class="note">{esc(note)}</div></div>'


STATUS = {"rows": ("good", "✓", "has rows"), "empty": ("warning", "○", "no rows"), "error": ("critical", "✕", "error")}


def page(run, sections, stale=""):
    nav = "".join(f'<a href="#{i}">{esc(t)}</a>' for i, (t, _) in enumerate(sections))
    body = "".join(f'<section id="{i}"><h2>{esc(t)}</h2>{s}</section>' for i, (t, s) in enumerate(sections))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Meridian run {esc(run["run_id"])}</title><style>
:root{{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--ring:rgba(11,11,11,.10);--s1:#2a78d6;--d1:#1c5cab;--d2:#3987e5;--d3:#86b6ef;--good:#0ca30c;--warning:#fab219;--critical:#d03b3b}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--s1:#3987e5;--d1:#86b6ef;--d2:#3987e5;--d3:#184f95}}}}
:root[data-theme="dark"]{{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;
--ring:rgba(255,255,255,.10);--s1:#3987e5;--d1:#86b6ef;--d2:#3987e5;--d3:#184f95}}
body{{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}
header,section{{max-width:1200px;margin:0 auto;padding:16px}}header p{{color:var(--ink2);margin:4px 0}}
nav{{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:8px}}nav a{{color:var(--s1);text-decoration:none}}
h1{{font-size:20px;margin:0}}h2{{font-size:16px;margin:8px 0 10px}}h3{{font-size:14px;margin:16px 0 6px;color:var(--ink2)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}}
.tile{{background:var(--surface);box-shadow:0 0 0 1px var(--ring);border-radius:8px;padding:10px 12px}}
.tile .label,.note{{color:var(--ink2);font-size:12px}}.tile .value{{font-size:22px;margin:2px 0}}
.scroll{{overflow-x:auto;background:var(--surface);box-shadow:0 0 0 1px var(--ring);border-radius:8px}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:5px 10px;border-bottom:1px solid var(--grid);vertical-align:top}}
th{{color:var(--muted);font-weight:500;font-size:12px;position:sticky;top:0;background:var(--surface)}}
td.num{{font-variant-numeric:tabular-nums;white-space:nowrap;position:relative}}
.bar{{display:inline-block;height:8px;background:var(--s1);border-radius:0 4px 4px 0;margin-right:8px;min-width:1px;vertical-align:middle}}
.stack{{display:inline-flex;width:160px;height:10px;gap:2px;vertical-align:middle}}
.seg{{height:100%;border-radius:2px}}.d1{{background:var(--d1)}}.d2{{background:var(--d2)}}.d3{{background:var(--d3)}}
.legend{{color:var(--ink2);font-size:12px;margin:6px 0}}.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px;vertical-align:-1px}}
.st{{white-space:nowrap}}.st b{{display:inline-block;width:16px;text-align:center;border-radius:4px;color:#fff;margin-right:6px}}
.st.good b{{background:var(--good)}}.st.warning b{{background:var(--warning);color:#0b0b0b}}.st.critical b{{background:var(--critical)}}
code,.mono{{font:12px ui-monospace,Consolas,monospace}}details summary{{cursor:pointer;color:var(--s1)}}
</style></head><body><header><h1>Meridian · run <span class="mono">{esc(run["run_id"])}</span></h1>
<p>snapshot_at {esc(run["snapshot_at"])} · status {esc(run["status"])} · built from <code>out/{esc(run["run_id"])}/</code>, local only</p>
<p>{esc(stale)} · <button onclick="fetch('/update',{{method:'POST',headers:{{'X-Meridian':'1'}}}}).then(r=>r.text())
.catch(()=>'serve the page (--serve) to use the button').then(t=>this.nextElementSibling.textContent=t)">Update graph</button>
<span class="note"></span></p><nav>{nav}</nav></header>{body}</body></html>"""


# ───────────── sections ─────────────

def build(db, run, queries, stale=""):
    q = lambda sql: db.execute(sql).fetchall()
    sections = []

    counts = [(t, db.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
              for (t,) in q("""SELECT name FROM sqlite_master WHERE type = 'table'
                               UNION SELECT name FROM temp.sqlite_master WHERE type = 'table' AND name != 'asof' ORDER BY name""")]
    present = q("SELECT count(*) FROM site_now WHERE status = 'present'")[0][0]
    blocked = q("SELECT count(*) FROM blocked_by")[0][0]
    tiles = (tile("present call sites", present) + tile("call edges", dict(counts)["call_edge"])
             + tile("blocked_by", blocked, f"{q('SELECT count(*) FROM blocked_by WHERE status = ' + sql_str('unknown'))[0][0]:,} unknown")
             + tile("chains", dict(counts)["chain"]) + tile("tickets", dict(counts)["ticket"])
             + tile("receivers", q("SELECT count(*) FROM resolution WHERE receiver_from IS NOT NULL")[0][0], "resolution.receiver_from set")
             + tile("suggestions tracked", dict(counts)["suggestion"], "proposed tables; stage 6 writes them"))
    sections.append(("Overview", f'<div class="tiles">{tiles}</div><h3>Rows per table</h3>' + table(["table", "rows"], counts, bars=[1])))

    cov = q("SELECT metric, numerator, denominator, round(100.0 * numerator / max(denominator, 1), 1), note FROM coverage ORDER BY metric")
    sections.append(("Coverage", table(["metric", "of", "out of", "%", "what it counts"], cov, bars=[3])))

    by = lambda col: q(f"SELECT {col}, sum(status = 'present'), sum(status = 'removed') FROM site_now GROUP BY 1 ORDER BY 2 DESC, 1")
    sections.append(("Call sites", "<h3>By repo</h3>" + table(["repo", "present", "removed"], by("repo"), bars=[1])
                     + "<h3>By owner team (top 30)</h3>" + table(["owner_team", "present", "removed"], by("owner_team")[:30], bars=[1])
                     + "<h3>By role and detection</h3>" + table(["role · detection", "present", "removed"], by("role || ' · ' || detection"), bars=[1])
                     + "<h3>Inside except (no LLM diff, stall reason #5)</h3>" + table(["in_except", "present", "removed"], by("in_except"))))

    graph = ("<h3>blocked_by by depth and status</h3>"
             + table(["depth", "status", "rows"], q("SELECT depth, status, count(*) FROM blocked_by GROUP BY 1, 2 ORDER BY 1, 2"), bars=[2])
             + "<h3>Cross-team pairs (waiting team → blocking team)</h3>"
             + table(["waiting", "blocking", "rows"], q("""SELECT a.owner_team, k.owner_team, count(*) FROM blocked_by b
                     JOIN site_now a ON a.call_site_id = b.call_site_id JOIN site_now k ON k.call_site_id = b.blocker_id
                     GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT 30"""), bars=[2])
             + "<h3>blocked_by rows (first 50)</h3>"
             + table(["waiting site", "blocker site", "via function", "depth", "status"], q("""SELECT a.path || ':' || a.line || ' (' || a.owner_team || ')',
                     k.path || ':' || k.line || ' (' || k.owner_team || ')', b.via_function, b.depth, b.status FROM blocked_by b
                     JOIN site_now a ON a.call_site_id = b.call_site_id JOIN site_now k ON k.call_site_id = b.blocker_id
                     ORDER BY b.depth, a.path, a.line LIMIT 50""")))
    blast = q("SELECT repo, symbol, direct_sites, invocations, files, by_depth FROM blast_radius ORDER BY invocations DESC, repo, symbol")
    rows = "".join(f'<tr><td>{esc(r)}</td><td><code>{esc(s)}</code></td><td class="num">{esc(d)}</td><td class="num">{esc(i)}</td>'
                   f'<td class="num">{esc(f)}</td><td>{depth_bar(json.loads(b))} <span class="note">{esc(b)}</span></td></tr>'
                   for r, s, d, i, f, b in blast)
    graph += ('<h3>Blast radius per repo and symbol</h3><div class="legend">invocations by depth<i class="d1"></i>1 direct'
              '<i class="d2"></i>2<i class="d3"></i>3</div><div class="scroll"><table><thead><tr><th>repo</th><th>symbol</th>'
              f'<th>direct sites</th><th>invocations</th><th>files</th><th>by depth</th></tr></thead><tbody>{rows}</tbody></table></div>')
    chain = q("SELECT type, count(*), sum(in_cycle), sum(json_array_length(evidence)) FROM chain GROUP BY 1 ORDER BY 1")
    graph += ("<h3>Chains by type</h3>" + table(["type", "rows", "in a cycle", "evidence items"], chain, bars=[1])
              + "<h3>Untracked work per team (no ticket maps to these sites)</h3>"
              + table(["team", "sites"], q("SELECT from_key, json_array_length(evidence) FROM chain WHERE type = 'untracked' ORDER BY 2 DESC, 1 LIMIT 30"), bars=[1])
              + "<h3>Ticket chains (first 50)</h3>"
              + table(["from", "to", "type", "in cycle", "evidence"], q("""SELECT from_key, to_key, type, in_cycle, evidence FROM chain
                      WHERE type != 'untracked' ORDER BY type, from_key, to_key LIMIT 50""")))
    sections.append(("Graph", graph))

    sections.append(("Tickets → code", table(["method", "target kind", "rows", "tickets"], q(
        "SELECT method, target_kind, count(*), count(DISTINCT key) FROM ticket_map GROUP BY 1, 2 ORDER BY 3 DESC"), bars=[2])))

    rows, state = [], lambda got, err: "error" if err else "rows" if got else "empty"
    for section, name, sql, params, cols, got, err in queries:
        cls, icon, word = STATUS[state(got, err)]
        preview = "" if err or not got else (f"<details><summary>first {min(PREVIEW, len(got))} of {len(got):,}</summary>"
                                             + table(cols, [list(r) for r in got[:PREVIEW]]) + "</details>")
        rows.append(f'<tr><td>{esc(section)}</td><td><code>{esc(name)}</code></td><td class="st {cls}"><b>{icon}</b>{word}</td>'
                    f'<td class="num">{esc(len(got))}</td><td><span class="note">{esc(err or (json.dumps(params) if params else ""))}</span>{preview}</td></tr>')
    n = {s: sum(state(got, err) == s for *_, got, err in queries) for s in STATUS}
    legend = " · ".join(f'<span class="st {STATUS[s][0]}"><b>{STATUS[s][1]}</b>{STATUS[s][2]}</span> {n[s]}' for s in STATUS)
    sections.append(("Planned features", f'<p class="note">Every named query in KNOWLEDGE-BASE.md, run on this block. '
                     f'Empty can be right (no chains without tickets on call sites), and rows can be hollow: with no suggestion '
                     f'rows, the tracking queries only show the control group.</p><p>{legend}</p>'
                     '<div class="scroll"><table><thead><tr><th>section</th><th>query</th><th>status</th><th>rows</th>'
                     f'<th>params · error · preview</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'))
    return page(run, sections, stale)


def main(run_id, config, root=None):
    cfg = load_config(config)
    out = handoff(cfg, run_id, Path(root or data_root()))
    block = out / "meridian.sqlite"  # the block itself, git-ignored like out/
    fresh = block.exists() and all(block.stat().st_mtime > p.stat().st_mtime for p in [*out.glob("*.jsonl"), W.SCHEMA])
    loaded = None if fresh else W.write(out)
    db = sqlite3.connect(block.resolve().as_uri() + "?mode=ro", uri=True)  # the block stays as validate wrote it
    run = dict(zip(("run_id", "snapshot_at", "status"), db.execute("SELECT run_id, snapshot_at, status FROM run").fetchone()))
    top = db.execute("""SELECT repo, symbol FROM call_site GROUP BY 1, 2 ORDER BY count(*) DESC, 1, 2 LIMIT 1""").fetchone() or ("", "")
    db.execute("CREATE TEMP TABLE asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT)")
    db.execute("INSERT INTO asof VALUES (?, ?, ?, ?)", (run_id, run["snapshot_at"], *top))
    results, pick = [], None
    for section, name, sql, params, is_query in doc_queries():
        if not is_query:  # setup views and the proposed tracking tables
            db.executescript(re.sub(r"\bCREATE TABLE\b", "CREATE TEMP TABLE", sql))
            continue
        pick = pick or picks(db, run)
        params = {k: pick.get(k) or v for k, v in params.items()}
        try:
            cols, got = run_query(db, sql, params)
            results.append((section, name, sql, params, cols, got, None))
        except sqlite3.Error as e:
            results.append((section, name, sql, params, [], [], f"{type(e).__name__}: {e}"))
    path = out / "dashboard.html"
    try:
        path.write_text(build(db, run, results, S.line(S.status(config, root))), encoding="utf-8", newline="\n")
    finally:
        db.close()
    state = lambda r: "error" if r[6] else "rows" if r[5] else "empty"
    log((f"{sum(loaded.values()):,} rows loaded" if loaded else f"reused {block.name}") + f" · {len(results)} planned queries: "
        + ", ".join(f"{sum(state(r) == s for r in results)} {s}" for s in STATUS) + f" -> {path}")
    return path


def serve(path, port):
    """The page on http://127.0.0.1:port/. POST /update (the button) triggers meridian_update. The custom header keeps
    other sites' pages from clicking it: a cross-site request can't send one without a preflight this server refuses."""
    class Page(http.server.BaseHTTPRequestHandler):
        def reply(self, code, text, kind="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))

        def do_GET(self):
            self.reply(200, path.read_text(encoding="utf-8"), "text/html")

        def do_POST(self):
            if self.path != "/update" or self.headers.get("X-Meridian") != "1":
                return self.reply(403, "no")
            try:
                self.reply(200, S.trigger())
            except OSError as e:  # Airflow down, wrong login: say so on the page
                self.reply(200, f"trigger failed: {e}")

    log(f"http://127.0.0.1:{port}/")
    http.server.HTTPServer(("127.0.0.1", port), Page).serve_forever()


if __name__ == "__main__":
    ap = parser(__doc__)
    ap.add_argument("--serve", type=int, metavar="PORT", help="serve the page on 127.0.0.1:PORT, with the Update graph button")
    a = ap.parse_args()
    path = main(a.run_id, a.config)
    if a.serve:
        serve(path, a.serve)
