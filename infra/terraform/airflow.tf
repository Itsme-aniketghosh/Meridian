# The hosted Airflow (deploy.toml [airflow]; Aniket signed off Nov 25): Data-Pipeline's docker compose on one VM, for
# the full product pipeline and the weekly rebuild. Spark's daily update stays with the refresh job (run.tf).
#
# - No public IP. Outbound (git, Jira, GitLab) goes through Cloud NAT; inbound is SSH from Google's IAP range only,
#   so the UI is a tunnel to the VM's localhost:8080 (README.md, "Airflow").
# - On only in a daily window (an instance schedule), so it costs ~4 h of compute a day. The data disk keeps the
#   clone, the caches and published/ between windows, with a daily snapshot.
# - Its account writes only [airflow] publish_prefix in the block bucket (never latest.json, the job's pointer) and
#   reads only its own two secrets. People on var.airflow_users get this VM, not the project.

locals {
  airflow       = local.cfg.airflow
  airflow_users = toset([for email in var.airflow_users : "user:${email}"])
  airflow_tag   = local.airflow.name
  iap_range     = "35.235.240.0/20" # Google's IAP TCP forwarding range (cloud.google.com/iap/docs/using-tcp-forwarding)
  airflow_secrets = {
    admin = local.airflow.admin_password_secret
    db    = local.airflow.db_password_secret
  }
}

# ---------- network: private, outbound through NAT ----------

resource "google_compute_network" "airflow" {
  name                    = local.airflow.name
  auto_create_subnetworks = false

  depends_on = [google_project_service.api]
}

resource "google_compute_subnetwork" "airflow" {
  name                     = local.airflow.name
  network                  = google_compute_network.airflow.id
  region                   = local.region
  ip_cidr_range            = "10.20.0.0/24"
  private_ip_google_access = true # Secret Manager and Cloud Storage without a public IP
}

resource "google_compute_router" "airflow" {
  name    = local.airflow.name
  network = google_compute_network.airflow.id
  region  = local.region
}

resource "google_compute_router_nat" "airflow" {
  name                               = local.airflow.name
  router                             = google_compute_router.airflow.name
  region                             = local.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# SSH from IAP only: the UI and the API listen on the VM's localhost, never on the network
resource "google_compute_firewall" "airflow_iap_ssh" {
  name          = "${local.airflow.name}-iap-ssh"
  network       = google_compute_network.airflow.id
  direction     = "INGRESS"
  source_ranges = [local.iap_range]
  target_tags   = [local.airflow_tag]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# ---------- secrets: the Airflow login and its database; a person adds the values ----------
#   printf %s "$PASSWORD" | gcloud secrets versions add <[airflow] admin_password_secret> --data-file=-

resource "google_secret_manager_secret" "airflow" {
  for_each  = local.airflow_secrets
  secret_id = each.value
  replication {
    user_managed {
      replicas {
        location = local.region
      }
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_secret_manager_secret_iam_member" "airflow" {
  for_each  = google_secret_manager_secret.airflow
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.sa.airflow
}

# The VM's alerts post to the same Slack as the job's (meridian.alert)
resource "google_secret_manager_secret_iam_member" "airflow_slack" {
  secret_id = google_secret_manager_secret.secret["slack_webhook"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.sa.airflow
}

# ---------- storage: the full block, under publish_prefix only ----------

resource "google_storage_bucket_iam_member" "airflow_publishes_product" {
  bucket = google_storage_bucket.data["block"].name
  role   = "roles/storage.objectAdmin"
  member = local.sa.airflow

  condition {
    title       = "publish_prefix only"
    description = "deploy.toml [airflow] publish_prefix: the full block, never latest.json or the job's blocks"
    expression  = "resource.name.startsWith(\"projects/_/buckets/${google_storage_bucket.data["block"].name}/objects/${local.airflow.publish_prefix}\")"
  }
}

# ---------- the VM, its data disk, its window and snapshots ----------

resource "google_compute_disk" "airflow_data" {
  name = "${local.airflow.name}-data"
  zone = local.airflow.zone
  type = "pd-standard"
  size = local.airflow.data_disk_gb

  depends_on = [google_project_service.api]
}

resource "google_compute_resource_policy" "airflow_window" {
  name   = "${local.airflow.name}-window"
  region = local.region
  instance_schedule_policy {
    vm_start_schedule {
      schedule = local.airflow.window_start
    }
    vm_stop_schedule {
      schedule = local.airflow.window_stop
    }
    time_zone = local.airflow.time_zone
  }

  depends_on = [google_project_service.api]
}

resource "google_compute_resource_policy" "airflow_snapshots" {
  name   = "${local.airflow.name}-snapshots"
  region = local.region
  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "07:00" # UTC, after the window: the disk is quiet
      }
    }
    retention_policy {
      max_retention_days    = local.airflow.snapshot_days
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      storage_locations = [local.region]
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_compute_disk_resource_policy_attachment" "airflow_snapshots" {
  name = google_compute_resource_policy.airflow_snapshots.name
  disk = google_compute_disk.airflow_data.name
  zone = local.airflow.zone
}

resource "google_compute_instance" "airflow" {
  name                      = local.airflow.name
  zone                      = local.airflow.zone
  machine_type              = local.airflow.machine_type
  tags                      = [local.airflow_tag]
  allow_stopping_for_update = true
  resource_policies         = [google_compute_resource_policy.airflow_window.id]

  boot_disk {
    initialize_params {
      image = local.airflow.image
      size  = local.airflow.boot_disk_gb
      type  = "pd-standard"
    }
  }

  attached_disk {
    source      = google_compute_disk.airflow_data.id
    device_name = "data" # /dev/disk/by-id/google-data
  }

  network_interface {
    subnetwork = google_compute_subnetwork.airflow.id # no access_config: no public IP
  }

  service_account {
    email  = google_service_account.sa["airflow"].email
    scopes = ["cloud-platform"] # what it may do is its IAM roles above
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin = "TRUE" # SSH by IAM (airflow_users), never by keys in metadata
    startup-script = templatefile("${path.module}/airflow-startup.sh.tftpl", {
      repository   = local.repo
      ref          = local.airflow.ref
      admin_secret = local.airflow.admin_password_secret
      db_secret    = local.airflow.db_password_secret
      slack_secret = local.cfg.secrets.slack_webhook
      project      = local.project
    })
  }

  depends_on = [google_compute_router_nat.airflow, google_secret_manager_secret_iam_member.airflow]
}

# The instance schedule starts and stops the VM as Compute Engine's own service agent, on this VM only
resource "google_compute_instance_iam_member" "airflow_window" {
  zone          = local.airflow.zone
  instance_name = google_compute_instance.airflow.name
  role          = "roles/compute.instanceAdmin.v1"
  member        = "serviceAccount:service-${local.project_number}@compute-system.iam.gserviceaccount.com"
}

# ---------- people: this VM only ----------

resource "google_iap_tunnel_instance_iam_member" "airflow_users" {
  for_each = local.airflow_users
  zone     = local.airflow.zone
  instance = google_compute_instance.airflow.name
  role     = "roles/iap.tunnelResourceAccessor"
  member   = each.value
}

# sudo on the VM: docker compose runs as root
resource "google_compute_instance_iam_member" "airflow_users" {
  for_each      = local.airflow_users
  zone          = local.airflow.zone
  instance_name = google_compute_instance.airflow.name
  role          = "roles/compute.osAdminLogin"
  member        = each.value
}

# OS Login on a VM with a service account needs this on that account
resource "google_service_account_iam_member" "airflow_users" {
  for_each           = local.airflow_users
  service_account_id = google_service_account.sa["airflow"].name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}
