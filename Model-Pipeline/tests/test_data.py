"""model/data.py on a tiny made-up history (packets/utkarsh-data, Tests). Run from Model-Pipeline/:

python -m unittest tests.test_data
"""
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

from model import data

SCHEMA = Path(__file__).resolve().parents[2] / "Data-Pipeline" / "schema.sql"
RUN, REPO = "fixture0000000001", "o/r"

CONFIG = f"""
[data]
run_id = "{RUN}"
window_days = 365
follow_up_end = "2019-01-01T00:00:00Z"
start_year = 2015
repo = "{REPO}"
prior_fix_days = 365

[split]
train = [2015, 2016]
val = 2017
test = 2018
test_allowed = false

[seed]
value = 1
"""

# (sha, authored_at, author email, subject, [(path, added, deleted)])
COMMITS = [
    ("c0", "2014-06-01T00:00:00Z", "alice@a.org", "start", [("core/a.py", 10, 0)]),
    ("c1", "2015-01-10T00:00:00Z", "alice@b.org", "[SPARK-9] grow a", [("core/a.py", 5, 1)]),  # f1 is 365 days on
    ("c2", "2015-02-01T00:00:00Z", "bob@a.org", "[MINOR] grow b", [("sql/x/b.py", 4, 0), ("img.png", None, None)]),
    ("f1", "2016-01-10T00:00:00Z", "bob@a.org", "[SPARK-1] fix a", [("core/a.py", 1, 1)]),
    ("c3", "2016-01-20T00:00:00Z", "carol@a.org", "touch a", [("core/a.py", 2, 2)]),
    ("f2", "2016-02-02T00:00:00Z", "carol@a.org", "[SPARK-2] fix b", [("sql/x/b.py", 1, 1)]),  # 366 days after c2
    ("c4", "2017-03-01T00:00:00Z", "alice@a.org", "val", [("core/a.py", 1, 0)]),
    ("c5", "2018-05-01T00:00:00Z", "bob@a.org", "test", [("core/a.py", 1, 0)]),
    ("c6", "2019-03-01T00:00:00Z", "bob@a.org", "late", [("core/a.py", 1, 0)]),
]
ALIAS = [("alice@a.org", "P1", "Alice A"), ("alice@b.org", "P1", "Alice A"), ("bob@a.org", "P2", "Bob B"),
         ("carol@a.org", "P3", "Carol C")]
TICKETS = [("SPARK-1", "Bug", "Fixed", '["Core"]'), ("SPARK-2", "Bug", "Fixed", '["SQL"]'),
           ("SPARK-9", "Improvement", "Fixed", "[]")]
LINKS = [("f1", "SPARK-1"), ("f2", "SPARK-2"), ("c1", "SPARK-9")]
BLAME = [("f1", "SPARK-1", "core/a.py", "c1"), ("f2", "SPARK-2", "sql/x/b.py", "c2")]


def make_block(folder):
    db = Path(folder) / f"{RUN}.sqlite"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.execute("insert into run values (?, '2020-01-15T00:00:00Z', '{}', '{}', 'x', 'x', '{}', 'complete', '')", [RUN])
    parent = None
    for seq, (sha, at, email, subject, files) in enumerate(COMMITS):
        con.execute("insert into git_commit values (?,?,?,?,?,1,0,?,?,?,?)",
                    [RUN, REPO, sha, parent, seq, at, email, email, subject])
        for path, a, d in files:
            con.execute("insert into commit_file values (?,?,?,?,NULL,?,?)", [RUN, REPO, sha, path, a, d])
        parent = sha
    for email, pid, name in ALIAS:
        con.execute("insert into person_alias values (?,?,?,?,0,'manual')", [RUN, email, pid, name])
    for key, typ, res, comp in TICKETS:
        con.execute("insert into ticket (run_id, key, tracker, type, effective_type, status, resolution, created_at, "
                    "components, bulk_closed, desc_empty, summary) values (?,?,'jira',?,?,'Resolved',?,"
                    "'2014-01-01T00:00:00Z',?,0,0,'s')", [RUN, key, typ, typ, res, comp])
    for sha, key in LINKS:
        con.execute("insert into commit_ticket values (?,?,?,?,'subject')", [RUN, REPO, sha, key])
    for fix, key, path, ind in BLAME:
        con.execute("insert into bug_inducing values (?,?,?,?,?,?)", [RUN, REPO, fix, key, path, ind])
    con.commit()
    con.close()
    (Path(folder) / "latest.json").write_text(json.dumps({"run_id": RUN, "file": db.name}), encoding="utf-8")


def build(tmp, out="out"):
    cfg = Path(tmp) / "model.toml"
    cfg.write_text(CONFIG, encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        data.main(["--config", str(cfg), "--published", tmp, "--out", str(Path(tmp) / out)])
    return Path(tmp) / out


class FeatureTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        make_block(cls.tmp.name)
        cls.out = build(cls.tmp.name)
        cls.df = pd.read_parquet(cls.out / "features.parquet").set_index("sha")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_window_edges(self):
        self.assertEqual(self.df.loc["c1", "label"], 1)  # fixed exactly 365 days later: inside
        self.assertEqual(self.df.loc["c2", "label"], 0)  # fixed 366 days later: outside
        self.assertEqual(int(self.df.label.sum()), 1)

    def test_prior_bug_fixes_never_sees_the_future(self):
        self.assertEqual(self.df.loc["c1", "prior_bug_fixes"], 0)  # f1 comes after c1
        self.assertEqual(self.df.loc["c3", "prior_bug_fixes"], 1)  # f1 came 10 days before c3

    def test_exp_merges_emails(self):
        self.assertEqual(self.df.loc["c1", "exp"], 1)  # c0 is Alice under her other email
        self.assertEqual(self.df.loc["c4", "exp"], 2)
        self.assertEqual(self.df.loc["c1", "ndev"], 1)  # only Alice touched core/a.py before
        self.assertEqual(self.df.loc["c3", "ndev"], 2)  # Alice and Bob

    def test_no_person_data(self):
        df = self.df.reset_index()
        self.assertEqual(list(df.columns), [n for n, _, _ in data.COLUMNS])
        for col in df.columns:
            self.assertFalse(df[col].astype(str).str.contains("@", regex=False).any(), col)
        names = {n.lower() for _, _, full in ALIAS for n in full.split()} | {p for _, p, _ in ALIAS}
        for col in df.columns:
            self.assertFalse(df[col].astype(str).str.lower().isin(names).any(), col)
        self.assertFalse(any(k in c for c in df.columns if c != "authored_at"
                             for k in ("name", "email", "person", "author")))

    def test_splits_hold_their_years(self):
        df = self.df
        self.assertNotIn("c0", df.index)  # before start_year
        years = {s: set(g.year) for s, g in df.groupby("split")}
        self.assertEqual(years, {"train": {2015, 2016}, "val": {2017}, "test": {2018}, "none": {2019}})
        self.assertTrue((df[df.split == "none"].authored_at > "2019-01-01T00:00:00Z").all())
        self.assertTrue((df[df.split != "none"].authored_at <= "2019-01-01T00:00:00Z").all())

    def test_same_input_same_md5(self):
        again = build(self.tmp.name, "out2")
        a, b = (hashlib.md5((p / "features.parquet").read_bytes()).hexdigest() for p in (self.out, again))
        self.assertEqual(a, b)
        self.assertEqual(json.loads((self.out / "features.schema.json").read_text())["md5"], a)

    def test_change_features(self):
        df = self.df
        self.assertEqual(df.loc["c2", ["la", "ld", "nf", "ns"]].tolist(), [4, 0, 2, 2])  # binary: 0 lines, still a file
        self.assertEqual(df.loc["c2", "module"], "sql/x")  # one level down inside sql/
        self.assertEqual(df.loc["c2", "is_minor"], 1)
        self.assertEqual(df.loc["c1", "entropy"], 0.0)
        self.assertEqual((df.loc["c1", "ticket_type"], df.loc["c1", "fix"], df.loc["c1", "has_ticket"]),
                         ("Improvement", 0, 1))
        self.assertEqual((df.loc["f1", "fix"], df.loc["f1", "component"]), (1, "Core"))
        self.assertEqual((df.loc["c3", "has_ticket"], df.loc["c3", "ticket_type"]), (0, "none"))
        self.assertEqual(df.loc["c3", "nuc"], 3)  # c0, c1, f1

    def test_latest_must_match_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "latest.json").write_text(json.dumps({"run_id": "other", "file": "x.sqlite"}))
            with self.assertRaises(SystemExit):
                data.open_block(tmp, RUN)


if __name__ == "__main__":
    unittest.main()
