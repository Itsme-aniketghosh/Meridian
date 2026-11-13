"""Jev's model comparison (meridian/jev_compare.py), with fake models on the fixture: scoring against hand labels,
same pick twice, refusals, and decisions 21, 14 and 13 by their written rules."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import agents as G
from meridian import jev_compare as C
from meridian import jev_tasks as JT
from meridian.common import CONFIG, load_config

RUNGS = {"small": G.SMALL, "mid": G.MID, "large": G.LARGE}


def labelled_set(folder, labels_by_kind):
    """The fixture's tasks as a decision-19 set, every task held out, labelled by kind."""
    db = K.build()
    tasks = G.tasks(db, load_config(CONFIG), "R2", K.R2[1])
    _, people = G.names(db)
    drawn = [(t, "held_out") for t, _ in JT.draw(tasks, people, n=60)]
    sheet = JT.write(drawn, folder)
    with open(sheet, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["label"], r["labelled_by"] = labels_by_kind.get(r["kind"], "small"), "RS"
    with open(sheet, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=JT.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    facts = json.loads((Path(folder) / "jev_tasks_facts.json").read_text())
    for f in facts:
        f["split"] = "held_out"
    (Path(folder) / "jev_tasks_facts.json").write_text(json.dumps(facts))
    return len(rows)


class Models:
    """Fake models: each answers a fixed rung, or bad JSON, or fails; with a price per call."""

    def __init__(self, answers, usd=None):
        self.answers, self.usd, self.calls = answers, usd or {}, []

    def __call__(self, model, prompt):
        self.calls.append(model)
        a = self.answers[model]
        if a == "fail":
            raise RuntimeError("HTTP 403: not on this project")
        kind = prompt.split("Task kind: ", 1)[1].split("\n", 1)[0]
        reply = a if a == "not json" else json.dumps({"agent": "writer", "model": RUNGS[a(kind) if callable(a) else a],
                                                      "reason": "Short and routine."})
        return {"reply": reply, "tokens_in": 500, "tokens_out": 20, "cost_usd": self.usd.get(model, 0.0)}


class Compare(unittest.TestCase):
    def run_compare(self, answers, usd=None, labels_by_kind=None):
        with tempfile.TemporaryDirectory() as d:
            n = labelled_set(d, labels_by_kind or {"stall_reason": "mid"})
            llm = Models(answers, usd)
            r = C.main(["--labels", d, "--models", *answers, "--cache", str(Path(d) / "cache")], llm=llm)
            saved = json.loads((Path(d) / "jev_compare.json").read_text())
        return r, n, llm, saved

    def test_scored_against_the_labels_and_the_rules_always_start_small(self):
        r, n, _, _ = self.run_compare({"gemma-4": "small", "llama-4-scout": lambda k: "mid" if k == "stall_reason"
                                       else "small"})
        stalls = sum(1 for p in r["rules"]["picks"] if p == "small")
        self.assertEqual(r["rules"]["tasks"], n)
        self.assertEqual(r["models"]["gemma-4"]["right"], r["rules"]["right"])       # small everywhere = the rules
        self.assertEqual(r["models"]["llama-4-scout"]["right"], n)                   # matches every label
        self.assertEqual(stalls, n)

    def test_the_second_ask_comes_from_the_cache_and_matches(self):
        r, n, llm, _ = self.run_compare({"gemma-4": "small"})
        self.assertTrue(r["models"]["gemma-4"]["same_twice"])
        self.assertEqual(llm.calls.count("gemma-4"), n)                              # n calls, not 2n

    def test_a_refused_answer_is_a_rules_pick(self):
        r, n, _, _ = self.run_compare({"gemma-4": "not json"})
        self.assertEqual(r["models"]["gemma-4"]["refused"], n)
        self.assertEqual(r["models"]["gemma-4"]["right"], r["rules"]["right"])

    def test_a_model_the_project_cant_reach_is_reported_not_run(self):
        r, _, _, saved = self.run_compare({"gemma-4": "small", "gemini-flash": "fail"})
        self.assertIn("error", r["models"]["gemini-flash"])
        self.assertIn("gemma-4", r["decisions"]["14"]["picked"])
        self.assertIn("decisions", saved)


class Decide(unittest.TestCase):
    def row(self, right, usd=0.0, p95=1.0):
        return {"right": right, "usd_per_pick": usd, "p95_s": p95, "tasks": 30}

    def test_14_the_cheapest_unless_another_gets_3_more(self):
        rules = self.row(28)
        self.assertEqual(C.decide({"gemma-4": self.row(25), "gemini-flash": self.row(27)}, rules)["14"]["picked"],
                         "gemma-4")
        self.assertEqual(C.decide({"gemma-4": self.row(25), "gemini-flash": self.row(28)}, rules)["14"]["picked"],
                         "gemini-flash")

    def test_21_local_within_3_and_fast_enough(self):
        rules = self.row(28)
        d = C.decide({"phi4-mini": self.row(24, p95=4.0), "gemma-4": self.row(27)}, rules)
        self.assertEqual(d["21"]["picked"], "phi4-mini")
        d = C.decide({"phi4-mini": self.row(23, p95=4.0), "gemma-4": self.row(27)}, rules)
        self.assertEqual(d["21"]["picked"], "gemma-4")
        d = C.decide({"phi4-mini": self.row(27, p95=12.0), "gemma-4": self.row(27)}, rules)
        self.assertEqual(d["21"]["picked"], "gemma-4")

    def test_13_jev_only_if_more_right_and_no_dearer_than_the_rules(self):
        rules = self.row(28)
        self.assertEqual(C.decide({"phi4-mini": self.row(29), "gemma-4": self.row(29)}, rules)["13"]["picked"],
                         "jev (phi4-mini)")
        self.assertEqual(C.decide({"phi4-mini": self.row(28), "gemma-4": self.row(28)}, rules)["13"]["picked"],
                         "rules")
        self.assertEqual(C.decide({"gemma-4": self.row(30, usd=0.0001)}, rules)["13"]["picked"], "rules")


if __name__ == "__main__":
    unittest.main()
