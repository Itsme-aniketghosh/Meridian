"""model/refresh.py: the split rolls with the block, nothing new is a no-op, a failed cycle undoes itself."""
import contextlib
import io
import json
import sqlite3
import tempfile
import tomllib
import unittest
from pathlib import Path

from model.refresh import NOTHING_NEW, cycle, roll, rolled_split

HERE = Path(__file__).resolve().parents[1]
TOML = (HERE / "model.toml").read_text(encoding="utf-8")
RUN = tomllib.loads(TOML)["data"]["run_id"]


def publish(folder, run_id, snapshot_at):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "latest.json").write_text(json.dumps({"run_id": run_id, "file": "b.sqlite"}), encoding="utf-8")
    con = sqlite3.connect(folder / "b.sqlite")
    con.execute("create table if not exists run (run_id text, snapshot_at text)")
    con.execute("delete from run")
    con.execute("insert into run values (?, ?)", (run_id, snapshot_at))
    con.commit()
    con.close()


class Split(unittest.TestCase):
    def test_todays_block_gives_todays_split(self):
        self.assertEqual(rolled_split("2026-10-01T00:00:00Z", 365, 2015),
                         ("2025-10-01T00:00:00Z", [2015, 2022], 2023, 2024))

    def test_rolling_to_todays_block_changes_nothing(self):
        self.assertEqual(roll(TOML, RUN, "2026-10-01T00:00:00Z"), TOML)

    def test_a_year_later_everything_moves_one_year(self):
        new = tomllib.loads(roll(TOML, "abcdef0123456789", "2027-10-01T00:00:00Z"))
        old = tomllib.loads(TOML)
        self.assertEqual(new["data"]["run_id"], "abcdef0123456789")
        self.assertEqual(new["data"]["follow_up_end"], "2026-10-01T00:00:00Z")
        self.assertEqual((new["split"]["train"], new["split"]["val"], new["split"]["test"]), ([2015, 2023], 2024, 2025))
        self.assertFalse(new["split"]["test_allowed"])
        for section in set(old) - {"data", "split"}:
            self.assertEqual(new[section], old[section], section)


class Cycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.config, self.pub, self.data = t / "model.toml", t / "published", t / "data"
        self.config.write_text(TOML, encoding="utf-8")
        self.data.mkdir()
        (self.data / "features.parquet").write_bytes(b"old table")
        self.built, self.ran = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, code=0, **kw):
        def build(args):
            self.built.append(args)
            (self.data / "features.parquet").write_bytes(b"new table")

        def run(plan):
            self.ran.append([s[0] for s in plan])
            return code

        with contextlib.redirect_stdout(io.StringIO()):
            return cycle(self.config, self.pub, run=run, build=build, data_dir=self.data, **kw)

    def test_nothing_new_is_a_no_op(self):
        publish(self.pub, RUN, "2026-10-01T00:00:00Z")
        self.assertEqual(self.go(), NOTHING_NEW)
        self.assertEqual((self.built, self.ran), ([], []))

    def test_a_new_block_rolls_rebuilds_and_runs_every_step(self):
        publish(self.pub, "abcdef0123456789", "2027-10-01T00:00:00Z")
        self.assertEqual(self.go(), 0)
        self.assertEqual(tomllib.loads(self.config.read_text(encoding="utf-8"))["data"]["run_id"], "abcdef0123456789")
        self.assertEqual(len(self.built), 1)
        self.assertEqual(self.ran, [["data", "train", "seed noise", "evaluate", "bias", "gate", "registry"]])
        self.assertEqual(self.go(), NOTHING_NEW)          # the next cycle on the same block does nothing

    def test_a_failed_cycle_puts_everything_back(self):
        publish(self.pub, "abcdef0123456789", "2027-10-01T00:00:00Z")
        self.assertEqual(self.go(code=1), 1)
        self.assertEqual(self.config.read_text(encoding="utf-8"), TOML)
        self.assertEqual((self.data / "features.parquet").read_bytes(), b"old table")

    def test_dry_run_changes_nothing(self):
        publish(self.pub, "abcdef0123456789", "2027-10-01T00:00:00Z")
        self.assertEqual(self.go(dry_run=True), 0)
        self.assertEqual(self.config.read_text(encoding="utf-8"), TOML)
        self.assertEqual((self.built, self.ran), ([], []))

    def test_refuses_during_the_test_run(self):
        self.config.write_text(TOML.replace("test_allowed = false", "test_allowed = true"), encoding="utf-8")
        publish(self.pub, "abcdef0123456789", "2027-10-01T00:00:00Z")
        with self.assertRaises(SystemExit):
            self.go()


if __name__ == "__main__":
    unittest.main()
