# The block (real names), the monitoring reports (D2) and the job's clones. Private, uniform access, one region.
# Destroy empties and removes them (the model versions in the registry are the record that stays). No soft delete:
# the job rewrites these daily, and kept copies would be billed for 7 days and keep real names after a delete.
locals {
  buckets = {
    block   = local.cfg.buckets.block
    monitor = local.cfg.buckets.monitor
    cache   = local.cfg.buckets.cache
  }
}

resource "google_storage_bucket" "data" {
  for_each                    = local.buckets
  name                        = each.value
  location                    = upper(local.region)
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  soft_delete_policy {
    retention_duration_seconds = 0
  }

  depends_on = [google_project_service.api]
}
