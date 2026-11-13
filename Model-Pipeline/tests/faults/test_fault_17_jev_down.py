"""Fault 17: Jev down.
Expected: rules pick; the run completes.
Why: 04-architecture's fallback."""
import unittest

from tests.contracts.make_vishwa import fixture


def unreachable(model, prompt):
    raise ConnectionError("Jev's endpoint is unreachable")


class JevDown(unittest.TestCase):
    def run_with(self, llm):
        K, agents, cfg = fixture()
        from meridian import jev
        db = K.build()
        teams, people = agents.names(db)
        result = agents.run(db, cfg, "R2", K.R2[1], router=jev.Router(llm=llm, teams=teams, people=people))
        self.assertTrue(result.written)
        self.assertEqual({w.route for w in result.written}, {"rules"})

    def test_jev_off_rules_pick_and_the_run_completes(self):
        self.run_with(None)

    def test_jev_unreachable_rules_pick_and_the_run_completes(self):
        self.run_with(unreachable)


if __name__ == "__main__":
    unittest.main()
