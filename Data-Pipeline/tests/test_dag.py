"""DAG tests. The spec (dags/pipeline_spec.py) runs anywhere: every table a part reads is written upstream of it,
publish only follows validate, and every task is one command. The Airflow DAGs themselves are checked where Airflow is
installed (the Docker image):

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


def ancestors(task, dag=None):
    up = dag or {p: u for p, (u, _, _) in P.STEPS.items()}
    seen, todo = set(), list(up[task])
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo += up[p]
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

    def test_each_dag_runs_in_order_and_its_tasks_are_modules(self):
        for name, dag in P.DAGS.items():
            order = list(dag)
            for task, upstream in dag.items():
                self.assertTrue(all(order.index(u) < order.index(task) for u in upstream), f"{name}.{task}")
                if task in P.STEPS:
                    self.assertTrue(set(P.STEPS[task][0]) <= ancestors(task, dag), f"{name}.{task}")
        for m in set(P.PARTS) | {"status", "compare", "publish"}:   # meridian.report is being written
            self.assertTrue((HERE / "meridian" / f"{m}.py").exists(), m)

    def test_publish_only_after_validate_and_bias_only_weekly(self):
        for name, dag in P.DAGS.items():
            self.assertTrue({"validate", next(iter(dag))} <= ancestors("publish", dag), name)   # behind the first task
        self.assertEqual(list(P.DAGS["update"])[:2], ["anything_new", "collect"])
        self.assertNotIn("bias", P.DAGS["update"])
        self.assertTrue({"bias", "compare"} <= ancestors("publish", P.DAGS["rebuild"]))
        self.assertTrue(all(any(g in dag for dag in P.DAGS.values()) for g in P.GATES))

    def test_commands(self):
        c = lambda dag, task, prev_arg="", prev="": P.command(dag, task, "R", "meridian.toml", "py", prev_arg, prev,
                                                              "/d", "/d/rb")
        self.assertEqual(c("update", "join", "--prev-run Q"), "py -m meridian.join --run-id R --config meridian.toml --prev-run Q")
        self.assertEqual(c("update", "graph", "--prev-run Q"), "py -m meridian.graph --run-id R --config meridian.toml")
        self.assertEqual(c("update", "anything_new"), "py -m meridian.status --config meridian.toml")
        self.assertEqual(c("update", "report", "--prev-run Q"),
                         "py -m meridian.report --run-id R --config meridian.toml --prev-run Q")
        self.assertEqual(c("update", "publish"), "py -m meridian.publish --run-id R --config meridian.toml")
        # the rebuild computes under its own root, compares with the main one, and publishes where the views read
        self.assertEqual(c("rebuild", "scan"), "MERIDIAN_DATA=/d/rb MERIDIAN_NO_CACHE=1 py -m meridian.scan --run-id R --config meridian.toml")
        self.assertEqual(c("rebuild", "compare"),
                         "MERIDIAN_DATA=/d/rb MERIDIAN_NO_CACHE=1 py -m meridian.compare --run-id R --config meridian.toml --against-root /d")
        self.assertEqual(c("rebuild", "publish", "--prev-run Q"),
                         "py -m meridian.publish --run-id R --config meridian.toml --prev-run Q --from-root /d/rb")
        fresh = c("rebuild", "fresh", prev="Q")
        self.assertTrue(fresh.startswith("rm -rf /d/rb/out/R && mkdir -p /d/rb/out/R && ln -sfn /d/snapshots /d/rb/snapshots"))
        self.assertIn('p="Q" && if [ -n "$p" ]; then ln -sfn /d/out/$p /d/rb/out/$p; fi', fresh)


@unittest.skipUnless(importlib.util.find_spec("airflow"), "Airflow isn't installed here: run it in the Docker image")
class Dag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from airflow.models import DagBag
        bag = DagBag(dag_folder=str(HERE / "dags"), include_examples=False)
        cls.errors = bag.import_errors
        cls.dags = {name: bag.get_dag(s["dag_id"]) for name, s in load_settings("dags").items()}

    def test_loads_without_errors(self):
        self.assertEqual(self.errors, {})
        self.assertEqual(set(self.dags), set(P.DAGS))
        self.assertTrue(all(self.dags.values()))

    def test_tasks_and_edges_follow_the_spec(self):
        for name, dag in self.dags.items():
            self.assertEqual(set(dag.task_ids), set(P.DAGS[name]))
            self.assertEqual(dag.max_active_runs, 1)
            for task, upstream in P.DAGS[name].items():
                self.assertEqual(dag.get_task(task).upstream_task_ids, set(upstream), f"{name}.{task}")

    def test_settings_reach_the_tasks(self):
        orch = load_settings("orchestration")
        for dag in self.dags.values():
            for task in dag.tasks:
                self.assertEqual(task.retries, orch["retries"])
                self.assertEqual(task.execution_timeout.total_seconds(), orch["timeout_minutes"][task.task_id] * 60)
                self.assertIsNotNone(task.on_failure_callback)


if __name__ == "__main__":
    unittest.main()
