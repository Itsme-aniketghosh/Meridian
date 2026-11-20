# What the other steps need: the endpoint's URL (PLAN §9's curl), and the names deploy.yml signs in and pushes with.

output "risk_url" {
  description = "The endpoint (D1): POST <risk_url>/score"
  value       = google_cloud_run_v2_service.risk.uri
}

output "workload_identity_provider" {
  description = "For google-github-actions/auth in deploy.yml and model.yml"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "service_accounts" {
  description = "Emails: ci (model.yml), deploy (deploy.yml), risk and refresh (what the service and job run as), scheduler"
  value       = { for k, v in google_service_account.sa : k => v.email }
}

output "images_repo" {
  description = "Where deploy.yml pushes the images"
  value       = "${local.region}-docker.pkg.dev/${local.project}/${google_artifact_registry_repository.images.repository_id}"
}

output "buckets" {
  description = "gs:// URLs: block, monitor (D2 reports), cache"
  value       = { for k, b in google_storage_bucket.data : k => b.url }
}

output "refresh_job" {
  description = "The job Cloud Scheduler starts daily"
  value       = google_cloud_run_v2_job.refresh.name
}

output "project_number" {
  description = "Must match deploy.toml [gcp] project_number on the team's project (workflows sign in with it)"
  value       = local.project_number
}
