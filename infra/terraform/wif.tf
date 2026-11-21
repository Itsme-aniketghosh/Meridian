# GitHub Actions signs in with no key (the university's organization blocks service-account keys).
# Phase 2's pool and provider, imported on the team's project: same settings, so CI's registry push keeps working.
# Only this repository's main branch gets a token; model.yml acts as ci, deploy.yml as deploy.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = local.cfg.workload_identity.pool
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.api]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = local.cfg.workload_identity.provider
  display_name                       = "Meridian main"
  attribute_condition                = "assertion.repository=='${local.repo}' && assertion.ref=='refs/heads/${local.cfg.github.branch}'"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github" {
  for_each           = toset(["ci", "deploy"])
  service_account_id = google_service_account.sa[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${local.repo}"
}
