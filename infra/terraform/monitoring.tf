# S6, Cloud Monitoring (PLAN §5 and §6): an uptime check on /health and the service alerts. They page Slack and the
# team's emails, and never retrain. Every threshold from the plan is deploy.toml's [service_alerts] (D4); the rest
# are Google's usual settings (the uptime alert's 2 regions, the windows below).
# Cost: $0. The uptime check runs about 26,000 times a month, of 1 million free per project, and Google charges
# nothing for alerting until at least Sept 1, 2027 (then about $0.35 a month per alert, the uptime one excepted).

locals {
  alerts    = local.cfg.service_alerts
  window    = "${local.alerts.error_window_min * 60}s"
  risk_name = google_cloud_run_v2_service.risk.name
  job_name  = google_cloud_run_v2_job.refresh.name
  revisions = "resource.type = \"cloud_run_revision\" AND resource.labels.service_name = \"${local.risk_name}\""

  # Who hears: the team's emails (terraform.tfvars) and, once someone has connected it, Slack
  alert_channels = concat(
    [for c in google_monitoring_notification_channel.team : c.name],
    data.google_monitoring_notification_channel.slack[*].name,
  )
}

# The team's emails, for these alerts and the budget's (budget.tf)
resource "google_monitoring_notification_channel" "team" {
  for_each     = toset(var.alert_emails)
  display_name = "Meridian alerts"
  type         = "email"
  labels = {
    email_address = each.value
  }

  depends_on = [google_project_service.api]
}

moved {
  from = google_monitoring_notification_channel.budget
  to   = google_monitoring_notification_channel.team
}

# Google connects Slack only in the console, where a person signs in to Slack once (README.md). Then slack_channel
# in terraform.tfvars names that channel, and every alert below pages it
data "google_monitoring_notification_channel" "slack" {
  count        = var.slack_channel != "" ? 1 : 0
  type         = "slack"
  display_name = var.slack_channel
}

# Every plan warns while nobody would hear the alerts
check "someone_hears_the_alerts" {
  assert {
    condition     = length(local.alert_channels) > 0
    error_message = "The alerts page nobody yet: set alert_emails or slack_channel in terraform.tfvars (README.md, Alerts)."
  }
}

# /health from each region every uptime_period_s, with the service's own timeout
resource "google_monitoring_uptime_check_config" "health" {
  display_name     = "${local.risk_name} /health"
  period           = "${local.alerts.uptime_period_s}s"
  timeout          = "${local.cfg.services.risk.timeout_s}s"
  selected_regions = local.alerts.uptime_regions

  http_check {
    path           = "/health" # D1
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = local.project
      host       = trimprefix(google_cloud_run_v2_service.risk.uri, "https://")
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_monitoring_alert_policy" "down" {
  display_name          = "${local.risk_name}: /health failing"
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.alert_channels

  conditions {
    display_name = "/health fails from 2 or more regions"
    condition_threshold {
      filter          = "resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.health.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "60s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      `${local.risk_name}`'s `/health` fails from 2 or more regions, so callers can't reach `/score` (D1).
      A deploy that broke it rolls back by itself (deploy.yml). Otherwise, read the service's logs in Cloud Run.
    EOT
  }

  depends_on = [google_project_service.api]
}

resource "google_monitoring_alert_policy" "errors" {
  display_name          = format("%s: 5xx above %.4g%% of requests", local.risk_name, local.alerts.error_rate * 100) # %.4g: 1, not 0.999…
  combiner              = "OR"
  severity              = "CRITICAL"
  notification_channels = local.alert_channels

  conditions {
    display_name = "5xx share of requests over the last ${local.alerts.error_window_min} min"
    condition_threshold {
      filter                  = "${local.revisions} AND metric.type = \"run.googleapis.com/request_count\" AND metric.labels.response_code_class = \"5xx\""
      denominator_filter      = "${local.revisions} AND metric.type = \"run.googleapis.com/request_count\""
      comparison              = "COMPARISON_GT"
      threshold_value         = local.alerts.error_rate
      duration                = "60s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE" # no data: resolved
      aggregations {
        alignment_period     = local.window
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
      denominator_aggregations {
        alignment_period     = local.window
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }

  # Missing data counts as resolved, so the incident closes once its window empties (within 30 minutes) and the next
  # failure pages again. Google's default would keep it open 7 days
  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      More than ${format("%.4g", local.alerts.error_rate * 100)}% of `${local.risk_name}`'s requests got a 5xx over the last ${local.alerts.error_window_min} minutes (PLAN §5).
      Read the service's logs in Cloud Run. If a new revision caused it, move traffic back to the one before.
    EOT
  }

  depends_on = [google_project_service.api]
}

resource "google_monitoring_alert_policy" "slow" {
  display_name          = "${local.risk_name}: p95 latency above ${local.alerts.p95_latency_s} s"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.alert_channels

  conditions {
    display_name = "p95 request latency over the last ${local.alerts.error_window_min} min"
    condition_threshold {
      filter                  = "${local.revisions} AND metric.type = \"run.googleapis.com/request_latencies\""
      comparison              = "COMPARISON_GT"
      threshold_value         = local.alerts.p95_latency_s * 1000 # the metric is in ms, and leaves out container start-up
      duration                = "60s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE" # no data: resolved
      aggregations {
        alignment_period     = local.window
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_PERCENTILE_95"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }

  # Missing data counts as resolved, so the incident closes once its window empties (within 30 minutes) and the next
  # failure pages again. Google's default would keep it open 7 days
  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      `${local.risk_name}`'s p95 latency is above ${local.alerts.p95_latency_s} s over the last ${local.alerts.error_window_min} minutes (PLAN §6: p95 ≤ ${local.alerts.p95_latency_s} s warm).
      Read the service's logs in Cloud Run: each `/score` line carries its `latency_ms` (D5).
    EOT
  }

  depends_on = [google_project_service.api]
}

resource "google_monitoring_alert_policy" "job_failed" {
  display_name          = "${local.job_name} failed"
  combiner              = "OR"
  severity              = "ERROR"
  notification_channels = local.alert_channels

  conditions {
    display_name = "an execution of ${local.job_name} failed"
    condition_threshold {
      filter                  = "resource.type = \"cloud_run_job\" AND resource.labels.job_name = \"${local.job_name}\" AND metric.type = \"run.googleapis.com/job/completed_execution_count\" AND metric.labels.result = \"failed\""
      comparison              = "COMPARISON_GT"
      threshold_value         = 0
      duration                = "60s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_INACTIVE" # no data: resolved
      aggregations {
        alignment_period     = local.window
        per_series_aligner   = "ALIGN_DELTA"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.job_name"]
      }
    }
  }

  # Missing data counts as resolved, so the incident closes once its window empties (within 30 minutes) and the next
  # failure pages again. Google's default would keep it open 7 days
  alert_strategy {
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      The refresh job (S4) failed, so this run made no new block or monitoring report.
      Its log lines in Cloud Run (D5) name the step that failed.
    EOT
  }

  depends_on = [google_project_service.api]
}

# Drift: the job's own D5 line, matched in its logs (a log-based alert)
resource "google_monitoring_alert_policy" "drift" {
  display_name          = "Drift found: retrain due"
  combiner              = "OR"
  severity              = "WARNING"
  notification_channels = local.alert_channels

  conditions {
    display_name = "${local.job_name} logs drift with retrain = true (D5)"
    condition_matched_log {
      filter = "resource.type = \"cloud_run_job\" AND resource.labels.job_name = \"${local.job_name}\" AND jsonPayload.event = \"drift\" AND jsonPayload.retrain = true"
      label_extractors = {
        run_id = "EXTRACT(jsonPayload.run_id)"
        why    = "EXTRACT(jsonPayload.why)"
      }
    }
  }

  # Google requires a rate limit on log-based alerts: at most one message per 5 minutes
  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = <<-EOT
      The refresh job found drift on the new block, and its report (D2) says retrain: $${log.extracted_label.why} (run $${log.extracted_label.run_id}).
      Retraining is the job's decision (S4, S5); this alert only tells the team.
    EOT
  }

  depends_on = [google_project_service.api]
}
