"""deploy.toml (contract D4): it parses, its names fit Google Cloud's rules, its thresholds are the plan's, phase 2's
copies of the same names agree with it, and the Terraform takes every name from it instead of typing it.

    python -m unittest discover -s Model-Deployment/tests -v
"""
import json
import os
import re
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))
import deploy_config  # noqa: E402

TERRAFORM = REPO / "infra" / "terraform"
LABEL_LINE = re.compile(r'^\s*(resource|data|variable|output|provider|module|dynamic)\s+"[^"]*"(\s+"[^"]*")?\s*\{\s*$')


def run_names(cfg):
    return [cfg["services"]["risk"]["name"], cfg["services"]["views"]["name"], cfg["jobs"]["refresh"]["name"]]


class Names(unittest.TestCase):
    def setUp(self):
        self.cfg = deploy_config.load(use_env=False)

    def test_project_is_filled_in(self):
        for name in self.cfg["buckets"].values():
            self.assertNotIn("{project}", name)
            self.assertTrue(name.startswith(self.cfg["gcp"]["project"] + "-"))

    def test_another_project(self):
        cfg = deploy_config.load(project="fresh-project-123", use_env=False)
        self.assertEqual(cfg["gcp"]["project"], "fresh-project-123")
        self.assertEqual(cfg["gcp"]["team_project"], self.cfg["gcp"]["project"])
        self.assertEqual(cfg["buckets"]["monitor"], "fresh-project-123-meridian-monitor")
        self.assertIsNone(cfg["gcp"]["project_number"])  # deploy.toml knows only its own project's number

    def test_bucket_names_fit_for_any_project(self):
        # 3 to 63 characters, lowercase letters, digits and dashes, a letter or digit at each end, never "goog".
        # Project ids run 6 to 30 characters, so the longest one must still fit
        for project in (self.cfg["gcp"]["project"], "p" * 30):
            for name in deploy_config.load(project=project, use_env=False)["buckets"].values():
                self.assertRegex(name, r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
                self.assertNotIn("goog", name)

    def test_service_account_ids(self):
        for account in self.cfg["service_accounts"].values():
            self.assertRegex(account, r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")  # 6 to 30 characters
        self.assertEqual(len(set(self.cfg["service_accounts"].values())), len(self.cfg["service_accounts"]))

    def test_cloud_run_and_scheduler_names(self):
        for name in run_names(self.cfg):
            self.assertRegex(name, r"^[a-z]([-a-z0-9]{0,47}[a-z0-9])?$")  # up to 49 characters
        self.assertLessEqual(len(self.cfg["jobs"]["refresh"]["name"] + "-daily"), 500)

    def test_registry_image_and_secret_names(self):
        for name in (self.cfg["registry"]["models"], self.cfg["registry"]["images"]):
            self.assertRegex(name, r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")
        for name in self.cfg["images"].values():
            self.assertRegex(name, r"^[a-z0-9]+([._-][a-z0-9]+)*$")
        for name in self.cfg["secrets"].values():
            self.assertRegex(name, r"^[A-Za-z0-9_-]{1,255}$")

    def test_project_number(self):
        self.assertRegex(self.cfg["gcp"]["project_number"], r"^[0-9]{6,20}$")

    def test_schedule(self):
        self.assertEqual(len(self.cfg["schedule"]["cron"].split()), 5)
        self.assertIsInstance(self.cfg["schedule"]["paused"], bool)

    def test_the_plans_numbers(self):
        # PLAN §4 (the service), §5 (retrain thresholds) and §6 (pass bars), as signed off. Changing one is a PR
        # that changes the plan too
        keys = ("services", "drift", "decay", "service_alerts", "budget")
        risk, drift, decay, alerts, budget = (self.cfg[k] for k in keys)
        self.assertEqual((risk["risk"]["min_instances"], risk["risk"]["max_instances"], risk["risk"]["public"]),
                         (0, 2, True))
        self.assertGreater(risk["risk"]["max_request_kb"], 0)
        self.assertEqual((drift["psi"], drift["dataset_share"], drift["window_days"]), (0.2, 0.5, 30))
        self.assertEqual(drift["top_features"], ["la", "nf", "prior_bug_fixes", "nd", "ld"])
        self.assertEqual((decay["min_positives"], decay["min_paired_lower"], decay["max_drop"],
                          decay["label_window_days"]), (30, 0.0, 0.05, 365))
        self.assertEqual((alerts["error_rate"], alerts["error_window_min"], alerts["p95_latency_s"],
                          alerts["cold_start_s"]), (0.01, 10, 2, 15))
        self.assertEqual((budget["amount_usd"], budget["alert_at"], budget["phase_target_usd"]),
                         (25, [0.5, 0.9, 1.0], 5))

    def test_the_budget_covers_the_phase(self):
        # Work starts Wed Nov 18, and the phase is due Mon Dec 7 (PLAN §11)
        start, end = (date.fromisoformat(self.cfg["budget"][k]) for k in ("start", "end"))
        self.assertLessEqual(start, date(2026, 11, 18))
        self.assertGreaterEqual(end, date(2026, 12, 7))


class Derived(unittest.TestCase):
    def test_names_built_from_the_rest(self):
        cfg = deploy_config.load(use_env=False)
        d, project, region = cfg["derived"], cfg["gcp"]["project"], cfg["gcp"]["region"]
        self.assertEqual(d["service_accounts"]["deploy"], f"meridian-deploy@{project}.iam.gserviceaccount.com")
        self.assertEqual(d["images"]["risk"], f"{region}-docker.pkg.dev/{project}/meridian-images/risk")
        self.assertEqual(d["models_repo"], f"{region}-generic.pkg.dev/{project}/meridian-models")
        self.assertEqual(d["workload_identity_provider"],
                         f"projects/{cfg['gcp']['project_number']}/locations/global/workloadIdentityPools/github"
                         f"/providers/meridian")

    def test_another_project_needs_its_number(self):
        cfg = deploy_config.load(project="fresh-project-123", use_env=False)
        self.assertIsNone(cfg["derived"]["workload_identity_provider"])
        with self.assertRaises(KeyError):
            deploy_config.get(cfg, "derived.workload_identity_provider")
        cfg = deploy_config.load(project="fresh-project-123", project_number="123456789", use_env=False)
        self.assertTrue(cfg["derived"]["workload_identity_provider"].startswith("projects/123456789/"))


class InsideCloudRun(unittest.TestCase):
    def test_the_containers_project_comes_from_its_env(self):
        with mock.patch.dict(os.environ, {"MERIDIAN_DEPLOY_PROJECT": "fresh-project-123"}):
            self.assertEqual(deploy_config.load()["buckets"]["block"], "fresh-project-123-meridian-block")
            team = deploy_config.load(use_env=False)["gcp"]["project"]
            self.assertEqual(deploy_config.load(use_env=False)["gcp"]["project"], team)

    def test_terraform_never_reads_the_env(self):
        env = {**os.environ, "MERIDIAN_DEPLOY_PROJECT": "fresh-project-123"}
        r = subprocess.run([sys.executable, str(DEPLOY / "deploy_config.py"), "--terraform"],
                           input=json.dumps({"project": ""}), capture_output=True, text=True, env=env)
        cfg = json.loads(json.loads(r.stdout)["json"])
        self.assertEqual(cfg["gcp"]["project"], cfg["gcp"]["team_project"])


class Phase2AgreesWithIt(unittest.TestCase):
    """Phase 2's registry settings, model.yml's sign-in and its Slack secret predate deploy.toml. They must agree."""

    def setUp(self):
        self.cfg = deploy_config.load(use_env=False)

    def test_model_toml_registry(self):
        with open(REPO / "Model-Pipeline" / "model.toml", "rb") as f:
            reg = deploy_config.tomllib.load(f)["registry"]
        self.assertEqual((reg["project"], reg["location"], reg["repository"], reg["package"], reg["pointer"]),
                         (self.cfg["gcp"]["project"], self.cfg["gcp"]["region"], self.cfg["registry"]["models"],
                          self.cfg["registry"]["model_package"], self.cfg["registry"]["pointer_package"]))

    def test_model_yml_sign_in(self):
        text = (REPO / ".github" / "workflows" / "model.yml").read_text()
        provider = re.search(r"workload_identity_provider:\s*(\S+)", text).group(1)
        account = re.search(r"service_account:\s*(\S+)", text).group(1)
        self.assertEqual(provider, self.cfg["derived"]["workload_identity_provider"])
        self.assertEqual(account, self.cfg["derived"]["service_accounts"]["ci"])

    def test_the_slack_secret_has_d4s_name(self):
        # D4: MERIDIAN_SLACK_WEBHOOK in GitHub and in Secret Manager; model.yml reads the GitHub one
        name = self.cfg["secrets"]["slack_webhook"]
        self.assertEqual(name, "MERIDIAN_SLACK_WEBHOOK")
        self.assertIn("secrets." + name, (REPO / ".github" / "workflows" / "model.yml").read_text())


class Cli(unittest.TestCase):
    def cli(self, *args, stdin=""):
        env = {k: v for k, v in os.environ.items() if k != "MERIDIAN_DEPLOY_PROJECT"}
        return subprocess.run([sys.executable, str(DEPLOY / "deploy_config.py"), *args], input=stdin,
                              capture_output=True, text=True, env=env)

    def test_get_a_string(self):
        r = self.cli("--get", "services.risk.name")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "meridian-risk"))

    def test_get_a_list_prints_json(self):
        r = self.cli("--get", "drift.top_features")
        self.assertEqual(json.loads(r.stdout), ["la", "nf", "prior_bug_fixes", "nd", "ld"])

    def test_get_a_derived_name(self):
        r = self.cli("--get", "derived.service_accounts.deploy")
        self.assertTrue(r.stdout.strip().startswith("meridian-deploy@"))

    def test_get_with_another_project(self):
        r = self.cli("--get", "buckets.block", "--project", "fresh-project-123")
        self.assertEqual(r.stdout.strip(), "fresh-project-123-meridian-block")

    def test_a_missing_key_fails_clearly(self):
        r = self.cli("--get", "services.nope.name")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("services.nope.name", r.stderr)

    def test_terraform_protocol(self):
        # Terraform's external data source sends its query on stdin and wants a map of strings back
        team = deploy_config.load(use_env=False)["gcp"]["project"]
        for query, project in (({"project": ""}, team), ({"project": "fresh-project-123"}, "fresh-project-123")):
            r = self.cli("--terraform", stdin=json.dumps(query))
            out = json.loads(r.stdout)
            self.assertEqual(list(out), ["json"])
            self.assertIsInstance(out["json"], str)
            cfg = json.loads(out["json"])
            self.assertEqual((cfg["gcp"]["project"], cfg["gcp"]["team_project"]), (project, team))
            self.assertEqual(cfg["buckets"]["cache"], f"{project}-meridian-cache")


class TerraformReadsDeployToml(unittest.TestCase):
    def test_no_name_is_typed_into_a_tf_file(self):
        # Comments may mention a name, and block labels (resource "…" "github") are Terraform's own names; every
        # value must come from deploy.toml (local.cfg)
        cfg = deploy_config.load(use_env=False)
        names = {*run_names(cfg), *cfg["service_accounts"].values(), cfg["registry"]["models"],
                 cfg["registry"]["images"], *cfg["secrets"].values(), *cfg["buckets"].values(),
                 cfg["workload_identity"]["pool"], cfg["workload_identity"]["provider"], cfg["gcp"]["project"],
                 cfg["gcp"]["project_number"], cfg["gcp"]["region"], cfg["github"]["repository"],
                 cfg["budget"]["start"], cfg["budget"]["end"]}
        files = sorted(TERRAFORM.glob("*.tf"))
        self.assertTrue(files, "no .tf files found")
        for tf in files:
            if tf.name.endswith("_override.tf"):
                continue
            code = "\n".join(line.split("#", 1)[0] for line in tf.read_text().splitlines()
                             if not LABEL_LINE.match(line))
            for name in names:
                self.assertNotIn(f'"{name}"', code, f"{tf.name} types {name!r}: read it from deploy.toml")


if __name__ == "__main__":
    unittest.main()
