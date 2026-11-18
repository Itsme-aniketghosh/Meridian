# The team's project already has these from phase 2, and CI's registry push uses them: adopt them, don't recreate
# them (adopt_existing = true). A fresh project (bootstrap.sh) has none, so adopt_existing stays false there.
# Once they're in the state, these blocks do nothing.

locals {
  adopt = var.adopt_existing ? toset(["phase2"]) : toset([])
}

import {
  for_each = local.adopt
  to       = google_artifact_registry_repository.models
  id       = "projects/${local.project}/locations/${local.region}/repositories/${local.cfg.registry.models}"
}

import {
  for_each = local.adopt
  to       = google_service_account.sa["ci"]
  id       = "projects/${local.project}/serviceAccounts/${local.sa_ids.ci}@${local.project}.iam.gserviceaccount.com"
}

import {
  for_each = local.adopt
  to       = google_iam_workload_identity_pool.github
  id       = "projects/${local.project}/locations/global/workloadIdentityPools/${local.cfg.workload_identity.pool}"
}

import {
  for_each = local.adopt
  to       = google_iam_workload_identity_pool_provider.github
  id       = "projects/${local.project}/locations/global/workloadIdentityPools/${local.cfg.workload_identity.pool}/providers/${local.cfg.workload_identity.provider}"
}
