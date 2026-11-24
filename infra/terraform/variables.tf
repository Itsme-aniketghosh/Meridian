variable "project_id" {
  description = "The project to deploy into. Empty: deploy.toml's [gcp] project. bootstrap.sh passes a fresh one."
  type        = string
  default     = ""
}

# No defaults on purpose: an apply without terraform.tfvars stops and asks, instead of planning to remove the budget
# or its recipients. Both stay out of git (terraform.tfvars.example is the template).
variable "billing_account" {
  description = "Billing account id for the budget (C7)."
  type        = string
}

variable "alert_emails" {
  description = "The team's emails for the budget and service alerts, besides the billing account's admins and users. At most 5."
  type        = list(string)
  validation {
    condition     = length(var.alert_emails) <= 5
    error_message = "A budget takes at most 5 notification channels."
  }
}

variable "slack_channel" {
  description = "The Slack channel's display name in Cloud Monitoring, once a person has connected it in the console (README.md). Empty: no Slack."
  type        = string
  default     = ""
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
