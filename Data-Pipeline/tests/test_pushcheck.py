"""Push check tests: one PR that trips every check, P1–P9, plus the scope (importers in, test importers out), a quiet
PR, a missing base, the size limits, a path list instead of a range, and that the block is never written. No network:
a local git repo and a block built from schema.sql, its call sites scanned from the base commit as scan and join would.

    python -m unittest tests.test_pushcheck -v      # from Data-Pipeline/
"""
import contextlib, hashlib, io, json, os, sqlite3, subprocess, sys, tempfile, tomllib, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.pushcheck as P
from meridian.common import SCHEMA
from meridian.scan import scan_repo, tracked

REPO = "acme/shop"
CFG_TEXT = """
[datasets.shop]
repos = [{ repo = "acme/shop", ref = "HEAD" }]
tracker = "jira"
symbols = [{ name = "verify_token", module = "auth.client" }]

[tickets]
key_regex_generic = '\\b[A-Z][A-Z0-9]+-\\d+\\b'

[scanner]
embedded_python = []
skip_paths = []
keep_name_only = true
edit_roles = ["import", "call", "alias"]
deprecation_markers = ['DeprecationWarning']
junk = { test = ["tests/"] }
"""
CFG = tomllib.loads(CFG_TEXT)
SETTINGS = {"max_scope_files": 200, "max_lines": 50, "defect_risk_threshold": 10}
BASE_AT = "2026-10-01T00:00:00Z"

BASE = {
    "auth/client.py": "import warnings\n\ndef verify_token():\n    warnings.warn('old', DeprecationWarning)\n",
    "cart/pricing.py": "def price():\n    verify_token()\n    return 1\n",
    "checkout/api.py": "from cart.pricing import price\n\ndef handler():\n    verify_token()\n    return price()\n",
    "pay/charge.py": "def charge():\n    verify_token()\n",
    "shared/client.py": "def get_client():\n    return 1\n",
    "ship/label.py": "from shared.client import get_client\n\ndef make_label():\n    get_client().verify_token()\n",
    "billing/invoice.py": "def bill():\n    verify_token()\n",
    "search/q.py": "def query():\n    return 1\n",
    "tests/test_pricing.py": "from cart.pricing import price\n",
}
HEAD = {  # Pia (payments) pushes
    "cart/pricing.py": "def price():\n    verify_token()\n    return 2\n",                        # P3: edits checkout's price
    "pay/charge.py": "def charge():\n    verify_token()\n\ndef refund():\n    verify_token()\n",  # P1, P2, P7
    "shared/client.py": "def get_client():\n    verify_token()\n    return 1\n",                 # P4: label waits on it
    "billing/invoice.py": "def bill():\n    return 0\n",                                        # P5: BILL-1's last site
    "search/q.py": "def query():\n    verify_token()\n    return 1\n",                           # P6: SRCH-1 is Done
}
TEAMS = {"auth": "platform", "cart": "checkout", "checkout": "checkout", "pay": "payments", "shared": "platform",
         "ship": "fulfillment", "billing": "billing", "search": "search", "tests": "checkout"}
TICKETS = [("CHK-1", "checkout", "Open", "cart/pricing.py"), ("CHK-2", "checkout", "Open", "checkout/api.py"),
           ("PAY-1", "payments", "Open", "pay/charge.py"), ("BILL-1", "billing", "Open", "billing/invoice.py"),
           ("SRCH-1", "search", "Done", "search/q.py")]


def sh(co, *args, env=None):
    return subprocess.run(["git", "-C", str(co), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **(env or {})}).stdout


def commit(co, files, who, message):
    for p, text in files.items():
        (co / p).parent.mkdir(parents=True, exist_ok=True)
        (co / p).write_text(text, encoding="utf-8", newline="\n")
    sh(co, "add", "-A")
    sh(co, "commit", "-q", "-m", message, env={"GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": f"{who}@acme.test",
                                              "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": f"{who}@acme.test"})


def block(path, co):
    """The base block: base commit scanned (sites, imports, deprecations), owners, tickets, one resolution per site."""
    sites, _ = P.head_sites(REPO, co, list(BASE), CFG)
    with tempfile.TemporaryDirectory() as tmp:  # the Done ticket's site, removed before the base: its ID, from its line
        (Path(tmp) / "search").mkdir()
        (Path(tmp) / "search/q.py").write_text(HEAD["search/q.py"], encoding="utf-8")
        removed, _ = P.head_sites(REPO, Path(tmp), ["search/q.py"], CFG)
    _, _, _, imports, deprecations = scan_repo("R1", REPO, co, tracked(CFG)[REPO], CFG["scanner"])
    team = lambda p: TEAMS[p.split("/")[0]]
    db = sqlite3.connect(path)
    db.executescript(SCHEMA.read_text(encoding="utf-8"))
    ins = lambda t, rows: rows and db.executemany(
        f"INSERT INTO {t} ({', '.join(rows[0])}) VALUES ({', '.join('?' * len(rows[0]))})", [list(r.values()) for r in rows])
    ins("run", [dict(run_id="R1", snapshot_at=BASE_AT, repo_shas="{}", source_hashes="{}", config_hash="x", image_digest="x",
                     status="complete")])
    cs = [dict(call_site_id=i, run_id="R1", repo=REPO, path=s["path"], line=s["line"], col=s["col"], scope=s["scope"],
               symbol=s["symbol"], role=s["role"], detection="ast_resolved", junk=s["junk"], in_except=s["in_except"],
               owner_team=team(s["path"]), bug_fixes=0, status=st, effective_date="2026-01-01T00:00:00Z", known_at=BASE_AT)
          for d, st in ((sites, "present"), (removed, "removed")) for i, s in d.items()]
    ins("call_site", cs)
    ins("resolution", [dict(run_id="R1", repo=REPO, path=s["path"], line=s["line"], col=s["col"], symbol=s["symbol"],
                            detection="ast_resolved", via="direct", resolved_to="auth.client.verify_token", resolver="jedi",
                            receiver_from=f"{REPO}:shared/client.py:get_client" if s["path"] == "ship/label.py" else None)
                       for s in cs if s["status"] == "present"])
    ins("module_import", imports)
    ins("deprecation", deprecations)
    ins("file_owner", [dict(run_id="R1", repo=REPO, path=p, owner_team=team(p), share=1.0, top_person_id=None, n_commits=1,
                            last_commit_at=None, basis="author") for p in BASE])
    ins("file_bug_fixes", [dict(run_id="R1", repo=REPO, path=p, bug_fixes=n, fix_keys=json.dumps([f"PAY-{i}" for i in range(n)]),
                                window_start="2025-10-01T00:00:00Z", window_end=BASE_AT)
                           for p, n in (("pay/charge.py", 4), ("cart/pricing.py", 1))])
    ins("ticket", [dict(run_id="R1", key=k, tracker="jira", type="Task", effective_type="Task", parent_key=None, status=st,
                        resolution=None, created_at="2026-06-01T00:00:00Z", resolved_at=None, components=json.dumps([t]),
                        assignee=None, reporter=None, bulk_closed=0, desc_empty=1, summary=k) for k, t, st, _ in TICKETS])
    ins("ticket_map", [dict(run_id="R1", key=k, target_kind="call_site", target=s["call_site_id"], method="symbol")
                       for k, _, _, p in TICKETS for s in cs if s["path"] == p])
    ins("person_alias", [dict(run_id="R1", email="pia@acme.test", person_id="p_pia", name="Pia", generic=0, source="single")])
    ins("team_map", [dict(run_id="R1", dataset="shop", subject_kind="person", subject="p_pia", team="payments", rule="person")])
    db.commit()
    db.close()


class PushCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.co, cls.base = Path(cls.tmp.name) / "shop", Path(cls.tmp.name) / "meridian.sqlite"
        cls.co.mkdir()
        sh(cls.co, "init", "-q")
        commit(cls.co, BASE, "ann", "start")
        block(cls.base, cls.co)
        commit(cls.co, HEAD, "pia", "PAY-99 add refund, see CHK-1")
        cls.digest = hashlib.sha256(cls.base.read_bytes()).hexdigest()
        cls.out = P.push_check(cls.base, REPO, cls.co, [], CFG, SETTINGS, "HEAD~1...HEAD")
        cls.lines = cls.out.splitlines()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def found(self, check):
        return [l for l in self.lines if l.startswith(f"- **{check} ")]

    def test_p1_drift_new_call_sites(self):
        self.assertEqual([l.split(" at ")[1] for l in self.found("P1")],
                         ["`pay/charge.py:5`", "`search/q.py:2`", "`shared/client.py:2`"])

    def test_p2_legacy_use_new_calls_to_a_deprecated_symbol(self):
        self.assertEqual(len(self.found("P2")), 3)
        self.assertIn("`pay/charge.py:5` calls `verify_token`, marked `DeprecationWarning`", self.found("P2")[0])

    def test_p3_collision_edited_function_and_importer_but_not_own_team(self):
        p3 = "\n".join(self.found("P3"))
        self.assertIn("edits `price` in `cart/pricing.py`: mapped to CHK-1 (Open)", p3)
        self.assertIn("`handler` in `checkout/api.py` imports a changed file: mapped to CHK-2 (Open)", p3)
        self.assertNotIn("PAY-1", p3)  # Pia is payments: her own team's ticket is no collision

    def test_p4_new_block(self):
        self.assertEqual(self.found("P4"), ["- **P4 New block**: `ship/label.py:4` is now blocked by `shared/client.py:2`"])

    def test_p5_ticket_can_move(self):
        self.assertEqual(self.found("P5"), ["- **P5 Ticket can move**: removes the last call site of BILL-1: the ticket can move"])

    def test_p6_mismatch_readds_a_done_tickets_site(self):
        self.assertEqual(self.found("P6"), ["- **P6 Mismatch**: re-adds `search/q.py:2`, a call site of SRCH-1, which is done"])

    def test_p7_risky_edit_only_files_over_the_bar(self):
        self.assertEqual(len(self.found("P7")), 1)  # pricing.py has 1 fix, and its site didn't change
        self.assertIn("`pay/charge.py`: 4 bug fixes in the window (PAY-0, PAY-1, PAY-2, …)", self.found("P7")[0])

    def test_p8_bad_key(self):
        self.assertEqual(len(self.found("P8")), 1)  # CHK-1 is in the snapshot
        self.assertIn("names PAY-99, not a ticket in the snapshot", self.found("P8")[0])

    def test_p9_defect_risk(self):
        # +3 pricing(1+1), charge 3+0, client 1+0, invoice 1+1, q 1+0: 9 lines in 5 files
        self.assertEqual(self.found("P9"), ["- **P9 Defect risk**: 9 lines × 5 files = 45, over 10"])

    def test_scope_is_changed_files_plus_non_test_importers(self):
        self.assertTrue(self.lines[0] == P.MARKER and "5 changed files, 2 importers" in self.lines[1], self.lines[:2])
        db = sqlite3.connect(self.base)  # pricing's test imports it too, but test importers are out of scope
        self.assertEqual(db.execute("SELECT count(*) FROM module_import WHERE path = 'tests/test_pricing.py'").fetchone()[0], 1)
        db.close()

    def test_never_writes_the_block(self):
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), self.digest)

    def test_quiet_when_nothing_to_say_or_no_base(self):
        self.assertEqual(P.push_check(self.base, REPO, self.co, [], CFG, SETTINGS, "HEAD...HEAD"), "")
        self.assertEqual(P.push_check(self.base.with_name("missing.sqlite"), REPO, self.co, [], CFG, SETTINGS, "HEAD~1...HEAD"), "")
        self.assertEqual(P.push_check(self.base, "acme/other", self.co, [], CFG, SETTINGS, "HEAD~1...HEAD"), "")  # not watched

    def test_limits(self):
        out = P.push_check(self.base, REPO, self.co, [], CFG, SETTINGS | {"max_lines": 2}, "HEAD~1...HEAD")
        self.assertEqual(out.splitlines()[-1], f"- … and {sum(l.startswith('- **P') for l in self.lines) - 2} more")
        out = P.push_check(self.base, REPO, self.co, [], CFG, SETTINGS | {"max_scope_files": 6}, "HEAD~1...HEAD")
        self.assertIn("Skipped: 7 files in scope, over 6.", out)

    def test_path_list_has_no_p8_or_p9(self):
        out = P.push_check(self.base, REPO, self.co, ["pay/charge.py"], CFG, SETTINGS)
        self.assertIn("**P1 Drift**", out)
        self.assertNotIn("**P8", out)
        self.assertNotIn("**P9", out)

    def test_command_line(self):
        cfg = Path(self.tmp.name) / "meridian.toml"
        cfg.write_text(CFG_TEXT, encoding="utf-8")
        buf = io.StringIO()
        buf.reconfigure = lambda **k: None
        with contextlib.redirect_stdout(buf):
            P.main(["--base", str(self.base), "--repo", REPO, "--changed", "pay/charge.py", "--checkout", str(self.co),
                    "--config", str(cfg)])
        self.assertIn("new call site of `verify_token` at `pay/charge.py:5`", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
