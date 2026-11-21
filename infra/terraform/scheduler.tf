# Daily: start the refresh job (deploy.toml [schedule]). Paused until the job's real image is in.
resource "google_cloud_scheduler_job" "refresh" {
  name             = "${local.refresh.name}-daily"
  region           = local.region
  schedule         = local.cfg.schedule.cron
  time_zone        = local.cfg.schedule.time_zone
  paused           = local.cfg.schedule.paused
  attempt_deadline = "180s"

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/${google_cloud_run_v2_job.refresh.id}:run"
    oauth_token {
      service_account_email = google_service_account.sa["scheduler"].email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_service.api, google_cloud_run_v2_job_iam_member.scheduler]
}
