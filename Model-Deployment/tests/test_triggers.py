"""deploy.yml's triggers (Nov 25): a PR, by hand, model.yml green on main, and D3's "champion-updated" dispatch.

    python -m unittest discover -s Model-Deployment/tests -p "test_triggers.py" -v
"""
import unittest
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:   # deploy.yml's environment has it (mlflow needs it)
    yaml = None

REPO = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO / ".github" / "workflows" / "deploy.yml"
MODEL_YML = REPO / ".github" / "workflows" / "model.yml"
REFRESH_JOB = REPO / "Model-Deployment" / "refresh" / "job.py"


@unittest.skipUnless(yaml, "needs PyYAML")
class Triggers(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
        self.on = self.doc.get("on", self.doc.get(True))   # YAML 1.1 reads a bare `on` as true
        self.job = self.doc["jobs"]["deploy"]
        self.steps = {s.get("id") or s.get("name"): s for s in self.job["steps"]}

    def test_the_four_triggers(self):
        self.assertEqual(set(self.on), {"pull_request", "workflow_dispatch", "workflow_run", "repository_dispatch"})

    def test_model_yml_on_main_by_its_name(self):
        run = self.on["workflow_run"]
        model_name = yaml.safe_load(MODEL_YML.read_text(encoding="utf-8"))["name"]
        self.assertEqual(run["workflows"], [model_name])
        self.assertEqual(run["types"], ["completed"])
        self.assertEqual(run["branches"], ["main"])

    def test_only_a_green_model_run_on_main_and_never_a_prs_deploys(self):
        cond = self.job["if"]
        for want in ("workflow_run.conclusion == 'success'", "workflow_run.head_branch == 'main'",
                     "workflow_run.event != 'pull_request'", "github.event_name != 'workflow_run'"):
            self.assertIn(want, cond)

    def test_the_dispatch_is_the_refresh_jobs_event(self):
        text = REFRESH_JOB.read_text(encoding="utf-8")
        event = self.on["repository_dispatch"]["types"]
        self.assertEqual(event, ["champion-updated"])
        self.assertIn('D3_EVENT = "champion-updated"', text)

    def test_a_dispatch_must_name_the_pointers_champion(self):
        step = self.steps["dispatch"]
        self.assertEqual(step["if"], "github.event_name == 'repository_dispatch'")
        self.assertIn("client_payload.version", step["env"]["SENT_VERSION"])
        self.assertIn("client_payload.md5", step["env"]["SENT_MD5"])
        self.assertNotIn("client_payload", step["run"])   # the payload reaches the shell as env, never as code
        self.assertIn("exit 1", step["run"])
        order = [s.get("id") for s in self.job["steps"]]
        self.assertLess(order.index("champion"), order.index("dispatch"))
        self.assertLess(order.index("dispatch"), order.index("build"))

    def test_an_automatic_run_skips_a_champion_that_already_serves(self):
        step = self.steps["current"]
        self.assertIn("env.AUTO == 'true'", step["if"])
        self.assertIn("ALREADY_SERVING=true", step["run"])
        self.assertIn("workflow_run", self.job["env"]["AUTO"])
        self.assertIn("repository_dispatch", self.job["env"]["AUTO"])
        self.assertNotIn("workflow_dispatch", self.job["env"]["AUTO"])   # by hand always deploys
        for sid in ("history", "build", "smoke_local", "push", "revision", "smoke", "traffic"):
            self.assertIn("env.ALREADY_SERVING != 'true'", self.steps[sid]["if"], sid)

    def test_new_events_run_on_main_so_workload_identity_still_signs_in(self):
        # wif.tf trusts ref == refs/heads/main; workflow_run and repository_dispatch run on the default branch
        self.assertIn("github.event_name != 'pull_request'", self.job["env"]["ON_MAIN"])
        self.assertIn("github.ref == 'refs/heads/main'", self.job["env"]["ON_MAIN"])
        wif = (REPO / "infra" / "terraform" / "wif.tf").read_text(encoding="utf-8")
        self.assertIn("assertion.ref=='refs/heads/", wif)


if __name__ == "__main__":
    unittest.main()
