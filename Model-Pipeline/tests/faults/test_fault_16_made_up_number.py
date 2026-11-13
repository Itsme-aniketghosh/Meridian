"""Fault 16: An LLM text with a made-up number.
Expected: the gate replaces it with the template; the fallback rate counts it.
Why: the gate's whole job."""
import json
import unittest

from tests.contracts.make_vishwa import fixture


def made_up(model, prompt):
    """Every model, every task: the facts' team (or ticket key) and a number no fact holds."""
    facts = json.loads(prompt.split("Facts:\n", 1)[1])
    name = "key" if "key" in facts else "team"
    text = f"{facts[name]} has 977 call sites left."
    return {"reply": json.dumps({"text": text, "used": [name]}), "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0}


class MadeUpNumber(unittest.TestCase):
    def test_the_template_ships_and_the_fallback_counts_it(self):
        K, agents, cfg = fixture()
        result = agents.run(K.build(), cfg, "R2", K.R2[1], llm=made_up)
        self.assertTrue(result.written)
        for w in result.written:
            self.assertEqual((w.source, w.text), ("template", w.task.template))  # whole, never the number cut out
            self.assertEqual({a[2] for a in w.attempts}, {"gate_miss"})
        counts = agents.fallback_counts(result.written)
        self.assertEqual({k: t for k, (t, _) in counts.items()}, {k: n for k, (_, n) in counts.items()})
        self.assertEqual(sum(r["templates"] for r in agents.report(result).values()), len(result.written))


if __name__ == "__main__":
    unittest.main()
