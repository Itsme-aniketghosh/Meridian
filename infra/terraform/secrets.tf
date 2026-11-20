# The secrets' names only. A person adds each value once, so it never lands in git or in Terraform's state:
#   printf %s "$MERIDIAN_SLACK_WEBHOOK" | gcloud secrets versions add MERIDIAN_SLACK_WEBHOOK --data-file=-
resource "google_secret_manager_secret" "secret" {
  for_each  = local.cfg.secrets
  secret_id = each.value
  replication { # in the project's region only, like everything else (automatic replication is global)
    user_managed {
      replicas {
        location = local.region
      }
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_secret_manager_secret_iam_member" "refresh" {
  for_each  = google_secret_manager_secret.secret
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.sa.refresh
}
