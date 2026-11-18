variable "project_id" {
  description = "The project to deploy into. Empty: deploy.toml's [gcp] project. bootstrap.sh passes a fresh one."
  type        = string
  default     = ""
}

variable "billing_account" {
  description = "Billing account id for the budget (C7). Empty: no budget. Keep it out of git: terraform.tfvars or TF_VAR_billing_account."
  type        = string
  default     = ""
}

variable "adopt_existing" {
  description = "The team's project only: import phase 2's registry repo, CI service account and Workload Identity pool instead of creating them."
  type        = bool
  default     = false
}

variable "placeholder_service_image" {
  description = "The service's first image, until deploy.yml ships the real one. Terraform leaves the image alone after."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "placeholder_job_image" {
  description = "The job's first image, until the refresh job's image is pushed."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job"
}
