"""Views and API (Contract 6) against PASS-BARS §5, on KNOWLEDGE-BASE.md's fixture (tests/test_kb.py).

    python -m unittest tests.test_views -v                          # from Data-Pipeline/
    MERIDIAN_VIEWS_BLOCK=../out/<run_id>/meridian.sqlite python -m unittest tests.test_views -v   # + a real block

Every number on every page must trace to a row: a table cell, the run's own fields (run_id, snapshot_at, coverage),
or a writer text the fact gate passes against facts read from rows. Titles, notes and column names carry none.
"""
import contextlib
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import agents as G
from meridian import views as V
from meridian.common import CONFIG, h, load_config
from meridian.fact_gate import check

CFG = load_config(CONFIG)
NUM = re.compile(r"\d+(?:\.\d+)?")
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
REAL = os.environ.get("MERIDIAN_VIEWS_BLOCK")


def block(db=None, run_id="R2"):
    db = db or K.build()
    db.commit()
    return V.Block(db, CFG, run_id, fixture=True)


def late_site(db, site_id, team="checkout", symbol=K.SYMBOL):
    """A call site first known at R2: billing's S_b1 with a new id, line, team or symbol."""
    db.execute("INSERT INTO call_site SELECT ?, 'R2', repo, path, line + 1000, col, scope, ?, role, detection, junk, "
               "in_except, ?, bug_fixes, 'present', effective_date, effective_sha, ? FROM call_site "
               "WHERE call_site_id = 'S_b1'", (site_id, symbol, team, K.R2[1]))


def paths(b):
    """Every page of a block: each view for its default scope, each team's consumer page, each push."""
    out = ["/", V.href("owner", b.pairs[0][1]), "/push", "/tickets"]
    repo, symbol = b.scope()
    out += [V.href("consumer", t, repo=repo, symbol=symbol) for (t,) in b.db.execute(
        "SELECT DISTINCT owner_team FROM call_site WHERE repo = ? AND symbol = ? ORDER BY 1", (repo, symbol))]
    if V._table(b.db, "push_alert"):
        out += [V.href("push", s) for (s,) in b.db.execute("SELECT DISTINCT push_sha FROM push_alert ORDER BY 1")]
    return out


def visible(page):
    """The page's text as a reader sees it: no style, no tags."""
    page = re.sub(r"<style>.*?</style>|<title>.*?</title>", "", page, flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def section(v, title):
    return next(s for s in v["sections"] if s["title"] == title)


def nums(x):
    return set(NUM.findall(x if isinstance(x, str) else json.dumps(x, default=str)))


def untraced(v, teams, people, page=None):
    """[(where, number)] for each number the page shows that its own source doesn't hold: a table cell only its own
    row's value, a diff only its own row, a text only a text the gate passes, the header only the run's fields and
    the view's scope. A section's title and note may show none."""
    page, bad = page or V.to_html(v), []
    head, *parts = page.split("<section>")
    run = nums(v["meta"]) | nums(v["scope"])
    links = run | {n for s in v["sections"] for n in nums(s.get("rows", []))}
    head, nav = head.split("<nav>") if "<nav>" in head else (head, "")
    bad += [("header", n) for n in nums(visible(head)) - run]
    bad += [("nav", n) for n in nums(visible(nav)) - links]
    assert len(parts) == len(v["sections"]), "one <section> per section"
    for s, part in zip(v["sections"], parts):
        title = s["title"]
        if "rows" in s:
            trs = re.findall(r"<tr>(.*?)</tr>", part, re.S)[1:]
            assert len(trs) == len(s["rows"]), title
            for row, tr in zip(s["rows"], trs):
                tds = re.findall(r"<td>(.*?)</td>", tr, re.S)
                assert len(tds) == len(row), title
                bad += [(title, n) for value, td in zip(row, tds) for n in nums(visible(td)) - nums(value)]
            own = nums(s["rows"])
        elif "diffs" in s:
            own = nums(s["diffs"])
        else:
            own = set()
            for t in s["texts"]:
                if check(t["body"], {"facts": t["facts"]}, teams=teams, people=people, vocab=G.VOCAB):
                    own |= nums(t["body"])
        bad += [(title, n) for n in nums(visible(part)) - own]
    return bad


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = block()
        cls.teams, cls.people = G.names(cls.b.db)
        cls.pages = {p: V.route(cls.b, p)[0] for p in paths(cls.b)}

    def test_every_page_renders_with_its_run_and_coverage_and_says_its_the_fixture(self):
        self.assertEqual(len(self.pages), 4 + 6 + 2)  # 4 views, 6 teams' consumer pages, 2 pushes
        for p, v in self.pages.items():
            page = V.to_html(v)
            api = json.loads(V.to_json(V.route(self.b, "/api" + p)[0]))
            self.assertEqual(api, json.loads(V.to_json(v)), p)  # the JSON is the page's own data
            self.assertEqual((api["meta"]["run_id"], api["meta"]["snapshot_at"], api["meta"]["data"]),
                             ("R2", K.R2[1], "fixture"), p)
            self.assertIn("tickets_mapped 6 of 7", page, p)  # coverage on every page
            self.assertIn("fixture acme/shop", page, p)       # and the banner

    def test_every_number_on_every_page_traces_to_a_row(self):
        for p, v in self.pages.items():
            self.assertTrue(NUM.findall(visible(V.to_html(v))), p)
            self.assertEqual(untraced(v, self.teams, self.people), [], p)

    def test_a_number_in_the_wrong_cell_is_caught(self):
        """The check itself: a number shown in a cell whose row doesn't hold it fails, even when another cell does."""
        v = self.pages[V.href("owner", K.SYMBOL)]
        page = V.to_html(v)
        good = "<tr><td>checkout</td><td>3</td><td>2</td><td>19</td>"
        self.assertIn(good, page)
        self.assertIn("<td>264</td>", page)  # 264 is on the page, in billing's row
        wrong = page.replace(good, good.replace("<td>19</td>", "<td>264</td>"))  # checkout shows billing's days
        self.assertEqual(untraced(v, self.teams, self.people, wrong), [("Per team", "264")])
        titled = page.replace("<h2>Done</h2>", "<h2>Done 7</h2>")  # a title may show no number
        self.assertEqual(untraced(v, self.teams, self.people, titled), [("Done", "7")])

    def test_every_text_passes_the_gate(self):
        for p, v in self.pages.items():
            for s in v["sections"]:
                for t in s.get("texts", []):
                    self.assertTrue(check(t["body"], {"facts": t["facts"]}, teams=self.teams, people=self.people,
                                          vocab=G.VOCAB), (p, t["body"]))

    def test_the_fixture_tells_02_products_story(self):
        """PASS-BARS §5's fixture bar, in the fixture's own numbers: "5 of 13 done" where 02-product's mock says
        "31 of 52" (acme/shop has 13 call sites), Checkout STALLED, Fulfillment blocked."""
        owner = self.pages[f"/owner/{K.SYMBOL}"]
        per_team = {r[0]: r for r in section(owner, "Per team")["rows"]}
        self.assertEqual(per_team["checkout"][4], "STALLED")
        self.assertEqual(per_team["fulfillment"][4], "blocked")
        self.assertEqual(section(owner, "Done")["rows"], [[5, 13]])
        page = V.to_html(owner)
        self.assertIn('<p class="headline">5 of 13 done</p>', page)
        self.assertIn("<td>checkout</td><td>3</td><td>2</td><td>19</td><td>STALLED</td>", page)
        self.assertIn("<td>fulfillment</td><td>1</td><td>0</td><td>264</td><td>blocked</td>", page)
        consumer = V.to_html(V.route(self.b, "/consumer/checkout")[0])
        self.assertIn("4 bug fixes in the last year (BUG-0, BUG-1, BUG-2, BUG-3)", consumer)
        tickets = V.to_html(self.pages["/tickets"])
        self.assertIn("CHK-311: Migrate cart/ off verify_token", tickets)
        self.assertIn("Keep the same handling?", tickets)

    def test_no_person_on_any_page(self):
        for p, v in self.pages.items():
            text = visible(V.to_html(v)) + V.to_json(v)
            self.assertIsNone(EMAIL.search(text), p)
            for name in self.people:
                self.assertNotRegex(text, rf"\b{re.escape(name)}\b", p)


class WriterText(unittest.TestCase):
    """The block's suggestion row where one exists for the same facts, else the template (PASS-BARS §5, LLM off)."""

    def test_an_empty_suggestion_table_renders_every_page_with_templates(self):
        db = K.build()
        db.executescript("DELETE FROM outcome; DELETE FROM suggestion;")
        b = block(db)
        for p in paths(b):
            v = V.route(b, p)[0]
            for s in v["sections"]:
                self.assertTrue(all(t["source"] == "template" for t in s.get("texts", [])), p)

    def test_a_stored_row_for_the_same_facts_is_shown(self):
        db = K.build()
        b0 = block(db)
        t = b0.text("ticket_draft", K.REPO, K.SYMBOL, "billing")
        fh = G.facts_hash({"facts": t["facts"]})
        db.execute("INSERT INTO suggestion (run_id, suggestion_id, kind, target_kind, target, owner_team, source, "
                   "body, facts_hash, offered_at, agent, model, route) VALUES ('R2', ?, 'ticket_draft', 'team', "
                   "'billing', 'billing', 'llm', 'STORED TEXT', ?, ?, 'writer', 'claude-haiku-5-5', 'rules')",
                   (h("ticket_draft", "billing", fh), fh, K.R2[1]))
        shown = block(db).text("ticket_draft", K.REPO, K.SYMBOL, "billing")
        self.assertEqual((shown["body"], shown["source"], shown["model"]), ("STORED TEXT", "llm", "claude-haiku-5-5"))

    def test_a_block_from_before_the_tracking_tables(self):
        db = K.build()
        db.executescript("DROP TABLE outcome; DROP TABLE suggestion; DROP TABLE push_alert;")
        b = block(db)
        self.assertEqual(V.route(b, "/push")[0]["sections"][0]["rows"], [])
        for p in paths(b):  # every page, not only /push
            v = V.route(b, p)[0]
            self.assertEqual(untraced(v, *G.names(db)), [], p)
            self.assertEqual([d for s in v["sections"] for d in s.get("diffs", [])], [], p)


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = block()

    def test_unknown_paths_teams_and_pushes_are_not_found(self):
        for p in ("/nope", "/owner", "/owner/no_such_symbol", "/consumer/nobody", "/push/zzz", "/tickets/x",
                  "/api/nope"):
            with self.assertRaises(V.NotFound, msg=p):
                V.route(self.b, p)

    def test_a_team_named_like_a_repo_round_trips(self):
        """A real block names teams "org/repo": the link escapes the slash, and the route reads the team back whole
        (it reaches the consumer view, which says no call sites, instead of "no view at")."""
        link = V.href("consumer", "org/team", repo=K.REPO, symbol=K.SYMBOL)
        self.assertTrue(link.startswith("/consumer/org%2Fteam?"))
        for path in (link, "/api" + link):
            with self.assertRaisesRegex(V.NotFound, "team=org/team$", msg=path):
                V.route(self.b, path)

    def test_scope_from_the_query(self):
        v = V.route(self.b, f"/?repo={K.REPO}&symbol={K.SYMBOL}")[0]
        self.assertEqual(v["scope"], {"repo": K.REPO, "symbol": K.SYMBOL})
        for p in ("/api/staff", "/staff", "/api"):
            self.assertEqual(V.route(self.b, p)[0]["view"], "staff", p)
        for p in (V.href(repo="no/such", symbol=K.SYMBOL), V.href(repo=K.REPO, symbol="nope"), "/api/staff/owner/x",
                  "/staff/x"):
            with self.assertRaises(V.NotFound, msg=p):
                V.route(self.b, p)


class Content(unittest.TestCase):
    """What each view shows beyond its named queries: diffs, stall reasons, drafts."""

    @classmethod
    def setUpClass(cls):
        cls.b = block()

    def test_diffs_on_the_consumer_view_are_the_teams_rows_for_sites_still_present(self):
        shown = {t: [d["target"] for d in section(V.route(self.b, V.href("consumer", t))[0], "Diffs")["diffs"]]
                 for t in ("checkout", "payments")}
        self.assertEqual(shown, {"checkout": ["S_c1"], "payments": []})  # S_c4, S_p2 and S_p3 are removed at R2
        r1 = block(run_id="R1")  # all four present at R1
        self.assertEqual(sorted(d["target"] for d in section(V.route(r1, "/consumer/checkout")[0], "Diffs")["diffs"]),
                         ["S_c1", "S_c4"])
        d = section(V.route(self.b, "/consumer/checkout")[0], "Diffs")["diffs"][0]
        self.assertEqual((d["where"], d["source"]), ("cart/pricing.py:214", "codemod"))

    def test_legacy_rows_on_the_staff_view(self):
        db = K.build()
        db.execute("INSERT INTO suggestion (run_id, suggestion_id, kind, target_kind, target, owner_team, source, "
                   "body, facts_hash, offered_at, agent, route) VALUES ('R2', 'L1', 'legacy', 'file', 'shared/old.py', "
                   "'platform', 'rules', 'old_helper: 3 callers, no ticket', 'f', ?, 'auditor', 'rules')", (K.R2[1],))
        title = "Legacy radar: unfiled legacy code"
        rows = section(V.route(block(db), "/")[0], title)["diffs"]
        self.assertEqual([(d["where"], d["team"], d["body"]) for d in rows],
                         [("shared/old.py", "platform", "old_helper: 3 callers, no ticket")])
        self.assertEqual(section(V.route(block(db, "R1"), "/")[0], title)["diffs"], [])  # offered at R2

    def test_a_reason_for_each_stalled_team_and_none_for_a_blocked_one(self):
        owner = V.route(self.b, V.href("owner", K.SYMBOL))[0]
        stalled = sorted(r[0] for r in section(owner, "Per team")["rows"] if r[4] == "STALLED")
        reasons = section(owner, "Possible reasons, for each stalled team")["texts"]
        self.assertEqual(stalled, ["billing", "checkout", "platform"])
        self.assertEqual(sorted(t["target"] for t in reasons), stalled)  # not fulfillment, which is blocked
        self.assertTrue(all(t["kind"] == "stall_reason" and t["body"] for t in reasons))

    def test_the_ticket_tools_show_a_draft_for_each_team_with_untracked_work(self):
        v = V.route(self.b, "/tickets")[0]
        teams = [r[0] for r in section(v, "Untracked work, one draft per team")["rows"]]
        self.assertTrue(teams)
        self.assertEqual(sorted(t["target"] for t in section(v, "Drafts")["texts"]), sorted(teams))
        self.assertTrue(section(v, "Suggested rewrites")["texts"])

    def test_the_staff_view_shows_02_products_rows(self):
        """Collisions, duplicates, teams not connected and the legacy radar, from KNOWLEDGE-BASE.md's queries."""
        v = V.route(self.b, "/")[0]
        rows = {t: section(v, t)["rows"] for t in (
            "Collisions: two open tickets edit the same function", "Duplicates: two tickets map the same call site",
            "Teams not connected: no ticket maps any of their call sites",
            "Legacy radar: deprecated functions still called")}
        self.assertEqual(list(rows.values()), [
            [["CHK-298", "CHK-311", "cart/pricing.py:price"], ["CHK-298", "PAY-12", "cart/pricing.py:price"]],
            [["CHK-311", "PAY-12", "S_c1"]],
            [["billing", 1]],
            [["auth/client.py", "AuthClient.verify_token", "DeprecationWarning", 8]]])

    def test_a_blocked_team_says_who_it_waits_on(self):
        per_team = {r[0]: r for r in section(V.route(self.b, V.href("owner", K.SYMBOL))[0], "Per team")["rows"]}
        self.assertEqual(per_team["fulfillment"][4:], ["blocked", "platform"])  # S_f1 waits on platform's S_shared
        self.assertEqual({r[5] for t, r in per_team.items() if t != "fulfillment"}, {""})

    def test_every_stall_text_has_its_repo(self):
        """A stall comes from auditor.block_stalls, which points at each (repo, symbol): its repo is never empty,
        so the owner view finds its text under (repo, symbol, team)."""
        stall = [k for k in self.b.texts if k[0] == "stall_reason"]
        self.assertTrue(stall)
        self.assertEqual([k for k in stall if not k[1]], [])

    def test_data_is_escaped(self):
        db = K.build()
        evil = '"><script>alert(1)</script>'
        late_site(db, "S_evil", team=evil)
        page = V.to_html(V.route(block(db), V.href("consumer", evil))[0])
        self.assertNotIn("<script>", page)
        self.assertIn(html.escape(evil), page)


class AsOf(unittest.TestCase):
    """A view of R1 (snapshot Sep 1) shows nothing known only later: sites, teams, rows, pushes or outcomes."""

    def test_a_team_or_symbol_first_known_later_is_not_found_before(self):
        db = K.build()
        late_site(db, "S_late", team="latecomer")
        late_site(db, "S_later", symbol="later_symbol")
        r1, r2 = block(db, "R1"), block(db, "R2")
        for p in ("/consumer/latecomer", V.href("owner", "later_symbol")):
            with self.assertRaises(V.NotFound, msg=p):
                V.route(r1, p)
            V.route(r2, p)
        self.assertEqual(r1.pairs, [(K.REPO, K.SYMBOL, 12)])  # R1's 12 sites, not R2's rows too

    def test_the_default_scope_counts_sites_at_the_snapshot(self):
        db = K.build()
        for i in range(20):  # a symbol with more sites than verify_token, all known only at R2
            late_site(db, f"S_more{i}", symbol="later_symbol")
        self.assertEqual(block(db, "R1").scope(), (K.REPO, K.SYMBOL))
        self.assertEqual(block(db, "R2").scope(), (K.REPO, "later_symbol"))

    def test_push_outcomes_decided_after_the_snapshot_show_open(self):
        db = K.build()
        db.execute("INSERT INTO push_alert VALUES ('f1', 'R2', 'P1', 'cart/x.py:1', 'e', NULL, NULL)")
        r1, r2 = block(db, "R1"), block(db, "R2")
        outcomes = {b.run_id: {r[0]: r[3] for r in V.route(b, "/push/e1")[0]["sections"][0]["rows"]}
                    for b in (r1, r2)}
        self.assertEqual(outcomes, {"R1": {"P1": "open", "P7": "open"}, "R2": {"P1": "fixed", "P7": "ignored"}})
        self.assertEqual([r[0] for r in V.route(r1, "/push")[0]["sections"][0]["rows"]], ["d1", "e1"])
        self.assertEqual([r[0] for r in V.route(r2, "/push")[0]["sections"][0]["rows"]], ["d1", "e1", "f1"])
        with self.assertRaises(V.NotFound):
            V.route(r1, "/push/f1")  # its base run is R2

    def test_a_suggestion_row_offered_later_is_not_shown(self):
        def shown(offered_at):
            db = K.build()
            t = block(db, "R1").text("ticket_draft", K.REPO, K.SYMBOL, "billing")
            fh = G.facts_hash({"facts": t["facts"]})
            db.execute("DELETE FROM outcome")
            db.execute("DELETE FROM suggestion WHERE kind = 'ticket_draft'")
            db.execute("INSERT INTO suggestion (run_id, suggestion_id, kind, target_kind, target, owner_team, source,"
                       " body, facts_hash, offered_at, agent, model, route) VALUES ('R2', ?, 'ticket_draft', 'team', "
                       "'billing', 'billing', 'llm', 'STORED TEXT', ?, ?, 'writer', 'claude-haiku-5-5', 'rules')",
                       (h("ticket_draft", "billing", fh), fh, offered_at))
            return block(db, "R1").text("ticket_draft", K.REPO, K.SYMBOL, "billing")["source"]
        self.assertEqual((shown(K.R1[1]), shown(K.R2[1])), ("llm", "template"))


class Decision8(unittest.TestCase):
    """Each view from the real block where its tables have rows there, else from the fixture, which says so."""

    @classmethod
    def setUpClass(cls):
        db, run_id = V.fixture_db()  # connections the server's threads may read
        db.execute("DELETE FROM push_alert")  # a "real" block with no push-check notes
        db.commit()
        cls.real = V.Block(db, CFG, run_id)
        cls.site = V.Site(cls.real, V.Block(V.fixture_db()[0], CFG, run_id, fixture=True))

    def test_a_view_with_no_rows_on_the_real_block_comes_from_the_fixture(self):
        self.assertEqual(self.site.rows["push"], 0)
        self.assertEqual({v: self.site.pick(v) is self.site.real for v in V.VIEWS},
                         {"staff": True, "owner": True, "consumer": True, "push": False, "tickets": True})
        for p in ("/push", "/api/push", "/push/e1"):
            v = V.route(self.site, p)[0]
            self.assertEqual(v["meta"]["data"], "fixture", p)
            self.assertIn(V.Site.FALLBACK, v["meta"]["note"], p)
            self.assertIn(V.Site.FALLBACK, V.to_html(v), p)
        for p in ("/", V.href("owner", K.SYMBOL), "/consumer/checkout", "/tickets"):
            self.assertEqual(V.route(self.site, p)[0]["meta"]["data"], "real", p)

    def test_a_link_from_a_real_page_opens_the_fixtures_default_page(self):
        """A real page's link names the real block's repo, symbol or team, which the fixture doesn't have."""
        site = V.Site(self.real, self.site.fixture)
        site.source = {v: site.fixture for v in V.VIEWS}  # as if no view had rows on the real block
        for p, view, scope in ((V.href("owner", "url", repo="django/x"), "owner", {"repo": K.REPO, "symbol": K.SYMBOL}),
                               ("/api" + V.href("consumer", "django/x", repo="django/x", symbol="url"), "consumer",
                                {"repo": K.REPO, "symbol": K.SYMBOL, "team": "checkout"}),
                               (V.href(repo="django/x", symbol="url"), "staff", {"repo": K.REPO, "symbol": K.SYMBOL}),
                               ("/push/abc123", "push", {}), ("/push/e1", "push", {"sha": "e1"})):
            v, as_json = V.route(site, p)
            want = (view, scope, "fixture", p.startswith("/api"))
            self.assertEqual((v["view"], v["scope"], v["meta"]["data"], as_json), want, p)

    def test_without_a_fixture_every_view_is_real(self):
        site = V.Site(self.real, None)
        self.assertEqual(V.route(site, "/push")[0]["meta"]["data"], "real")
        self.assertEqual(V.route(site, "/push")[0]["sections"][0]["rows"], [])

    def test_unknown_views_are_not_found(self):
        for p in ("/nope", "/api/nope"):
            with self.assertRaises(V.NotFound, msg=p):
                V.route(self.site, p)

    def test_served_through_the_site(self):
        srv = V.serve(self.site, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/push", timeout=10) as r:
                self.assertEqual(json.loads(r.read())["meta"]["data"], "fixture")
        finally:
            srv.shutdown()
            srv.server_close()


class NoWrites(unittest.TestCase):
    def test_no_view_writes_to_the_block(self):
        """On a writable connection, an authorizer refuses any write to the block's own tables: temp tables only."""
        db = K.build()
        db.commit()
        writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE,
                  sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_TABLE,
                  sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE}
        refused = []

        def guard(action, arg1, arg2, dbname, source):
            if action in writes and dbname == "main":
                refused.append((action, arg1))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(guard)
        b = V.Block(db, CFG, "R2", fixture=True)
        for p in paths(b):
            V.to_html(V.route(b, p)[0])
        self.assertEqual(refused, [])


class Server(unittest.TestCase):
    """The real HTTP server, on 127.0.0.1 and a free port, read-only."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.file = Path(cls.tmp.name) / "block.sqlite"
        src, dst = K.build(), sqlite3.connect(cls.file)
        src.commit()
        src.backup(dst)
        dst.close()
        cls.md5 = hashlib.md5(cls.file.read_bytes()).hexdigest()
        db = sqlite3.connect(cls.file.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        cls.b = V.Block(db, CFG, "R2")
        cls.srv = V.serve(cls.b, 0)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.b.db.close()
        cls.tmp.cleanup()

    def get(self, p):
        with urllib.request.urlopen(self.url + p, timeout=10) as r:
            return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")

    def test_pages_and_json(self):
        status, kind, body = self.get("/")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"))
        self.assertIn("real block", body)  # a file block isn't the fixture
        status, kind, body = self.get("/api/tickets")
        self.assertEqual((status, kind), (200, "application/json; charset=utf-8"))
        self.assertEqual(json.loads(body)["view"], "tickets")

    def test_not_found_and_no_writes(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/consumer/nobody")
        self.assertEqual(e.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as e:  # GET only
            urllib.request.urlopen(urllib.request.Request(self.url + "/", data=b"x", method="POST"), timeout=10)
        self.assertEqual(e.exception.code, 501)
        for p in paths(self.b):
            self.get(p)
        self.assertEqual(hashlib.md5(self.file.read_bytes()).hexdigest(), self.md5)  # the block is untouched

    def test_parallel_requests_get_the_same_answers_as_one_at_a_time(self):
        """Every request re-aims the block's temp tables (A.point), so the server answers one at a time."""
        ps = ["/api" + p for p in paths(self.b)]
        want = {p: self.get(p)[2] for p in ps}
        got, errors = [], []

        def client(i):
            for p in ps * 4:
                try:
                    got.append((p, self.get(p)[2]))
                except Exception as e:  # a dropped connection or a 500
                    errors.append((p, repr(e)))
        threads = [threading.Thread(target=client, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(got), 8 * 4 * len(ps))
        self.assertEqual([p for p, body in got if body != want[p]], [])

    def test_a_failing_view_is_a_500_not_a_dropped_connection(self):
        real = V.route
        V.route = lambda b, path: 1 / 0
        try:
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.get("/")
        finally:
            V.route = real
        self.assertEqual(e.exception.code, 500)
        self.assertNotIn("ZeroDivisionError", e.exception.read().decode())  # the traceback goes to the log only
        self.assertEqual(self.get("/")[0], 200)


class FixtureServer(unittest.TestCase):
    def test_the_fixture_serves_over_http(self):
        """--fixture --serve: the fixture's in-memory block is read from the server's threads."""
        db, run_id = V.fixture_db()
        b = V.Block(db, CFG, run_id, fixture=True)
        srv = V.serve(b, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            for p in ("/", "/api/tickets", V.href("owner", K.SYMBOL)):
                with urllib.request.urlopen(url + p, timeout=10) as r:
                    self.assertEqual(r.status, 200, p)
                    self.assertIn("fixture", r.read().decode("utf-8"), p)
        finally:
            srv.shutdown()
            srv.server_close()
            db.close()


class Command(unittest.TestCase):
    """python -m meridian.views --path: the page's JSON on stdout, nothing else (the pipeline's log is stdout too)."""

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(V.main(list(argv)), 0)
        return json.loads(out.getvalue())

    def test_a_block_file_and_the_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "block.sqlite"
            src, dst = K.build(), sqlite3.connect(file)
            src.commit()
            src.backup(dst)
            dst.execute("DELETE FROM push_alert")  # decision 8: /push comes from the fixture
            dst.commit()
            dst.close()
            push = self.run_main("--run-id", "R2", "--db", str(file), "--path", "/api/push")
            self.assertEqual((push["view"], push["meta"]["data"]), ("push", "fixture"))
            self.assertIn(V.Site.FALLBACK, push["meta"]["note"])
            staff = self.run_main("--run-id", "R2", "--db", str(file), "--path", "/api/")
            self.assertEqual((staff["view"], staff["meta"]["data"]), ("staff", "real"))
            only = self.run_main("--run-id", "R2", "--db", str(file), "--real-only", "--path", "/api/push")
            self.assertEqual((only["meta"]["data"], only["sections"][0]["rows"]), ("real", []))
        self.assertEqual(self.run_main("--fixture", "--path", "/api/tickets")["meta"]["data"], "fixture")

    def test_an_unknown_page_exits_with_its_message(self):
        with self.assertRaises(SystemExit) as e:
            V.main(["--fixture", "--path", "/nope"])
        self.assertEqual(str(e.exception), "no view at /nope")


@unittest.skipUnless(REAL and Path(REAL).exists(), "needs MERIDIAN_VIEWS_BLOCK=<out/<run_id>/meridian.sqlite>")
class RealBlock(unittest.TestCase):
    """Decision 8's measure: which views have rows on a real block. Every page renders, and every number traces."""

    @classmethod
    def setUpClass(cls):
        db = sqlite3.connect(Path(REAL).resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        run_id = db.execute("SELECT run_id FROM run").fetchone()[0]
        cls.b = V.Block(db, CFG, run_id)
        cls.teams, cls.people = G.names(db)

    def test_every_page_renders_and_every_number_traces(self):
        for p in paths(self.b):
            v = V.route(self.b, p)[0]
            self.assertEqual(v["meta"]["data"], "real")
            self.assertEqual(untraced(v, self.teams, self.people), [], p)

    def test_decision_8_each_view_where_it_has_rows(self):
        db, run_id = V.fixture_db()
        site = V.Site(self.b, V.Block(db, CFG, run_id, fixture=True))
        for view in V.VIEWS:
            self.assertEqual(site.pick(view) is self.b, site.rows[view] > 0, view)
        for p in paths(self.b):
            v = V.route(site, p)[0]
            want = "real" if site.pick(v["view"]) is self.b else "fixture"
            self.assertEqual(v["meta"]["data"], want, p)
        db.close()

    def test_no_person_on_any_page(self):
        """The block's people (names over 3 letters: shorter ones match ordinary words) and any email: none shown.
        A failure names the page only, never the person."""
        names = [re.compile(rf"\b{re.escape(n)}\b") for n in self.people if len(n) > 3]
        for p in paths(self.b):
            v = V.route(self.b, p)[0]
            text = visible(V.to_html(v)) + V.to_json(v)
            self.assertFalse(EMAIL.search(text), f"an email on {p}")
            self.assertFalse(any(n.search(text) for n in names), f"a person's name on {p}")


if __name__ == "__main__":
    unittest.main()
