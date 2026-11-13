"""Fault 20: Auditor replay with a future call-site row.
Expected: The row is ignored. Ticket maps known later are the one known gap (auditor decisions, Nov 10).
Why: No `known_at` leak."""
import unittest

from tests.contracts.make_vishwa import fixture

LATER = ("R1b", "2026-09-15T00:00:00Z")  # a run between R1 and R2


class FutureRow(unittest.TestCase):
    def setUp(self):
        self.K, _, self.cfg = fixture()
        from meridian import auditor
        self.A = auditor

    def planted(self):
        """The fixture, plus billing's only call site removed on Sep 14 and known only at R1b: billing would be done."""
        db = self.K.build()
        db.execute("INSERT INTO run (run_id, snapshot_at, repo_shas, source_hashes, config_hash, image_digest, status) "
                   "VALUES (?, ?, '{}', '{}', 'x', 'x', 'complete')", LATER)
        cols = [r[1] for r in db.execute("PRAGMA table_info(call_site)")]
        row = dict(zip(cols, db.execute("SELECT * FROM call_site WHERE call_site_id = 'S_b1' ORDER BY known_at DESC")
                       .fetchone()))
        row.update(run_id=LATER[0], status="removed", effective_date="2026-09-14T00:00:00Z", known_at=LATER[1])
        db.execute(f"INSERT INTO call_site ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                   [row[c] for c in cols])
        return db

    def stalls(self, db, at):
        return {x.team: (x.since, x.left, x.facts) for x in self.A.block_stalls(db, self.cfg, at, "R2")}

    def test_replayed_at_r1_the_future_row_changes_nothing(self):
        r1 = self.K.R1[1]
        self.assertEqual(self.stalls(self.planted(), r1), self.stalls(self.K.build(), r1))

    def test_once_it_is_known_it_counts(self):
        at = "2026-09-20T00:00:00Z"
        self.assertNotEqual(self.stalls(self.planted(), at), self.stalls(self.K.build(), at))


if __name__ == "__main__":
    unittest.main()
