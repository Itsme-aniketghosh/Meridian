"""The knowledge base proof: every ```sql block in KNOWLEDGE-BASE.md that has a `-- name:` line runs against a
fixture KB built from schema.sql, and must return exactly its `-- expect:` rows. The doc can't claim a view the
schema can't answer, and a schema change that breaks a planned view fails here.

    python -m unittest tests.test_kb -v      # from Data-Pipeline/

The fixture is the 02-product story in one repo (acme/shop), two runs a month apart. join and graph don't exist
yet, so their tables (call_site, ticket_map, blocked_by, chain, blast_radius) are filled by hand here: this proves
the KB can carry and answer the views, and the derive_* queries prove graph can compute its tables from the inputs.
call_site_ids are readable names, not hashes, so the expectations read like the story.
"""
import json, re, sqlite3, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from meridian.common import SCHEMA

DOC = Path(__file__).resolve().parents[1] / "KNOWLEDGE-BASE.md"
REPO = "acme/shop"
R1, R2 = ("R1", "2026-09-01T00:00:00Z"), ("R2", "2026-10-01T00:00:00Z")
SYMBOL = "verify_token"


def site(sid, run, path, line, scope, team, status="present", eff="2026-01-10T00:00:00Z", bug=0, exc=0, junk="none"):
    return dict(call_site_id=sid, run_id=run[0], repo=REPO, path=path, line=line, col=4, scope=scope, symbol=SYMBOL,
                role="call", detection="ast_resolved", junk=junk, in_except=exc, owner_team=team, bug_fixes=bug,
                status=status, effective_date=eff, effective_sha="e" * 40, known_at=run[1])


SITES = [  # R1 writes every site; R2 writes only what changed (append-only)
    site("S_shared", R1, "shared/client.py", 40, "get_client", "platform", bug=1),
    site("S_c1", R1, "cart/pricing.py", 214, "price", "checkout", bug=4),
    site("S_c2", R1, "cart/pricing.py", 231, "price", "checkout", bug=4, exc=1),
    site("S_c4", R1, "cart/pricing.py", 250, "price", "checkout", bug=4),
    site("S_c3", R1, "cart/totals.py", 57, "total", "checkout"),
    site("S_f1", R1, "ship/label.py", 10, "make_label", "fulfillment"),
    site("S_p1", R1, "pay/charge.py", 5, "charge", "payments"),
    site("S_p2", R1, "pay/charge.py", 9, "refund", "payments"),
    site("S_p3", R1, "pay/refund.py", 3, "refund", "payments"),
    site("S_b1", R1, "billing/invoice.py", 12, "bill", "billing"),
    site("S_s1", R1, "search/q.py", 1, "query", "search", status="removed", eff="2026-08-01T00:00:00Z"),
    site("S_s2", R1, "search/q.py", 7, "query", "search"),
    site("S_c4", R2, "cart/pricing.py", 250, "price", "checkout", status="removed", eff="2026-09-12T00:00:00Z", bug=4),
    site("S_s2", R2, "search/q.py", 7, "query", "search", status="removed", eff="2026-09-10T00:00:00Z"),
    site("S_p2", R2, "pay/charge.py", 9, "refund", "payments", status="removed", eff="2026-09-20T00:00:00Z"),
    site("S_p3", R2, "pay/refund.py", 3, "refund", "payments", status="removed", eff="2026-09-25T00:00:00Z"),
    site("S_new", R2, "pay/charge.py", 30, "charge", "payments", eff="2026-09-28T00:00:00Z"),
]


def ticket(key, team, status, summary, description="", resolution=None, typ="Task"):
    return dict(run_id="R2", key=key, tracker="jira", type=typ, effective_type=typ, parent_key=None, status=status,
                resolution=resolution, created_at="2026-06-01T00:00:00Z",
                resolved_at="2026-09-15T00:00:00Z" if resolution else None, components=json.dumps([team]),
                assignee=None, reporter=None, bulk_closed=0, desc_empty=int(not description), summary=summary,
                description=description)


TICKETS = [
    ticket("PLAT-2291", "platform", "Open", "Migrate shared client off verify_token", "Done when no caller of verify_token is left."),
    ticket("CHK-311", "checkout", "Open", "Fix auth stuff in checkout"),
    ticket("CHK-298", "checkout", "In Progress", "Migrate cart pricing tail", "Done when pricing.py:250 shows removed."),
    ticket("FUL-7", "fulfillment", "Open", "Fulfillment auth migration", "Waiting on the platform client."),
    ticket("PAY-10", "payments", "Closed", "Payments auth migration", "Done when charge.py is clean.", resolution="Fixed"),
    ticket("PAY-12", "payments", "Open", "Pricing check for payments", "Done when pricing.py:214 is removed."),
    ticket("OPS-1", "ops", "Open", "Rotate keys", "Done when rotated."),
]
TICKET_MAP = [("PLAT-2291", "S_shared"), ("CHK-311", "S_c1"), ("CHK-311", "S_c2"), ("CHK-298", "S_c4"),
              ("FUL-7", "S_f1"), ("PAY-10", "S_p1"), ("PAY-12", "S_c1")]

FILES = {"shared/client.py": ("platform", "p_pat", 1), "cart/pricing.py": ("checkout", "p_ann", 4),
         "cart/totals.py": ("checkout", "p_bob", 0), "ship/label.py": ("fulfillment", "p_fay", 0),
         "pay/charge.py": ("payments", "p_pia", 0), "pay/refund.py": ("payments", "p_pia", 0),
         "billing/invoice.py": ("billing", "p_bea", 0), "search/q.py": ("search", "p_sam", 0)}
PEOPLE = {"p_pat": ("pat@acme.test", "platform"), "p_ann": ("ann@acme.test", "checkout"), "p_bob": ("bob@acme.test", "checkout"),
          "p_fay": ("fay@acme.test", "fulfillment"), "p_pia": ("pia@acme.test", "payments"),
          "p_bea": ("bea@acme.test", "billing"), "p_sam": ("sam@acme.test", "search")}
COMMITS = (  # (sha, author, authored_at, [(path, added, deleted)])
    [("a1", "p_ann", "2026-07-01T10:00:00Z", [("cart/pricing.py", 5, 1)])]                       # Ann, then silent
    + [(f"b{i}", "p_bob", f"2026-08-{2 + i * 3:02d}T10:00:00Z", [("cart/ui.py", 3, 1)]) for i in range(8)]  # usual: 8 in 56 days
    + [(f"c{i}", "p_bob", f"2026-09-{18 + i * 3:02d}T10:00:00Z", [("cart/ui.py", 3, 1)]) for i in range(4)]  # last 14 days: 4
    + [("d1", "p_pia", "2026-09-28T10:00:00Z", [("pay/charge.py", 30, 0), ("pay/api.py", 10, 0)])]
)
EDGES = [  # (caller_path, caller_scope, line, callee_path, callee_scope): depth 1 = calls of the symbol itself
    ("cart/pricing.py", "price", 214, "auth/client.py", "AuthClient.verify_token"),
    ("cart/pricing.py", "price", 231, "auth/client.py", "AuthClient.verify_token"),
    ("cart/totals.py", "total", 57, "auth/client.py", "AuthClient.verify_token"),
    ("shared/client.py", "get_client", 40, "auth/client.py", "AuthClient.verify_token"),
    ("ship/label.py", "make_label", 10, "auth/client.py", "AuthClient.verify_token"),
    ("pay/charge.py", "charge", 5, "auth/client.py", "AuthClient.verify_token"),
    ("pay/charge.py", "charge", 30, "auth/client.py", "AuthClient.verify_token"),
    ("billing/invoice.py", "bill", 12, "auth/client.py", "AuthClient.verify_token"),
    ("checkout/api.py", "handler", 5, "cart/pricing.py", "price"),
    ("ship/label.py", "make_label", 9, "shared/client.py", "get_client"),
    ("web/views.py", "page", 3, "cart/totals.py", "total"),
    ("web/views.py", "page", 8, "checkout/api.py", "handler"),
]


SUGGESTIONS = [  # (id, kind, target_kind, target, team, outcome, decided_at, latency_days): all offered in R1
    ("SG1", "diff", "call_site", "S_c4", "checkout", "applied", "2026-09-12T00:00:00Z", 11),
    ("SG2", "diff", "call_site", "S_p2", "payments", "edited", "2026-09-20T00:00:00Z", 19),
    ("SG3", "diff", "call_site", "S_p3", "payments", "applied", "2026-09-25T00:00:00Z", 24),
    ("SG4", "diff", "call_site", "S_c1", "checkout", "ignored", "2026-10-01T00:00:00Z", None),
    ("SG5", "ticket_draft", "team", "billing", "billing", "ignored", "2026-09-15T00:00:00Z", None),
    ("SG6", "rewrite", "ticket", "CHK-311", "checkout", "ignored", "2026-10-01T00:00:00Z", None),
]


def build():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA.read_text(encoding="utf-8"))
    for name, sql, _, _ in blocks():
        if name.startswith("schema_"):  # tables the doc proposes and schema.sql doesn't have yet
            db.executescript(sql)
    ins = lambda t, rows: rows and db.executemany(
        f"INSERT INTO {t} ({', '.join(rows[0])}) VALUES ({', '.join('?' * len(rows[0]))})", [list(r.values()) for r in rows])
    ins("run", [dict(run_id=r, snapshot_at=at, repo_shas="{}", source_hashes="{}", config_hash="x", image_digest="x",
                     status="complete") for r, at in (R1, R2)])
    ins("call_site", SITES)
    ins("ticket", TICKETS)
    ins("ticket_map", [dict(run_id="R2", key=k, target_kind="call_site", target=s, method="symbol") for k, s in TICKET_MAP]
        + [dict(run_id="R2", key="OPS-1", target_kind="unmapped", target="", method="none")])
    ins("file_owner", [dict(run_id="R2", repo=REPO, path=p, owner_team=t, share=1.0, top_person_id=who, n_commits=3,
                            last_commit_at=None, basis="author") for p, (t, who, _) in FILES.items()])
    ins("file_bug_fixes", [dict(run_id="R2", repo=REPO, path=p, bug_fixes=n, fix_keys=json.dumps([f"BUG-{i}" for i in range(n)]),
                                window_start="2025-10-01T00:00:00Z", window_end=R2[1]) for p, (_, _, n) in FILES.items() if n])
    ins("person", [dict(run_id="R2", person_id=p, name=p[2:].title(), is_bot=0) for p in PEOPLE])
    ins("person_alias", [dict(run_id="R2", email=e, person_id=p, name=p[2:].title(), generic=0, source="single")
                         for p, (e, _) in PEOPLE.items()])
    ins("team_map", [dict(run_id="R2", dataset="shop", subject_kind="person", subject=p, team=t, rule="person_component")
                     for p, (_, t) in PEOPLE.items()])
    ins("git_commit", [dict(run_id="R2", repo=REPO, sha=sha, parent_sha=None, seq=i, first_parent=1, is_merge=0, authored_at=at,
                            author_email=PEOPLE[who][0], committer_email=PEOPLE[who][0], subject=f"change {sha}")
                       for i, (sha, who, at, _) in enumerate(COMMITS)])
    ins("commit_file", [dict(run_id="R2", repo=REPO, sha=sha, path=p, old_path=None, added=a, deleted=d)
                        for sha, _, _, fs in COMMITS for p, a, d in fs])
    ins("resolution", [dict(run_id="R2", repo=REPO, path=s["path"], line=s["line"], col=s["col"], symbol=SYMBOL,
                            detection="ast_resolved", via="direct", resolved_to="auth.client.AuthClient.verify_token",
                            resolver="jedi==0.19.2",
                            receiver_from=f"{REPO}:shared/client.py:get_client" if s["call_site_id"] == "S_f1" else None)
                       for s in SITES if s["run_id"] == "R1" or s["call_site_id"] == "S_new"])
    ins("call_edge", [dict(run_id="R2", repo=REPO, caller_path=cp, caller_scope=cs, line=ln, col=4, callee_path=ep,
                           callee_repo=REPO, callee_scope=es, introduced_sha="e" * 40) for cp, cs, ln, ep, es in EDGES])
    ins("module_import", [dict(run_id="R2", repo=REPO, path="tests/test_totals.py", line=1, module="cart.totals", name="total", is_test=1),
                          dict(run_id="R2", repo=REPO, path="checkout/api.py", line=1, module="cart.pricing", name="price", is_test=0),
                          # `from shared import client` / `from ship import label`: the module is parent + name
                          dict(run_id="R2", repo=REPO, path="ship/label.py", line=2, module="shared", name="client", is_test=0),
                          dict(run_id="R2", repo=REPO, path="tests/test_ship.py", line=1, module="ship", name="label", is_test=1)])
    ins("deprecation", [dict(run_id="R2", repo=REPO, path="auth/client.py", line=88, scope="AuthClient.verify_token",
                             qualname="auth.client.AuthClient.verify_token", marker="DeprecationWarning")])
    ins("blocked_by", [dict(run_id="R2", call_site_id="S_f1", blocker_id="S_shared", via_function=f"{REPO}:shared/client.py:get_client",
                            depth=1, status="resolved")])
    ins("chain", [dict(run_id="R2", from_key=a, to_key=b, type=t, evidence=json.dumps(ev), in_cycle=0) for a, b, t, ev in [
        ("FUL-7", "PLAT-2291", "depends_on", ["S_f1>S_shared"]), ("CHK-311", "PAY-12", "duplicates", ["S_c1"]),
        ("CHK-298", "CHK-311", "collides", [f"{REPO}:cart/pricing.py:price"]),
        ("CHK-298", "PAY-12", "collides", [f"{REPO}:cart/pricing.py:price"]),
        ("billing", "", "untracked", ["S_b1"]), ("checkout", "", "untracked", ["S_c3"]), ("payments", "", "untracked", ["S_new"])]])
    ins("blast_radius", [dict(run_id="R2", repo=REPO, symbol=SYMBOL, direct_sites=8, invocations=12, files=8,
                              by_depth=json.dumps({"1": 8, "2": 3, "3": 1}))])
    ins("coverage", [dict(run_id="R2", metric="tickets_mapped", numerator=6, denominator=7, note="OPS-1 unmapped")])
    ins("suggestion", [dict(run_id="R1", suggestion_id=i, kind=k, target_kind=tk, target=t, owner_team=team,
                            source="codemod" if k == "diff" else "template", body="", facts_hash="x", offered_at=R1[1])
                       for i, k, tk, t, team, *_ in SUGGESTIONS])
    ins("outcome", [dict(suggestion_id=i, decided_run_id="R2", outcome=o, decided_at=at, latency_days=lat, rule="fixture",
                         evidence="[]") for i, _, _, _, _, o, at, lat in SUGGESTIONS])
    ins("push_alert", [dict(push_sha="d1", base_run_id="R1", check_id="P1", target="pay/charge.py:30", evidence="[]",
                            outcome="ignored", decided_at="2026-09-28T00:00:00Z"),
                       dict(push_sha="e1", base_run_id="R1", check_id="P1", target="pay/charge.py:31", evidence="[]",
                            outcome="fixed", decided_at="2026-09-29T00:00:00Z"),
                       dict(push_sha="e1", base_run_id="R1", check_id="P7", target="cart/pricing.py", evidence="[]",
                            outcome="ignored", decided_at="2026-09-29T00:00:00Z")])
    db.execute("CREATE TEMP TABLE asof (run_id TEXT, at TEXT, repo TEXT, symbol TEXT)")
    db.execute("INSERT INTO asof VALUES (?, ?, ?, ?)", (*R2, REPO, SYMBOL))
    return db


def blocks():
    """[(name, sql, params, expect)] from the doc, in order. A block without `-- expect:` is setup (views)."""
    out = []
    for body in re.findall(r"```sql\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S):
        name = re.search(r"^-- name: (\S+)", body, re.M)
        if not name:
            continue
        expect = re.search(r"^-- expect: (.*)$", body, re.M)
        params = re.search(r"^-- params: (.*)$", body, re.M)
        out.append((name.group(1), body, json.loads(params.group(1)) if params else {},
                    json.loads(expect.group(1)) if expect else None))
    return out


class KnowledgeBase(unittest.TestCase):
    def test_every_doc_query_returns_what_the_doc_says(self):
        db, found = build(), blocks()
        self.assertGreater(len(found), 20, "KNOWLEDGE-BASE.md lost its queries")
        for name, sql, params, expect in found:
            with self.subTest(name):
                if name.startswith("schema_"):
                    continue  # already run by build()
                if expect is None:
                    db.executescript(sql)
                    continue
                got = [list(r) for r in db.execute(sql, params).fetchall()]
                self.assertEqual(got, expect, f"{name}: the doc's expect line is wrong, or the KB can't answer it")


    def test_file_module_view_names_modules_like_the_scanner(self):
        """The doc's path -> module rule and scan.module_of must agree, or imports and deprecations stop joining."""
        from meridian.scan import module_of
        db = build()
        paths = ["django/utils/translation/__init__.py", "django/utils/translation/trans_real.py", "setup.py"]
        db.executemany("INSERT INTO call_site VALUES (?, 'R2', 'acme/shop', ?, 1, 0, '<module>', 'verify_token', 'call', 'ast_resolved', "
                       "'none', 0, 't', 0, 'present', ?, ?, ?)", [(f"X{i}", p, R2[1], "e" * 40, R2[1]) for i, p in enumerate(paths)])
        db.executescript(next(sql for name, sql, _, _ in blocks() if name == "setup"))
        got = dict(db.execute(f"SELECT path, module FROM file_module WHERE path IN ({', '.join('?' * len(paths))})", paths))
        self.assertEqual(got, {p: module_of(p) for p in paths})

    def test_scope_done_case_and_blast_depth(self):
        """A block holds many symbols and trackers: another symbol's site must not leak into the asof one, GitLab's
        lowercase 'closed' is done, and derive_blast_radius counts each edge once, as graph does, through a self-call."""
        import meridian.graph as G
        db, q = build(), {name: sql for name, sql, _, _ in blocks()}
        db.execute("INSERT INTO call_site VALUES ('S_x', 'R2', ?, 'cart/totals.py', 60, 4, 'total', 'other', 'call', "
                   "'ast_resolved', 'none', 0, 'checkout', 0, 'present', ?, ?, ?)", (REPO, R2[1], "e" * 40, R2[1]))
        db.execute("INSERT INTO ticket VALUES ('R2', 'shop#1', 'gitlab', 'Task', 'Task', NULL, 'closed', NULL, ?, NULL, "
                   "'[\"checkout\"]', NULL, NULL, 0, 0, 'Remove totals call', 'Done when totals.py is clean.')", (R2[1],))
        db.execute("INSERT INTO ticket_map VALUES ('R2', 'shop#1', 'call_site', 'S_c3', 'symbol')")
        db.execute("INSERT INTO call_edge VALUES ('R2', ?, 'cart/pricing.py', 'price', 300, 4, 'cart/pricing.py', ?, 'price', ?)",
                   (REPO, REPO, "e" * 40))
        db.executescript(q["setup"])
        self.assertEqual(db.execute(q["staff_headline"]).fetchall(), [(8, 5)])  # not S_x, not shop#1
        self.assertIn(("checkout", "shop#1", "S_c3"), db.execute(q["stall_ticket_mismatch"]).fetchall())
        db.row_factory = sqlite3.Row
        res, edges = (list(map(dict, db.execute(f"SELECT * FROM {t}"))) for t in ("resolution", "call_edge"))
        row = next(r for r in G.build_blast_radius("R2", [], res, edges, 3) if r["symbol"] == SYMBOL)
        db.row_factory = None
        self.assertEqual({str(d): n for d, n in db.execute(q["derive_blast_radius"])}, json.loads(row["by_depth"]))
        db.execute("UPDATE asof SET symbol = NULL")  # NULL: every symbol
        self.assertEqual(db.execute(q["staff_headline"]).fetchall(), [(9, 5)])


if __name__ == "__main__":
    unittest.main()
