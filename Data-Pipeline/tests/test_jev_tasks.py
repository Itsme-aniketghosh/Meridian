"""Decision 19's task set (meridian/jev_tasks.py), on KNOWLEDGE-BASE.md's fixture: the draw, the split, the sheet."""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tests.test_kb as K
from meridian import agents as G
from meridian import jev_tasks as JT
from meridian.common import CONFIG, load_config


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class TaskSet(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db = K.build()
        cls.tasks = G.tasks(db, load_config(CONFIG), "R2", K.R2[1])
        cls.teams, cls.people = G.names(db)

    def test_draw_spreads_over_kinds_and_splits_each_half_and_half(self):
        drawn = JT.draw(self.tasks, self.people, n=8, seed=1)
        self.assertEqual(len(drawn), 8)
        kinds = {t.kind for t, _ in drawn}
        self.assertEqual(kinds, {t.kind for t in self.tasks})          # every kind gets some
        for kind in kinds:
            splits = [s for t, s in drawn if t.kind == kind]
            self.assertLessEqual(abs(splits.count("prompt") - splits.count("held_out")), 1)

    def test_same_seed_same_tasks(self):
        a = [(G.facts_hash(t.facts), s) for t, s in JT.draw(self.tasks, self.people, n=8, seed=3)]
        b = [(G.facts_hash(t.facts), s) for t, s in JT.draw(self.tasks, self.people, n=8, seed=3)]
        self.assertEqual(a, b)

    def test_more_than_the_block_has_takes_them_all(self):
        self.assertEqual(len(JT.draw(self.tasks, self.people, n=60)), len(self.tasks))

    def test_a_task_naming_a_person_is_never_drawn(self):
        person = self.people[0]
        named = [G.Task(**{**t.__dict__, "facts": {"facts": {"who": person}, "source": {}}}) for t in self.tasks[:3]]
        drawn = JT.draw(named + self.tasks, self.people, n=60)
        self.assertFalse(any(person in json.dumps(t.facts) for t, _ in drawn))

    def test_sheet_and_facts_file(self):
        drawn = JT.draw(self.tasks, self.people, n=8, seed=1)
        with tempfile.TemporaryDirectory() as d:
            sheet = JT.write(drawn, d)
            rows = read(sheet)
            facts = json.loads((Path(d) / "jev_tasks_facts.json").read_text())
        self.assertEqual(len(rows), 8)
        self.assertEqual(tuple(rows[0]), JT.COLUMNS)
        self.assertTrue(all(r["label"] == "" for r in rows))
        self.assertEqual([r["task_id"] for r in rows], [f["task_id"] for f in facts])
        self.assertEqual(facts[0]["facts"], drawn[0][0].facts)

    def test_labels_must_all_be_filled_with_a_known_size(self):
        drawn = JT.draw(self.tasks, self.people, n=4, seed=1)
        with tempfile.TemporaryDirectory() as d:
            sheet = JT.write(drawn, d)
            rows = read(sheet)
            for r, label in zip(rows, ["small", "Mid", "large", ""]):
                r["label"] = label
            with open(sheet, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=JT.COLUMNS)
                w.writeheader()
                w.writerows(rows)
            with self.assertRaisesRegex(ValueError, "not one of"):
                JT.read_labels(sheet)
            rows[3]["label"] = "small"
            with open(sheet, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=JT.COLUMNS)
                w.writeheader()
                w.writerows(rows)
            got = JT.read_labels(sheet)
        self.assertEqual(sorted(v[1] for v in got.values()), ["large", "mid", "small", "small"])


if __name__ == "__main__":
    unittest.main()
