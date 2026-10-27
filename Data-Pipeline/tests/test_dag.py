"""DAG tests. The spec (dags/pipeline_spec.py) runs anywhere: every table a part reads is written upstream of it,
publish only follows validate, and every task is one command. The Airflow DAGs themselves are checked where Airflow is
installed (the Docker image):

    python -m unittest tests.test_dag -v                                              # from Data-Pipeline/
    docker compose run --rm --no-deps -e CONNECTION_CHECK_MAX_COUNT=0 -w /opt/meridian/Data-Pipeline \
        airflow-scheduler python -m unittest tests.test_dag -v                        # with Airflow, no database
"""
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

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
        self.assertTrue({"report", "compare"} <= ancestors("publish", P.DAGS["rebuild"]))
        self.assertNotIn("bias", ancestors("publish", P.DAGS["rebuild"]))   # a bias crash never holds the publish
        self.assertEqual(P.DAGS["rebuild"]["compare"], ("report",))   # a bias crash never skips the weekly check
        self.assertIn("report", ancestors("compare", P.DAGS["rebuild"]))   # the rebuild's report checks the update's
        self.assertTrue(all(any(g in dag for dag in P.DAGS.values()) for g in P.GATES))

    def test_gx_is_weekly_beside_bias_and_never_gates(self):
        rebuild = P.DAGS["rebuild"]
        self.assertEqual(rebuild["gx"], ("validate",))
        self.assertNotIn("gx", P.DAGS["update"])
        self.assertTrue(all("gx" not in ancestors(t, rebuild) for t in rebuild))   # nothing waits for it
        cmd = P.command("rebuild", "gx", "R", "meridian.toml", "py", "", "", "/d", "/d/rb", gx_python="gxpy")
        self.assertEqual(cmd,
                         "MERIDIAN_DATA=/d/rb MERIDIAN_NO_CACHE=1 gxpy -m meridian.gx --run-id R "
                         "--config meridian.toml --publish-root /d")  # its own environment; Data Docs where views read

    def test_fresh_refuses_the_main_data_root(self):   # its rm -rf would delete the live run's out/
        for rebuild in ("/d", "/d/", "", ".", "/d/.", "/d/x/.."):
            with self.assertRaisesRegex(ValueError, "folder of its own"):
                P.command("rebuild", "fresh", "R", "meridian.toml", "py", "", "Q", "/d", rebuild)

    def test_only_a_run_id_reaches_a_command(self):   # the trigger's config goes into rm -rf
        self.assertEqual([P.checked_run(v) for v in ("70dd1dabfed544b8", "", None)], ["70dd1dabfed544b8", "", ""])
        for bad in ("../..", "x;rm -rf /", "70dd1dabfed544b8 ", "70DD1DABFED544B8", "new", 7):
            with self.assertRaisesRegex(ValueError, "not a run_id"):
                P.checked_run(bad)

    def test_commands(self):
        c = lambda dag, task, prev_arg="", prev="": P.command(dag, task, "R", "meridian.toml", "py", prev_arg, prev,
                                                              "/d", "/d/rb")
        self.assertEqual(c("update", "join", "--prev-run Q"),
                         "py -m meridian.join --run-id R --config meridian.toml --prev-run Q")
        self.assertEqual(c("update", "graph", "--prev-run Q"), "py -m meridian.graph --run-id R --config meridian.toml")
        self.assertEqual(c("update", "anything_new"), "py -m meridian.status --config meridian.toml")
        self.assertEqual(c("update", "report", "--prev-run Q"),
                         "py -m meridian.report --run-id R --config meridian.toml --prev-run Q")
        self.assertEqual(c("update", "publish"), "py -m meridian.publish --run-id R --config meridian.toml")
        # the rebuild computes under its own root, compares with the main one, and publishes where the views read
        self.assertEqual(c("rebuild", "scan"),
                         "MERIDIAN_DATA=/d/rb MERIDIAN_NO_CACHE=1 py -m meridian.scan --run-id R "
                         "--config meridian.toml")
        self.assertEqual(c("rebuild", "compare"),
                         "MERIDIAN_DATA=/d/rb MERIDIAN_NO_CACHE=1 py -m meridian.compare --run-id R "
                         "--config meridian.toml --against-root /d")
        self.assertEqual(c("rebuild", "publish", "--prev-run Q"),
                         "py -m meridian.publish --run-id R --config meridian.toml --prev-run Q --from-root /d/rb")
        fresh = c("rebuild", "fresh", prev="Q")
        self.assertTrue(fresh.startswith("rm -rf /d/rb/out && mkdir -p /d/rb/out/R "   # every past week's out/ too
                                         "&& ln -sfn /d/snapshots /d/rb/snapshots"))
        self.assertIn('p="Q" && if [ -n "$p" ]; then ln -sfn /d/out/$p /d/rb/out/$p; fi', fresh)
        self.assertTrue(fresh.endswith('echo "run_id=R prev_run=$p"'))   # the run, named once for the later tasks

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_fresh_replaces_last_weeks_real_prev_dir(self):   # ln -sfn onto a real dir would link inside it
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "out" / "Q").mkdir(parents=True)
        (d / "snapshots").mkdir()
        (d / "out" / "Q" / "call_site.jsonl").write_text("main")
        (d / "rb" / "out" / "Q").mkdir(parents=True)   # last week's rebuild of Q, a real dir
        (d / "rb" / "out" / "O").mkdir()               # an older week's: ~0.5 GB each, never read again
        fresh = P.command("rebuild", "fresh", "R", "m.toml", "py", "", "Q", d.as_posix(), (d / "rb").as_posix())
        for _ in range(2):   # the second time out/Q is last time's link: rm -rf removes the link, not main's files
            p = subprocess.run([shutil.which("bash"), "-c", fresh], capture_output=True, text=True)
            self.assertEqual((p.returncode, p.stdout.split()[-2:]), (0, ["run_id=R", "prev_run=Q"]), p.stderr)
            self.assertEqual(sorted(x.name for x in (d / "rb" / "out" / "Q").iterdir()), ["call_site.jsonl"])
            self.assertEqual(sorted(x.name for x in (d / "rb" / "out").iterdir()), ["Q", "R"])
        self.assertEqual((d / "out" / "Q" / "call_site.jsonl").read_text(), "main")

    def test_a_part_that_alerted_itself_gets_no_second_alert(self):   # exit code 4: it sent its own, then failed
        self.assertTrue(P.alerted("Bash command failed. The command returned a non-zero exit code 4."))   # Airflow 2.10
        self.assertTrue(P.alerted(RuntimeError("exit code 4: py -m meridian.compare --run-id R")))         # gate()
        for other in ("Bash command failed. The command returned a non-zero exit code 1.", "exit code 42: x",
                      "Timeout, PID: 4", "", None):
            self.assertFalse(P.alerted(other), other)


@unittest.skipUnless(importlib.util.find_spec("airflow"), "Airflow isn't installed here: run it in the Docker image")
class Dag(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from airflow.models import DagBag
        bag = DagBag(dag_folder=str(HERE / "dags"), include_examples=False)
        cls.errors = bag.import_errors
        # get_dag() asks the database
        cls.dags = {name: bag.dags.get(s["dag_id"]) for name, s in load_settings("dags").items()}

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

    def test_a_trigger_naming_a_run_skips_anything_new(self):   # a rerun after a code fix, from the UI or the API
        import meridian_dag as M
        self.assertIn("dag_run.conf", self.dags["update"].get_task("anything_new").op_kwargs["force"])
        # the weekly check always runs
        self.assertEqual(self.dags["rebuild"].get_task("compare").op_kwargs["force"], "")
        with redirect_stdout(io.StringIO()):
            self.assertTrue(M.gate("exit 1", 0, 99, force="R1"))     # forced: the command isn't even run
            self.assertFalse(M.gate("exit 99", 0, 99))                # not forced: nothing new stops the DAG
            self.assertTrue(M.gate("exit 0", 0, 99))

    def test_templates_name_one_run_and_never_it_as_its_prev(self):
        import meridian.publish as Pub
        import meridian_dag as M
        from types import SimpleNamespace as NS

        def render(dag, task, latest, conf=None, xcom=None):
            t = self.dags[dag].get_task(task)
            ctx = {"dag_run": NS(conf=conf or {}), "ti": NS(xcom_pull=lambda task_ids: xcom),
                   "params": self.dags[dag].params}
            with mock.patch.object(Pub, "latest", return_value=latest):   # the published() macro's latest.json
                return t.render_template(t.op_kwargs if task in P.GATES else t.bash_command, ctx)
        R, Q, S = (c * 16 for c in "abc")
        pub = {"run_id": R, "prev_run": Q}
        self.assertTrue(render("update", "join", pub,
                               xcom=f"run_id={S}").endswith(f"--run-id {S} --config meridian.toml --prev-run {R}"))
        # a forced rerun of the published run keeps its prev_run, not none
        self.assertTrue(render("update", "join", pub,
                               {"run_id": R}).endswith(f"--run-id {R} --config meridian.toml --prev-run {Q}"))
        self.assertEqual(render("update", "anything_new", pub, {"force": True})["force"], "force")
        # the Trigger form sends the params as conf: left as they are, it's the usual run, not a forced 70dd
        form = self.dags["update"].params.dump()
        self.assertEqual(form, {"run_id": "", "prev_run": "", "force": False})
        self.assertEqual(render("update", "anything_new", pub, form)["force"], "")
        self.assertIn("--run-id new ", render("update", "collect", pub, form))
        # the rebuild names its run once, in fresh: an update published meanwhile doesn't switch the later tasks
        self.assertTrue(render("rebuild", "fresh",
                               pub).endswith(f'p="{Q}" && if [ -n "$p" ]; then ln -sfn '
                                             f'{{1}}/out/$p {{0}}/out/$p; fi && echo "run_id={R} prev_run=$p"'
                                             .format(M.REBUILD.as_posix(), M.MAIN.as_posix())))
        # nothing published yet: settings.toml [pipeline] run_id
        self.assertIn(f'echo "run_id={M.PIPELINE["run_id"]} prev_run=$p"', render("rebuild", "fresh", {}))
        later = {"run_id": S, "prev_run": R}
        self.assertIn(f"--run-id {R} --config meridian.toml --prev-run {Q} --from-root",
                      render("rebuild", "publish", later, xcom=f"run_id={R} prev_run={Q}"))
        self.assertIn(f"--run-id {R} --config meridian.toml --against-root",
                      render("rebuild", "compare", later, xcom=f"run_id={R} prev_run={Q}")["cmd"])
        # the trigger's config never reaches a shell unchecked: {"run_id": "../.."} would rm -rf the data root
        named = [("update", "collect"), ("update", "anything_new"), ("update", "join"), ("rebuild", "fresh")]
        for conf, tasks in (({"run_id": "../.."}, named), ({"run_id": "x;rm"}, named),
                            ({"prev_run": "../.."}, named[2:])):   # collect and anything_new never read prev_run
            for dag, task in tasks:
                with self.assertRaisesRegex(ValueError, "not a run_id", msg=f"{dag}.{task} {conf}"):
                    render(dag, task, pub, conf, xcom=f"run_id={S}")

    def test_settings_reach_the_tasks(self):
        orch = load_settings("orchestration")
        for dag in self.dags.values():
            for task in dag.tasks:
                self.assertEqual(task.retries, orch["task_retries"].get(task.task_id, orch["retries"]))
                self.assertEqual(task.execution_timeout.total_seconds(), orch["timeout_minutes"][task.task_id] * 60)
                self.assertIsNotNone(task.on_failure_callback)


if __name__ == "__main__":
    unittest.main()
