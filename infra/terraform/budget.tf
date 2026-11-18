# C7: alerts at 50, 90 and 100% of $25 (deploy.toml [budget]). Credits are left out, so the alerts track what the
# project uses; with credits counted, the bill shows $0 and no alert would ever fire.
# The billing account id stays out of git (variables.tf); without it there's no budget.

locals {
  project_number = split("/", google_iam_workload_identity_pool.github.name)[1]
}

resource "google_billing_budget" "project" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "meridian ${local.project}"

  budget_filter {
    projects               = ["projects/${local.project_number}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(local.cfg.budget.amount_usd)
    }
  }

  dynamic "threshold_rules" {
    for_each = local.cfg.budget.alert_at
    content {
      threshold_percent = threshold_rules.value
    }
  }

  depends_on = [google_project_service.api]
}
