"""deploy.toml (contract D4): it parses, its names fit Google Cloud's rules, its thresholds are the plan's, phase 2's
copies of the same names agree with it, and the Terraform takes every name from it instead of typing it.

    python -m unittest discover -s Model-Deployment/tests -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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
        self.assertEqual((drift["psi"], drift["dataset_share"], drift["window_days"]), (0.25, 0.5, 30))
        self.assertEqual((drift["reference"], drift["share_retrains"]), ("latest_year", False))
        self.assertEqual(drift["top_features"], ["la", "nf", "prior_bug_fixes", "nd", "ld"])
        self.assertEqual((decay["window"], decay["min_positives"], decay["min_paired_lower"],
                          decay["label_window_days"]), ("half", 100, 0.0, 365))
        self.assertNotIn("max_drop", decay)
        self.assertEqual((alerts["error_rate"], alerts["error_window_min"], alerts["p95_latency_s"],
                          alerts["cold_start_s"]), (0.01, 10, 2, 15))
        self.assertEqual((budget["amount_usd"], budget["alert_at"], budget["phase_target_usd"]),
                         (25, [0.5, 0.9, 1.0], 5))

    def test_the_budget_covers_the_phase(self):
        # Work starts Wed Nov 18, and the phase is due Mon Dec 7 (PLAN §11)
        start, end = (date.fromisoformat(self.cfg["budget"][k]) for k in ("start", "end"))
        self.assertLessEqual(start, date(2026, 11, 18))
        self.assertGreaterEqual(end, date(2026, 12, 7))

    def test_history_pkl_is_kept_away_from_the_names(self):
        # deploy.yml bakes history.pkl into the image, and the deploy account never reads the block's real names
        # (Aniket, Nov 22). iam.tf grants deploy only history.pkl's folder in this bucket
        self.assertNotEqual(self.cfg["serve_history"]["bucket"], "block")


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
    def cli(self, *args, stdin="", cwd=None):
        env = {k: v for k, v in os.environ.items() if k not in ("MERIDIAN_DEPLOY_PROJECT", "TF_DATA_DIR")}
        return subprocess.run([sys.executable, str(DEPLOY / "deploy_config.py"), *args], input=stdin,
                              capture_output=True, text=True, env=env, cwd=cwd)

    def workdir(self, bucket, kind="gcs"):
        # a directory whose Terraform state is `bucket`, the way `terraform init` records it
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        (d / ".terraform").mkdir()
        backend = {"backend": {"type": kind, "config": {"bucket": bucket, "prefix": "terraform"}}}
        (d / ".terraform" / "terraform.tfstate").write_text(json.dumps(backend))
        return d

    def test_terraform_refuses_another_projects_state(self):
        # a fresh project's run left on the team's state stops before any plan, targeted or not
        team_state = deploy_config.load(use_env=False)["buckets"]["tfstate"]
        r = self.cli("--terraform", stdin=json.dumps({"project": "fresh-project-123"}), cwd=self.workdir(team_state))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gs://fresh-project-123-tfstate", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_terraform_runs_on_its_own_state(self):
        team_state = deploy_config.load(use_env=False)["buckets"]["tfstate"]
        for query, bucket, kind in (({"project": ""}, team_state, "gcs"),
                                    ({"project": "fresh-project-123"}, "fresh-project-123-tfstate", "gcs"),
                                    ({"project": "fresh-project-123"}, team_state, "local")):  # a local dry run
            r = self.cli("--terraform", stdin=json.dumps(query), cwd=self.workdir(bucket, kind))
            self.assertEqual(r.returncode, 0, r.stderr)

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


class ServiceAlerts(unittest.TestCase):
    """S6 (monitoring.tf): every alert threshold comes from [service_alerts], within Google's limits."""

    def setUp(self):
        self.cfg = deploy_config.load(use_env=False)
        self.alerts = self.cfg["service_alerts"]
        self.tf = (TERRAFORM / "monitoring.tf").read_text()

    def test_monitoring_tf_reads_each_threshold(self):
        # The assignments themselves, so a number typed into the .tf file fails here. cold_start_s is a dashboard
        # chart (Wed), not an alert
        for pattern in (r"threshold_value\s*=\s*local\.alerts\.error_rate\n",
                        r"threshold_value\s*=\s*local\.alerts\.p95_latency_s \* 1000\s",
                        r'window\s*=\s*"\$\{local\.alerts\.error_window_min \* 60\}s"',
                        r'period\s*=\s*"\$\{local\.alerts\.uptime_period_s\}s"',
                        r"selected_regions\s*=\s*local\.alerts\.uptime_regions\n"):
            self.assertRegex(self.tf, pattern)
        # the 5xx alert (both halves of its ratio), the p95 alert and the job alert all use that window
        self.assertEqual(len(re.findall(r"alignment_period\s*=\s*local\.window\n", self.tf)), 4)

    def test_the_sparse_alerts_close_so_the_next_failure_pages(self):
        # Google keeps an incident open 7 days once its data stops, unless no data counts as resolved, which it
        # honours only with a duration of at least 60 s
        for name in ("errors", "slow", "job_failed"):
            block = self.tf.split(f'resource "google_monitoring_alert_policy" "{name}"', 1)[1].split("\nresource ")[0]
            self.assertRegex(block, r'evaluation_missing_data\s*=\s*"EVALUATION_MISSING_DATA_INACTIVE"', name)
            self.assertRegex(block, r'duration\s*=\s*"60s"', name)
            self.assertRegex(block, r'auto_close\s*=\s*"1800s"', name)

    def test_the_uptime_check_fits_googles_limits(self):
        # Google: a period of 1, 5, 10 or 15 minutes, a timeout of at most 60 s and within it, at least 3 locations
        period = self.alerts["uptime_period_s"]
        self.assertIn(period, (60, 300, 600, 900))
        self.assertLessEqual(self.cfg["services"]["risk"]["timeout_s"], min(60, period))
        regions = set(self.alerts["uptime_regions"])
        self.assertLessEqual(regions, {"USA", "USA_OREGON", "USA_IOWA", "USA_VIRGINIA", "EUROPE", "SOUTH_AMERICA",
                                       "ASIA_PACIFIC"})
        self.assertGreaterEqual(len(regions), 3)

    def test_every_alert_pages_the_channels(self):
        # each policy pages local.alert_channels, never a list of its own: an empty one would page nobody
        self.assertEqual(len(re.findall(r'^resource "google_monitoring_alert_policy"', self.tf, re.M)), 5)
        self.assertEqual(len(re.findall(r"notification_channels\s*=\s*local\.alert_channels\n", self.tf)), 5)

    def test_the_drift_alert_matches_the_jobs_log_line(self):
        # D5: the refresh job logs event "drift" with D2's retrain flag; the log-based alert matches both fields
        job = (DEPLOY / "refresh" / "job.py").read_text()
        self.assertRegex(job, r'self\.event\(\s*"drift"[^)]*event="drift"[^)]*retrain=')
        self.assertIn('jsonPayload.event = \\"drift\\"', self.tf)
        self.assertIn("jsonPayload.retrain = true", self.tf)


class SloAndCostPanel(unittest.TestCase):
    """S6, part 2 (dashboard.tf): the SLOs are the plan's bars, the cost panel reads deploy.toml's list prices, and the
    log-based metrics match the D5 lines the service and the job write."""

    def setUp(self):
        self.cfg = deploy_config.load(use_env=False)
        self.tf = (TERRAFORM / "dashboard.tf").read_text()

    def test_the_slos_are_the_plans_bars(self):
        slo, alerts = self.cfg["slo"], self.cfg["service_alerts"]
        self.assertAlmostEqual(slo["availability_goal"], 1 - alerts["error_rate"])   # the 5xx alert's 1% as a budget
        self.assertEqual(slo["latency_goal"], 0.95)                                    # the 95 in p95_latency_s
        self.assertTrue(1 <= slo["window_days"] <= 30, "Google's rolling windows are 1 to 30 days")
        for pattern in (r"goal\s*=\s*local\.slo\.availability_goal\n", r"goal\s*=\s*local\.slo\.latency_goal\n",
                        r"max = local\.alerts\.p95_latency_s \* 1000\s"):
            self.assertRegex(self.tf, pattern)
        self.assertEqual(len(re.findall(r"rolling_period_days\s*=\s*local\.slo\.window_days\n", self.tf)), 2)

    def test_the_cost_panel_and_the_lines_read_deploy_toml(self):
        cost = self.cfg["cost"]
        self.assertEqual(set(cost), {"vcpu_second_usd", "gib_second_usd", "million_requests_usd"})
        self.assertTrue(all(0 < v < 1 for v in cost.values()))
        for key in cost:
            self.assertIn(f"local.cost.{key}", self.tf)
        # the service's size comes from [services.risk], and each chart's line from [service_alerts]
        self.assertIn("tonumber(local.risk.cpu) * local.cost.vcpu_second_usd", self.tf)
        for line in ("local.alerts.error_rate, targetAxis", "local.alerts.p95_latency_s * 1000, targetAxis",
                     "local.alerts.cold_start_s * 1000, targetAxis", "local.risk.max_instances, targetAxis"):
            self.assertIn(f"value = {line}", self.tf)

    def test_the_log_metrics_match_the_d5_lines(self):
        app = (DEPLOY / "serve" / "app.py").read_text()
        job = (DEPLOY / "refresh" / "job.py").read_text()
        # the service: a "score" line with its status and model version, the job: "refresh" lines with a step and an
        # outcome, a failed step with outcome "failed", and S5's "drift" line with its retrain verdict
        self.assertRegex(app, r'log\(event="score", status=status, model_version=scorer\.version')
        self.assertRegex(job, r'def event\(self, step, ms, event="refresh"')
        self.assertRegex(job, r'self\.event\("done", [^\n]*outcome=')
        self.assertRegex(job, r'severity="ERROR", outcome="failed"')
        self.assertRegex(job, r'self\.event\(\s*"drift"[^)]*event="drift"[^)]*retrain=')
        for match in ('jsonPayload.event = \\"score\\" AND jsonPayload.status = 200',
                      'jsonPayload.step = \\"done\\" OR jsonPayload.outcome = \\"failed\\"',
                      'jsonPayload.event = \\"drift\\"', "EXTRACT(jsonPayload.model_version)",
                      "EXTRACT(jsonPayload.outcome)", "EXTRACT(jsonPayload.retrain)",
                      "EXTRACT(jsonPayload.latency_ms)"):
            self.assertIn(match, self.tf)

    def test_tiles_leave_out_zero_positions(self):
        # Google drops zero values, so a tile with xPos = 0 in the JSON would show as a change on every plan
        self.assertIn("{ for k, v in { xPos = t.x, yPos = t.y } : k => v if v > 0 }", self.tf)

    def test_the_incident_list_names_policies_the_way_google_wants(self):
        # Google refused the full name (projects/<p>/alertPolicies/<id>) on Nov 24: the widget takes alertPolicies/<id>
        self.assertIn('regex("alertPolicies/[^/]+$", p.name)', self.tf)
        self.assertNotRegex(self.tf, r"\] : p\.name\]")


class TeamGuard(unittest.TestCase):
    """guard.tf: on the team's project, a destroy, a run pointed at another project, or a replacement of CI's sign-in
    stops at the guard before anything goes."""

    def setUp(self):
        self.guard = (TERRAFORM / "guard.tf").read_text()

    def listed(self, name):
        # the entries of `name = [...]`, closed by a line of its own, comments stripped
        m = re.search(rf"^\s*{name}\s*=\s*\[\n(.*?)^\s*\]", self.guard, re.M | re.S)
        self.assertIsNotNone(m, f"guard.tf has no {name}")
        body = "\n".join(line.split("#", 1)[0] for line in m.group(1).splitlines())
        return {entry.strip() for entry in body.split(",") if entry.strip()}

    def test_every_resource_waits_for_the_guard(self):
        # exactly the resources in the .tf files: one left out could be deleted around the guard
        resources = {f"{kind}.{name}" for tf in TERRAFORM.glob("*.tf")
                     for kind, name in re.findall(r'^resource\s+"([^"]+)"\s+"([^"]+)"', tf.read_text(), re.M)}
        resources.discard("terraform_data.team_guard")
        self.assertGreater(len(resources), 20)
        self.assertEqual(self.listed("depends_on"), resources)

    def test_what_cant_be_rebuilt_stops_at_the_guard(self):
        self.assertEqual(self.listed("replace_triggered_by"), {
            "google_iam_workload_identity_pool.github.id", "google_iam_workload_identity_pool_provider.github.id",
            'google_service_account.sa["ci"].id', 'google_service_account_iam_member.github["ci"].id',
            'google_artifact_registry_repository_iam_member.models["ci"].id',
            "google_artifact_registry_repository.models.id", 'google_storage_bucket.data["block"].id',
            'google_storage_bucket.data["monitor"].id', 'google_storage_bucket.data["cache"].id'})
        self.assertRegex(self.guard, r"triggers_replace\s*=\s*local\.project\n")   # a run pointed at another project

    def test_the_guard_refuses_on_the_team_project(self):
        # the part that refuses: a destroy-time command that fails while armed, and no `on_failure` to swallow it
        self.assertRegex(self.guard, r"input\s*=\s*length\(local\.adopt\) > 0 && !var\.allow_team_destroy")
        self.assertRegex(self.guard, r"when\s*=\s*destroy\n")
        self.assertRegex(self.guard, r'command\s*=\s*self\.input \? "[^"]*&& exit 1" : "exit 0"\n')
        self.assertNotIn("on_failure", self.guard)


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


class People(unittest.TestCase):
    """iam.tf: whoever deploys the refresh job by hand (refresh_developers in terraform.tfvars) gets that job and what
    deploying it needs, never the project or the service, and their emails never reach git."""

    def blocks(self, kind):
        # each `resource "<kind>" "<name>" {...}` in the .tf files: {name: body}
        return {name: body for tf in TERRAFORM.glob("*.tf")
                for name, body in re.findall(rf'^resource "{kind}" "([^"]+)" \{{\n(.*?)^\}}', tf.read_text(),
                                             re.M | re.S)}

    def test_project_wide_roles_only_read_logs_or_write_metrics(self):
        # a project-wide run.developer would let the people on the list change meridian-risk too
        roles = sorted(re.search(r'role\s*=\s*"([^"]+)"', body).group(1)
                       for body in self.blocks("google_project_iam_member").values())
        self.assertEqual(roles, ["roles/logging.viewer", "roles/monitoring.metricWriter"])

    def test_people_get_the_job_its_account_the_images_and_the_cache(self):
        on = {"google_cloud_run_v2_job_iam_member": ("roles/run.developer", "google_cloud_run_v2_job.refresh.name"),
              "google_service_account_iam_member": ("roles/iam.serviceAccountUser",
                                                    'google_service_account.sa["refresh"]'),
              "google_artifact_registry_repository_iam_member": ("roles/artifactregistry.writer",
                                                                 "google_artifact_registry_repository.images.name"),
              "google_storage_bucket_iam_member": ("roles/storage.objectViewer", 'google_storage_bucket.data["cache"]'),
              "google_project_iam_member": ("roles/logging.viewer", "local.project"),
              "google_secret_manager_secret_iam_member": ("roles/secretmanager.secretVersionAdder",
                                                          "google_secret_manager_secret.secret[")}
        for kind, (role, resource) in on.items():
            body = self.blocks(kind).get("refresh_developers")
            self.assertIsNotNone(body, f"iam.tf has no {kind} for refresh_developers")
            self.assertRegex(body, rf'role\s*=\s*"{re.escape(role)}"\n', kind)
            self.assertIn(resource, body, kind)
        # those six grants are the only ones the list gets
        code = "\n".join(tf.read_text() for tf in TERRAFORM.glob("*.tf"))
        self.assertEqual(len(re.findall(r"local\.refresh_developers\b", code)), 6)

    def test_emails_stay_out_of_git(self):
        # terraform.tfvars holds them, and git ignores it; the files git keeps name example.com only
        self.assertRegex((TERRAFORM / ".gitignore").read_text(), r"(?m)^terraform\.tfvars$")
        for f in [*TERRAFORM.glob("*.tf"), TERRAFORM / "terraform.tfvars.example"]:
            for email in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", f.read_text()):
                self.assertTrue(email.endswith("@example.com"), f"{f.name} has an email in it: {email}")


class AirflowVm(unittest.TestCase):
    """airflow.tf and deploy.toml [airflow]: a private VM, on in a window around the DAGs, writing only its folder."""

    def setUp(self):
        self.cfg = deploy_config.load(use_env=False)
        self.a = self.cfg["airflow"]
        self.tf = (TERRAFORM / "airflow.tf").read_text()
        self.code = "\n".join(line.split("#", 1)[0] for line in self.tf.splitlines())

    @staticmethod
    def minutes(cron):   # "M H * * *" -> minutes after midnight
        m, h = cron.split()[:2]
        return int(h) * 60 + int(m)

    def dag_schedules(self):
        import tomllib
        with open(REPO / "Data-Pipeline" / "settings.toml", "rb") as f:
            dags = tomllib.load(f)["dags"]
        return {name: d["schedule"] for name, d in dags.items() if d.get("schedule")}

    def test_the_window_holds_every_dag_start(self):
        start, stop = self.minutes(self.a["window_start"]), self.minutes(self.a["window_stop"])
        self.assertEqual(self.a["time_zone"], "Etc/UTC")   # settings.toml's schedules are UTC
        for name, cron in self.dag_schedules().items():
            self.assertTrue(start < self.minutes(cron) < stop, f"[dags.{name}] {cron} is outside the window")

    def test_it_writes_only_its_folder_never_the_jobs_pointer(self):
        prefix = self.a["publish_prefix"]
        self.assertTrue(prefix.endswith("/") and prefix not in ("/", ""))
        self.assertFalse("latest.json".startswith(prefix))
        self.assertIn("publish_prefix", self.code)
        self.assertIn("objects/${local.airflow.publish_prefix}", self.code)

    def test_no_public_ip_and_ssh_from_iap_only(self):
        self.assertNotIn("access_config", self.code)
        self.assertIn('"35.235.240.0/20"', self.code)
        self.assertNotIn("0.0.0.0/0", self.code)
        self.assertIn('ports    = ["22"]', self.code)
        self.assertIn('enable-oslogin = "TRUE"', self.code)

    def test_its_secrets_are_its_own(self):
        own = {self.a["admin_password_secret"], self.a["db_password_secret"]}
        self.assertFalse(own & set(self.cfg["secrets"].values()), "the refresh job would read the Airflow logins")
        for name in own:
            self.assertNotIn(f'"{name}"', self.code)   # read from deploy.toml, never typed

    def test_enough_memory_for_szz(self):
        self.assertIn(self.a["machine_type"], {"e2-standard-2", "e2-standard-4", "e2-highmem-2"})

    def test_people_get_the_vm_not_the_project(self):
        self.assertNotRegex(self.tf, r'resource "google_project_iam_member"')


if __name__ == "__main__":
    unittest.main()
