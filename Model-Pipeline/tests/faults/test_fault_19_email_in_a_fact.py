"""Fault 19: A fact containing an email.
Expected: refused before any prompt is built.
Why: no people in facts."""
import json
import unittest

from tests.contracts.make_vishwa import fixture
from tests.test_handoff_writer import good

EMAIL = "ann@example.com"


class EmailInAFact(unittest.TestCase):
    def test_the_task_is_refused_before_any_prompt(self):
        K, agents, cfg = fixture()
        db = K.build()
        db.execute("UPDATE call_site SET path = ? WHERE path = 'cart/totals.py'", (f"cart/{EMAIL}/totals.py",))
        prompts = []

        def llm(model, prompt):
            prompts.append(prompt)
            return good(model, prompt)
        result = agents.run(db, cfg, "R2", K.R2[1], llm=llm)
        self.assertTrue(result.skipped)
        self.assertTrue(all("a fact names a person" in why for _, _, why in result.skipped))
        self.assertTrue(prompts)  # the other tasks still ran
        self.assertFalse([p for p in prompts if EMAIL in p])
        self.assertFalse([w for w in result.written if EMAIL in json.dumps(w.task.facts)])


if __name__ == "__main__":
    unittest.main()
