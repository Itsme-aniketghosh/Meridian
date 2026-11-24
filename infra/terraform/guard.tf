# Aniket's review of S1: a `terraform destroy` on the team's project would delete CI's sign-in (phase 2's Workload
# Identity and meridian-ci), the block and the service. This resource depends on every other one, so whatever would
# delete one of them has to remove the guard first, and on the team's project the guard refuses. That covers:
# - a plain destroy, and one with -target;
# - an apply pointed at another project (triggers_replace), say a fresh project's run left on the team's state;
# - an apply that would replace CI's sign-in, its account, the registry or a bucket (replace_triggered_by).
# An `apply -target` skips the guard, so every run also checks that its state is its project's own bucket
# (Model-Deployment/deploy_config.py): a run for one project never plans with another's state, targeted or not.
# Ordinary in-place changes go through. On a fresh project the guard steps aside. To tear the team's project down on
# purpose, after grading (README.md):
#   terraform apply -var allow_team_destroy=true
#   terraform destroy
# It stops accidents, not intent: deleting this file, `terraform state rm` or `taint` on it get past it.
# Model-Deployment/tests/test_deploy_config.py fails if a resource is missing from the list below.

resource "terraform_data" "team_guard" {
  input            = length(local.adopt) > 0 && !var.allow_team_destroy # true: the team's project, guarded
  triggers_replace = local.project

  provisioner "local-exec" {
    when    = destroy
    command = self.input ? "echo 'team_guard: this is the team project, so nothing is destroyed or replaced (infra/terraform/guard.tf)' && exit 1" : "exit 0"
  }

  lifecycle {
    replace_triggered_by = [
      google_iam_workload_identity_pool.github.id,
      google_iam_workload_identity_pool_provider.github.id,
      google_service_account.sa["ci"].id,
      google_service_account_iam_member.github["ci"].id,
      google_artifact_registry_repository_iam_member.models["ci"].id,
      google_artifact_registry_repository.models.id,
      google_storage_bucket.data["block"].id,
      google_storage_bucket.data["monitor"].id,
      google_storage_bucket.data["cache"].id,
    ]
  }

  depends_on = [
    google_project_service.api,
    google_storage_bucket.data,
    google_artifact_registry_repository.models,
    google_artifact_registry_repository.images,
    google_service_account.sa,
    google_iam_workload_identity_pool.github,
    google_iam_workload_identity_pool_provider.github,
    google_service_account_iam_member.github,
    google_artifact_registry_repository_iam_member.models,
    google_artifact_registry_repository_iam_member.images_deploy,
    google_storage_bucket_iam_member.refresh,
    google_storage_bucket_iam_member.deploy_reads_history,
    google_project_iam_member.refresh_metrics,
    google_service_account_iam_member.deploy_acts_as,
    google_cloud_run_v2_service.risk,
    google_cloud_run_v2_service_iam_member.public,
    google_cloud_run_v2_service_iam_member.deploy,
    google_cloud_run_v2_job.refresh,
    google_cloud_run_v2_job_iam_member.deploy,
    google_cloud_run_v2_job_iam_member.scheduler,
    google_cloud_scheduler_job.refresh,
    google_secret_manager_secret.secret,
    google_secret_manager_secret_iam_member.refresh,
    google_billing_budget.project,
    google_monitoring_notification_channel.team,
    google_monitoring_uptime_check_config.health,
    google_monitoring_alert_policy.down,
    google_monitoring_alert_policy.errors,
    google_monitoring_alert_policy.slow,
    google_monitoring_alert_policy.job_failed,
    google_monitoring_alert_policy.drift,
  ]
}
