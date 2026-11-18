"""deploy.toml (contract D4): it parses, its names fit Google Cloud's rules, its thresholds are the plan's, and the
Terraform takes every name from it instead of typing it.

    python -m unittest discover -s Model-Deployment/tests -v
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
import deploy_config  # noqa: E402

TERRAFORM = DEPLOY.parent / "infra" / "terraform"
LABEL_LINE = re.compile(r'^\s*(resource|data|variable|output|provider|module|dynamic)\s+"[^"]*"(\s+"[^"]*")?\s*\{\s*$')


def run_names(cfg):
    return [cfg["services"]["risk"]["name"], cfg["services"]["views"]["name"], cfg["jobs"]["refresh"]["name"]]


class Names(unittest.TestCase):
    def setUp(self):
        self.cfg = deploy_config.load()

    def test_project_is_filled_in(self):
        for name in self.cfg["buckets"].values():
            self.assertNotIn("{project}", name)
            self.assertTrue(name.startswith(self.cfg["gcp"]["project"] + "-"))

    def test_another_project(self):
        cfg = deploy_config.load(project="fresh-project-123")
        self.assertEqual(cfg["gcp"]["project"], "fresh-project-123")
        self.assertEqual(cfg["buckets"]["monitor"], "fresh-project-123-meridian-monitor")
        self.assertEqual(deploy_config.load()["gcp"]["project"], self.cfg["gcp"]["project"])

    def test_bucket_names_fit_for_any_project(self):
        # 3 to 63 characters, lowercase letters, digits and dashes, a letter or digit at each end, never "goog".
        # Project ids run 6 to 30 characters, so the longest one must still fit
        for project in (self.cfg["gcp"]["project"], "p" * 30):
            for name in deploy_config.load(project=project)["buckets"].values():
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

    def test_registry_and_secret_names(self):
        for name in (self.cfg["registry"]["models"], self.cfg["registry"]["images"]):
            self.assertRegex(name, r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")
        for name in self.cfg["secrets"].values():
            self.assertRegex(name, r"^[A-Za-z0-9_-]{1,255}$")

    def test_schedule(self):
        self.assertEqual(len(self.cfg["schedule"]["cron"].split()), 5)
        self.assertIsInstance(self.cfg["schedule"]["paused"], bool)

    def test_the_plans_numbers(self):
        # PLAN §4 (the service), §5 (retrain thresholds) and §6 (pass bars), as signed off. Changing one is a PR
        # that changes the plan too
        risk, drift, decay, alerts, budget = (self.cfg[k] for k in ("services", "drift", "decay",
                                                                     "service_alerts", "budget"))
        self.assertEqual((risk["risk"]["min_instances"], risk["risk"]["max_instances"], risk["risk"]["public"]),
                         (0, 2, True))
        self.assertEqual((drift["psi"], drift["dataset_share"], drift["window_days"]), (0.2, 0.5, 30))
        self.assertEqual(drift["top_features"], ["la", "nf", "prior_bug_fixes", "nd", "ld"])
        self.assertEqual((decay["min_positives"], decay["max_drop"], decay["label_window_days"]), (30, 0.05, 365))
        self.assertEqual((alerts["error_rate"], alerts["error_window_min"], alerts["p95_latency_s"],
                          alerts["cold_start_s"]), (0.01, 10, 2, 15))
        self.assertEqual((budget["amount_usd"], budget["alert_at"], budget["phase_target_usd"]),
                         (25, [0.5, 0.9, 1.0], 5))


class Cli(unittest.TestCase):
    def cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(DEPLOY / "deploy_config.py"), *args], input=stdin,
                              capture_output=True, text=True)

    def test_get_a_string(self):
        r = self.cli("--get", "services.risk.name")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "meridian-risk"))

    def test_get_a_list_prints_json(self):
        r = self.cli("--get", "drift.top_features")
        self.assertEqual(json.loads(r.stdout), ["la", "nf", "prior_bug_fixes", "nd", "ld"])

    def test_get_with_another_project(self):
        r = self.cli("--get", "buckets.block", "--project", "fresh-project-123")
        self.assertEqual(r.stdout.strip(), "fresh-project-123-meridian-block")

    def test_a_missing_key_fails_clearly(self):
        r = self.cli("--get", "services.nope.name")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("services.nope.name", r.stderr)

    def test_terraform_protocol(self):
        # Terraform's external data source sends its query on stdin and wants a map of strings back
        for query, project in (({"project": ""}, deploy_config.load()["gcp"]["project"]),
                               ({"project": "fresh-project-123"}, "fresh-project-123")):
            r = self.cli("--terraform", stdin=json.dumps(query))
            out = json.loads(r.stdout)
            self.assertEqual(list(out), ["json"])
            self.assertIsInstance(out["json"], str)
            cfg = json.loads(out["json"])
            self.assertEqual(cfg["gcp"]["project"], project)
            self.assertEqual(cfg["buckets"]["cache"], f"{project}-meridian-cache")


class TerraformReadsDeployToml(unittest.TestCase):
    def test_no_name_is_typed_into_a_tf_file(self):
        # Comments may mention a name, and block labels (resource "…" "github") are Terraform's own names; every
        # value must come from deploy.toml (local.cfg)
        cfg = deploy_config.load()
        names = {*run_names(cfg), *cfg["service_accounts"].values(), cfg["registry"]["models"],
                 cfg["registry"]["images"], *cfg["secrets"].values(), *cfg["buckets"].values(),
                 cfg["workload_identity"]["pool"], cfg["workload_identity"]["provider"], cfg["gcp"]["project"],
                 cfg["gcp"]["region"], cfg["github"]["repository"]}
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
