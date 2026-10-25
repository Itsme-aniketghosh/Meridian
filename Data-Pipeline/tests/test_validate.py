"""Validate and alert tests: schema, formats, ranges, coverage, missing values, outliers, drift, and the alert channels.
The block is the KB fixture after graph (tests/test_dashboard.py's fixture_block).

    python -m unittest tests.test_validate -v      # from Data-Pipeline/
"""
import io, json, shutil, sqlite3, sys, tempfile, textwrap, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.graph as G
import meridian.validate as V
from meridian import alert
from tests.test_dashboard import fixture_block
from tests.test_graph import CONFIG

BASE = """\
    [validate.rows]
    run = 1
    call_site = 1
    [validate.coverage]
    tickets_mapped = {floor}
    [validate.missing]
    default = {missing}
    [validate.ranges]
    "file_owner.share" = [0.0, 1.0]
    [[validate.formats]]
    name = "UTC times"
    columns = '.*_at|effective_date'
    pattern = '\\d{{4}}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\dZ'
    """
IDS = """\
    [[validate.formats]]
    name = "16-hex content IDs"
    columns = 'call_site_id|blocker_id'
    pattern = '[0-9a-f]{16}'
    """


def per_column_stats(db, table, cols, top_n):
    """validate.column_stats as it was, one or two queries per column: the reference the one-query version must equal."""
    rows = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    out = {}
    for name, typ, *_ in cols:
        q = f'"{name}"'
        missing, distinct = db.execute(f"SELECT sum({q} IS NULL OR {q} = ''), count(DISTINCT {q}) FROM \"{table}\"").fetchone()
        s = {"missing": missing or 0, "distinct": distinct}
        if typ in V.NUMERIC:
            lo, hi, mean = db.execute(f'SELECT min({q}), max({q}), avg({q}) FROM "{table}"').fetchone()
            s |= {"min": lo, "max": hi, "mean": None if mean is None else round(mean, 4)}
        if 0 < distinct <= top_n and distinct < rows:
            s["top"] = [list(r) for r in db.execute(
                f'SELECT {q}, count(*) FROM "{table}" GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {top_n}')]
        out[name] = s
    return rows, out


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "meridian.toml").write_text(CONFIG)
        self.out = fixture_block(self.tmp)
        with redirect_stdout(io.StringIO()):
            G.main("R2", self.tmp / "meridian.toml", self.tmp)

    def run_it(self, extra="", floor=0.5, missing=1.0, prev_run=None):
        exp = self.tmp / "settings.toml"
        exp.write_text(textwrap.dedent(BASE.format(floor=floor, missing=missing)) + textwrap.dedent(extra))
        with redirect_stdout(io.StringIO()), mock.patch.object(alert, "send") as sent:
            try:
                V.main("R2", self.tmp / "meridian.toml", self.tmp, prev_run, exp)
                failed = None
            except SystemExit as e:
                failed = str(e)
        return failed, json.loads((self.out / "anomalies.json").read_text()), sent

    def test_a_clean_block_passes_and_writes_its_statistics(self):
        failed, found, sent = self.run_it()
        self.assertIsNone(failed)
        self.assertEqual(found, [])
        sent.assert_not_called()
        stats = json.loads((self.out / "stats.json").read_text())["tables"]
        self.assertEqual(stats["call_site"]["rows"], 17)
        self.assertEqual(stats["call_site"]["columns"]["status"]["top"], [["present", 12], ["removed", 5]])
        self.assertEqual(stats["file_owner"]["columns"]["share"]["max"], 1.0)

    def test_one_query_per_table_gives_the_same_statistics_as_one_per_column(self):
        self.run_it()
        spec = V.W.tables(V.W.SCHEMA.read_text(encoding="utf-8"))
        with sqlite3.connect(self.out / "meridian.sqlite") as db:
            for table, cols in sorted(spec.items()):
                new, old = V.column_stats(db, table, cols, 10), per_column_stats(db, table, cols, 10)
                self.assertEqual(json.dumps(new, sort_keys=True), json.dumps(old, sort_keys=True), table)
        db.close()

    def test_a_bad_format_is_an_error_and_alerts(self):
        failed, found, sent = self.run_it(IDS)   # the fixture's IDs are readable names, not hashes
        self.assertIn("data errors", failed)
        self.assertIn(["error", "formats", "call_site.call_site_id"], [f[:3] for f in found])
        sent.assert_called_once()

    def test_a_schema_violation_stops_before_anything_else(self):
        with open(self.out / "ticket_map.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"run_id": "R2", "key": "X-1", "target_kind": "fuzzy", "target": "", "method": "none"}) + "\n")
        failed, found, _ = self.run_it()
        self.assertTrue(failed.startswith("schema"))
        self.assertEqual([f[:2] for f in found], [["error", "schema"]])

    def test_coverage_below_its_floor_is_an_error(self):
        failed, found, _ = self.run_it(floor=0.9)   # 6 of 7 tickets mapped
        self.assertIsNotNone(failed)
        self.assertIn(["error", "coverage", "tickets_mapped"], [f[:3] for f in found])

    def test_missing_values_warn_but_the_run_goes_on(self):
        failed, found, sent = self.run_it(missing=0.0)
        self.assertIsNone(failed)
        self.assertIn(["warn", "missing", "ticket.parent_key"], [f[:3] for f in found])
        sent.assert_called_once()

    def test_drift_from_the_previous_run_warns(self):
        prev = self.tmp / "out" / "R1"
        prev.mkdir(parents=True)
        (prev / "stats.json").write_text(json.dumps({"run_id": "R1", "tables": {"call_site": {"rows": 5}}}))
        failed, found, _ = self.run_it(prev_run="R1")
        self.assertIsNone(failed)
        self.assertIn(["warn", "drift", "call_site"], [f[:3] for f in found])


class Outliers(unittest.TestCase):
    def test_iqr_fences_raw_and_log(self):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE t (x INTEGER)")
        db.executemany("INSERT INTO t VALUES (?)", [(v,) for v in [10] * 19 + [11] * 19 + [1000, 2000]])
        share, lo, hi = V.outliers(db, "t", "x", 3.0)
        self.assertEqual((share, lo, hi), (2 / 40, 7, 14))
        self.assertLess(V.outliers(db, "t", "x", 3.0, log_scale=True)[2], 1000)   # still outside on a log scale
        db.execute("CREATE TABLE few (x INTEGER)")
        db.executemany("INSERT INTO few VALUES (?)", [(v,) for v in range(5)])
        self.assertIsNone(V.outliers(db, "few", "x", 3.0))                     # too few values to say


class Dates(unittest.TestCase):
    def test_a_date_before_earliest_or_after_the_snapshot_warns(self):
        db = sqlite3.connect(":memory:")
        db.executescript("CREATE TABLE run (snapshot_at TEXT); INSERT INTO run VALUES ('2026-10-01T00:00:00Z');"
                         "CREATE TABLE ticket (created_at TEXT); INSERT INTO ticket VALUES ('0010-12-17T12:12:00Z'),"
                         "('2014-01-01T00:00:00Z'), ('2027-01-01T00:00:00Z'), ('');")
        exp = {"dates": {"columns": ".*_at", "earliest": "2000-01-01T00:00:00Z"}}
        found = V.check(db, {"run": [], "ticket": [("created_at", "TEXT", 0, 0, 0)]}, {}, exp, None)
        self.assertEqual([f[:3] for f in found], [["warn", "dates", "ticket.created_at"]])
        self.assertIn("2 dates", found[0][3])


class Alert(unittest.TestCase):
    def settings(self, text):
        p = Path(tempfile.mkdtemp()) / "settings.toml"
        self.addCleanup(shutil.rmtree, p.parent, True)
        p.write_text(textwrap.dedent(text))
        return p

    def test_logged_without_channels_and_a_broken_channel_never_raises(self):
        quiet = self.settings('[alerts]\non = ["failure", "error", "warn"]\n')
        with redirect_stdout(io.StringIO()) as log, mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(alert.send("subject", ["line"], "error", quiet), [])
        self.assertIn("ALERT subject\nline", log.getvalue())
        broken = self.settings("""\
            [alerts]
            on = ["error"]
            email_to = ["team@example.com"]
            smtp_host = "127.0.0.1"
            smtp_port = 9
            slack_webhook_env = "HOOK"
            timeout_s = 2
            """)
        with redirect_stdout(io.StringIO()) as log, mock.patch.dict("os.environ", {"HOOK": "http://127.0.0.1:9/x"}, clear=True):
            self.assertEqual(alert.send("subject", [], "error", broken), [])
        self.assertIn("Slack failed", log.getvalue())
        self.assertIn("email failed", log.getvalue())

    def test_slack_gets_the_text_only_for_events_it_is_on_for(self):
        s = self.settings('[alerts]\non = ["error"]\nslack_webhook_env = "HOOK"\nmax_lines = 1\n')
        with redirect_stdout(io.StringIO()), mock.patch.dict("os.environ", {"HOOK": "https://hooks.example/x"}, clear=True), \
                mock.patch("urllib.request.urlopen") as post:
            self.assertEqual(alert.send("subject", ["a", "b"], "error", s), ["slack"])
            self.assertEqual(alert.send("subject", ["a"], "warn", s), [])        # warn isn't in on: logged only
        self.assertEqual(json.loads(post.call_args[0][0].data), {"text": "subject\na\n... and 1 more"})


if __name__ == "__main__":
    unittest.main()
