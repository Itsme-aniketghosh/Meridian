# Meridian phase 3 on Google Cloud (Model-Deployment/PLAN.md §2, step S1).
# Every name and setting comes from Model-Deployment/deploy.toml (contract D4), read by deploy_config.py.

terraform {
  required_version = ">= 1.7" # import blocks with for_each (imports.tf)

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }

  # The state lives in deploy.toml's [buckets] tfstate, made before init (README.md):
  #   terraform init -backend-config="bucket=<project>-tfstate"
  backend "gcs" {
    prefix = "terraform"
  }
}

data "external" "deploy" {
  program = ["python3", "${path.module}/../../Model-Deployment/deploy_config.py", "--terraform"]
  query   = { project = var.project_id }
}

locals {
  cfg     = jsondecode(data.external.deploy.result.json)
  project = local.cfg.gcp.project
  region  = local.cfg.gcp.region
  repo    = local.cfg.github.repository
  sa_ids  = local.cfg.service_accounts
}

provider "google" {
  project = local.project
  region  = local.region

  # The budget API needs a quota project when Terraform runs on a person's login
  user_project_override = true
  billing_project       = local.project
}
