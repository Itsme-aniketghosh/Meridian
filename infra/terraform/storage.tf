# The block (real names), the monitoring reports (D2) and the job's clones. Private, uniform access, one region.
# Destroy empties and removes them (the model versions in the registry are the record that stays).
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

  depends_on = [google_project_service.api]
}
