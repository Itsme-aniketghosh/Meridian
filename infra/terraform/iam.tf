# One service account per job, each with only what it needs, granted on the resource it needs it on.

locals {
  service_accounts = {
    ci        = "Meridian CI registry push" # phase 2's (model.yml), imported on the team's project
    deploy    = "Meridian deploy: images, revisions, traffic"
    risk      = "Meridian risk service"
    refresh   = "Meridian refresh job"
    scheduler = "Meridian scheduler: starts the refresh job"
  }
  sa = { for k, v in google_service_account.sa : k => "serviceAccount:${v.email}" }
}

resource "google_service_account" "sa" {
  for_each     = local.service_accounts
  account_id   = local.sa_ids[each.key]
  display_name = each.value

  depends_on = [google_project_service.api]
}

# Model registry: CI and the refresh job push models; deploy reads the champion to bake it into the image
resource "google_artifact_registry_repository_iam_member" "models" {
  for_each = {
    ci      = "roles/artifactregistry.writer"
    refresh = "roles/artifactregistry.writer"
    deploy  = "roles/artifactregistry.reader"
  }
  location   = local.region
  repository = google_artifact_registry_repository.models.name
  role       = each.value
  member     = local.sa[each.key]
}

# Docker repo: deploy pushes the images (Cloud Run's own agent pulls them)
resource "google_artifact_registry_repository_iam_member" "images_deploy" {
  location   = local.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = local.sa.deploy
}

# Buckets: the job writes all three. Deploy reads only history.pkl's folder (deploy.toml [serve_history]) to bake it
# into the serve image: nothing else in that bucket, and nothing in the block (real names). The service reads no
# bucket: its champion and history.pkl are in its image
resource "google_storage_bucket_iam_member" "refresh" {
  for_each = google_storage_bucket.data
  bucket   = each.value.name
  role     = "roles/storage.objectAdmin"
  member   = local.sa.refresh
}

locals {
  history_bucket = google_storage_bucket.data[local.cfg.serve_history.bucket].name
  history_folder = regex("^(.+/)[^/]+$", local.cfg.serve_history.object)[0] # history/latest.pkl -> history/
}

resource "google_storage_bucket_iam_member" "deploy_reads_history" {
  bucket = local.history_bucket
  role   = "roles/storage.objectViewer"
  member = local.sa.deploy

  # Objects under the folder only. A listing is checked against the bucket, not an object, so it's refused;
  # deploy.yml downloads one named object, which needs storage.objects.get alone
  condition {
    title       = "history.pkl only"
    description = "deploy.toml [serve_history]: the objects in its folder, nothing else in the bucket"
    expression  = "resource.name.startsWith(\"projects/_/buckets/${local.history_bucket}/objects/${local.history_folder}\")"
  }
}

# The job writes the drift and decay numbers as custom metrics (S5, S6)
resource "google_project_iam_member" "refresh_metrics" {
  project = local.project
  role    = "roles/monitoring.metricWriter"
  member  = local.sa.refresh
}

# deploy.yml deploys revisions that run as the service's and the job's accounts
resource "google_service_account_iam_member" "deploy_acts_as" {
  for_each           = toset(["risk", "refresh"])
  service_account_id = google_service_account.sa[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = local.sa.deploy
}
