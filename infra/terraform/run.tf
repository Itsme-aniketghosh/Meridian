# The endpoint (S2, contract D1) and the daily refresh job (S4). Terraform makes them with a placeholder image;
# deploy.yml ships the real images, and Terraform never puts its placeholder back (ignore_changes).

locals {
  risk    = local.cfg.services.risk
  refresh = local.cfg.jobs.refresh
}

resource "google_cloud_run_v2_service" "risk" {
  name                = local.risk.name
  location            = local.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.sa["risk"].email
    max_instance_request_concurrency = local.risk.concurrency
    timeout                          = "${local.risk.timeout_s}s"

    scaling {
      min_instance_count = local.risk.min_instances
      max_instance_count = local.risk.max_instances
    }

    containers {
      image = var.placeholder_service_image
      ports {
        container_port = 8080
      }
      resources {
        limits = {
          cpu    = local.risk.cpu
          memory = local.risk.memory
        }
        cpu_idle          = true # billed only while a request runs
        startup_cpu_boost = true # for the cold-start bar (PLAN §6)
      }
    }
  }

  lifecycle {
    ignore_changes = [
      client,
      client_version,
      traffic,
      template[0].containers[0].image,
      template[0].revision,
      template[0].labels,
      template[0].annotations,
    ]
  }

  depends_on = [google_project_service.api]
}

# Public, so graders can call it (PLAN §4): no names out, max 2 instances
resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = local.risk.public ? 1 : 0
  location = local.region
  name     = google_cloud_run_v2_service.risk.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "deploy" {
  location = local.region
  name     = google_cloud_run_v2_service.risk.name
  role     = "roles/run.developer"
  member   = local.sa.deploy
}

resource "google_cloud_run_v2_job" "refresh" {
  name                = local.refresh.name
  location            = local.region
  deletion_protection = false

  template {
    task_count = 1
    template {
      service_account = google_service_account.sa["refresh"].email
      timeout         = "${local.refresh.timeout_s}s"
      max_retries     = local.refresh.max_retries
      containers {
        image = var.placeholder_job_image
        resources {
          limits = {
            cpu    = local.refresh.cpu
            memory = local.refresh.memory
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
      template[0].labels,
      template[0].annotations,
    ]
  }

  depends_on = [google_project_service.api]
}

resource "google_cloud_run_v2_job_iam_member" "deploy" {
  location = local.region
  name     = google_cloud_run_v2_job.refresh.name
  role     = "roles/run.developer"
  member   = local.sa.deploy
}

resource "google_cloud_run_v2_job_iam_member" "scheduler" {
  location = local.region
  name     = google_cloud_run_v2_job.refresh.name
  role     = "roles/run.invoker"
  member   = local.sa.scheduler
}
