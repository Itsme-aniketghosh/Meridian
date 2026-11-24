# C7: alerts at 50, 90 and 100% of $25 over the phase (deploy.toml [budget]), to the billing account's admins and
# users and to the team's emails (terraform.tfvars, at most 5). Credits are left out, so the alerts track what the
# project uses: with credits counted, the bill shows $0 and no alert would ever fire.

locals {
  project_number = split("/", google_iam_workload_identity_pool.github.name)[1]
  budget_start   = [for p in split("-", local.cfg.budget.start) : tonumber(p)]
  budget_end     = [for p in split("-", local.cfg.budget.end) : tonumber(p)]
}

resource "google_billing_budget" "project" {
  billing_account = var.billing_account
  display_name    = "meridian ${local.project}"

  budget_filter {
    projects               = ["projects/${local.project_number}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
    custom_period {
      start_date {
        year  = local.budget_start[0]
        month = local.budget_start[1]
        day   = local.budget_start[2]
      }
      end_date {
        year  = local.budget_end[0]
        month = local.budget_end[1]
        day   = local.budget_end[2]
      }
    }
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

  # Only with team emails. The admins and users get the alerts without it, and a rule holding only defaults reads
  # back from Google as absent, so every plan would show it as a change (the Nov 19 apply did).
  dynamic "all_updates_rule" {
    for_each = length(var.alert_emails) > 0 ? [1] : []
    content {
      monitoring_notification_channels = [for c in google_monitoring_notification_channel.team : c.id] # monitoring.tf
      disable_default_iam_recipients   = false
    }
  }

  depends_on = [google_project_service.api]
}
