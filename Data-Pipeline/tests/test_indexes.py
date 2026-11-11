"""The block's indexes (gap 10): the writer builds them, and --index adds them to a block written before them.

    python -m unittest tests.test_indexes -v      # from Data-Pipeline/

That they change no row is tests/test_golden.py's job: it hashes every table's rows, not the file.
"""
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meridian.graph as G
import meridian.writer as W
from tests.test_dashboard import fixture_block
from tests.test_graph import CONFIG


def names(path):
    with closing(sqlite3.connect(path)) as db:
        return {n for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}


def rows(path):
    with closing(sqlite3.connect(path)) as db:
        return {t: db.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
                for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")}


class Indexes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "meridian.toml").write_text(CONFIG)
        out = fixture_block(cls.tmp)
        with redirect_stdout(StringIO()):
            G.main("R2", cls.tmp / "meridian.toml", cls.tmp)
        cls.block = cls.tmp / "block.sqlite"
        W.write(out, cls.block)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_writer_builds_every_index(self):
        want = {sql.split()[5] for sql in W.INDEXES}
        self.assertLessEqual(want, names(self.block))
        with closing(sqlite3.connect(self.block)) as db:
            self.assertEqual(W.missing_indexes(db), [])

    def test_an_old_block_gets_them_in_place_with_the_same_rows_and_twice_is_a_no_op(self):
        old = self.tmp / "old.sqlite"
        shutil.copy(self.block, old)
        with closing(sqlite3.connect(old)) as db:
            for sql in W.INDEXES:
                db.execute(f"DROP INDEX {sql.split()[5]}")
            self.assertEqual(len(W.missing_indexes(db)), len(W.INDEXES))
        before = rows(old)
        with closing(sqlite3.connect(old, isolation_level=None)) as db:
            self.assertEqual(len(W.index(db)), len(W.INDEXES))
            self.assertEqual(W.index(db), [])
            self.assertEqual(W.missing_indexes(db), [])
        self.assertEqual(rows(old), before)


if __name__ == "__main__":
    unittest.main()
