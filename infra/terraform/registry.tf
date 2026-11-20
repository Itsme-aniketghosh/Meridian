# Phase 2's model registry (imported on the team's project, imports.tf) and the Docker repo for the images.

resource "google_artifact_registry_repository" "models" {
  repository_id = local.cfg.registry.models
  location      = local.region
  format        = "GENERIC"
  description   = "Meridian: the champion model and champion.json (model.registry --push)"

  # destroy lets go of it instead of deleting it: the model versions are phase 2's record (the packet)
  deletion_policy = "ABANDON"

  depends_on = [google_project_service.api]
}

resource "google_artifact_registry_repository" "images" {
  repository_id = local.cfg.registry.images
  location      = local.region
  format        = "DOCKER"
  description   = "Meridian: the serving and job images (deploy.yml)"

  # Keep the newest few versions of each image (the serving one and the ones before it); delete older ones after a
  # day. Planted broken images count too, so push a good one after them
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-newest"
    action = "KEEP"
    most_recent_versions {
      keep_count = local.cfg.registry.keep_images
    }
  }
  cleanup_policies {
    id     = "delete-older"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "86400s"
    }
  }

  depends_on = [google_project_service.api]
}
