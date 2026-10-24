"""DAG tests. The spec (dags/pipeline_spec.py) runs anywhere: every table a part reads is written upstream of it, and
every task is one command. The Airflow DAG itself is checked where Airflow is installed (the Docker image):

    python -m unittest tests.test_dag -v                                              # from Data-Pipeline/
    docker compose run --rm airflow-scheduler python -m unittest tests.test_dag -v    # with Airflow
"""
import importlib.util, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "dags"))
import pipeline_spec as P
from meridian.common import SCHEMA, load_settings
from meridian.writer import tables


def ancestors(part):
    seen, todo = set(), list(P.STEPS[part][0])
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo += P.STEPS[p][0]
    return seen


class Spec(unittest.TestCase):
    def test_every_table_a_part_reads_is_written_upstream(self):
        all_tables = set(tables(SCHEMA.read_text(encoding="utf-8")))
        for part, (_, reads, _) in P.STEPS.items():
            written = {t for up in ancestors(part) for t in P.STEPS[up][2]}
            need = all_tables if reads == ("*",) else set(reads)
            self.assertEqual(need - written, set(), f"{part} reads tables nothing upstream writes")

    def test_every_schema_table_has_one_writer(self):
        writers = [t for _, _, w in P.STEPS.values() for t in w]
        self.assertEqual(sorted(writers), sorted(set(writers)))
        self.assertEqual(set(writers), set(tables(SCHEMA.read_text(encoding="utf-8"))))

    def test_order_is_a_run_order_and_parts_are_modules(self):
        order = list(P.STEPS)
        for part, (upstream, _, _) in P.STEPS.items():
            self.assertTrue(all(order.index(u) < order.index(part) for u in upstream), part)
        for part in P.PARTS:
            self.assertTrue((HERE / "meridian" / f"{part}.py").exists(), part)

    def test_commands(self):
        self.assertEqual(P.command("join", "R", "meridian.toml", "py", "--prev-run Q"),
                         "py -m meridian.join --run-id R --config meridian.toml --prev-run Q")
        self.assertEqual(P.command("graph", "R", "meridian.toml", "py", "--prev-run Q"),
                         "py -m meridian.graph --run-id R --config meridian.toml")      # only join and validate take it
        self.assertEqual(P.command("version", "R", "m", "py", "", True), "py -m dvc commit --force --quiet && py -m dvc push")


@unittest.skipUnless(importlib.util.find_spec("airflow"), "Airflow isn't installed here: run it in the Docker image")
class Dag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from airflow.models import DagBag
        bag = DagBag(dag_folder=str(HERE / "dags"), include_examples=False)
        cls.errors = bag.import_errors
        cls.dag = bag.get_dag(load_settings("orchestration")["dag_id"])

    def test_loads_without_errors(self):
        self.assertEqual(self.errors, {})
        self.assertIsNotNone(self.dag)

    def test_tasks_and_edges_follow_the_spec(self):
        self.assertEqual(set(self.dag.task_ids), set(P.STEPS))
        for part, (upstream, _, _) in P.STEPS.items():
            self.assertEqual(self.dag.get_task(part).upstream_task_ids, set(upstream), part)

    def test_settings_reach_the_tasks(self):
        orch = load_settings("orchestration")
        for task in self.dag.tasks:
            self.assertEqual(task.retries, orch["retries"])
            self.assertEqual(task.execution_timeout.total_seconds(), orch["timeout_minutes"][task.task_id] * 60)
            self.assertIsNotNone(task.on_failure_callback)


if __name__ == "__main__":
    unittest.main()
