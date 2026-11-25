# S6, part 2 (PLAN §5 and §6, the packet's Wed Nov 25): metrics from the D5 log lines, the endpoint's two SLOs, and one
# dashboard whose top row is the SLO and cost panel the expo asks for: error budget, latency budget, cost per 1,000
# scores. Every bar and price is deploy.toml's ([service_alerts], [slo], [cost]).
# Cost: $0. Google doesn't bill dashboards or SLOs. The 4 log-based metrics are custom metrics of a few MiB a month,
# within the 150 MiB each billing account gets free.

locals {
  slo        = local.cfg.slo
  cost       = local.cfg.cost
  job_filter = "resource.type = \"cloud_run_job\" AND resource.labels.job_name = \"${local.job_name}\""
  requests   = "${local.revisions} AND metric.type = \"run.googleapis.com/request_count\""
  latencies  = "${local.revisions} AND metric.type = \"run.googleapis.com/request_latencies\""

  # One second of one service instance, and one request, at deploy.toml's [cost] list prices
  risk_memory_gib     = endswith(local.risk.memory, "Gi") ? tonumber(trimsuffix(local.risk.memory, "Gi")) : tonumber(trimsuffix(local.risk.memory, "Mi")) / 1024
  instance_second_usd = tonumber(local.risk.cpu) * local.cost.vcpu_second_usd + local.risk_memory_gib * local.cost.gib_second_usd
  request_usd         = local.cost.million_requests_usd / 1000000
}

# D5 lines as metrics: the service writes one per call (serve/app.py), the job one per step (refresh/job.py)

resource "google_logging_metric" "scores" {
  name        = "meridian_scores"
  description = "Scores the service answered (D5 event score, status 200), by model version"
  filter      = "${local.revisions} AND jsonPayload.event = \"score\" AND jsonPayload.status = 200"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "model_version"
      value_type = "STRING"
    }
  }
  label_extractors = {
    model_version = "EXTRACT(jsonPayload.model_version)"
  }

  depends_on = [google_project_service.api]
}

resource "google_logging_metric" "score_latency" {
  name            = "meridian_score_latency"
  description     = "The service's own time per answered score (D5 latency_ms), without the network or Cloud Run's queue"
  filter          = "${local.revisions} AND jsonPayload.event = \"score\" AND jsonPayload.status = 200"
  value_extractor = "EXTRACT(jsonPayload.latency_ms)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }
  bucket_options {
    exponential_buckets { # 1 ms to about 17 minutes
      num_finite_buckets = 20
      growth_factor      = 2
      scale              = 1
    }
  }

  depends_on = [google_project_service.api]
}

resource "google_logging_metric" "refresh_runs" {
  name        = "meridian_refresh_runs"
  description = "Refresh-job runs that ended (D5 step done, or a failed step), by outcome: ok, nothing new, failed"
  filter      = "${local.job_filter} AND jsonPayload.event = \"refresh\" AND (jsonPayload.step = \"done\" OR jsonPayload.outcome = \"failed\")"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "outcome"
      value_type = "STRING"
    }
  }
  label_extractors = {
    outcome = "EXTRACT(jsonPayload.outcome)"
  }

  depends_on = [google_project_service.api]
}

resource "google_logging_metric" "drift_checks" {
  name        = "meridian_drift_checks"
  description = "Drift checks in the refresh job (D5 event drift, S5's verdict), by retrain: true or false"
  filter      = "${local.job_filter} AND jsonPayload.event = \"drift\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key        = "retrain"
      value_type = "STRING"
    }
  }
  label_extractors = {
    retrain = "EXTRACT(jsonPayload.retrain)"
  }

  depends_on = [google_project_service.api]
}

# The endpoint as a service in Cloud Monitoring, so its SLOs have a home. Both count every request, the uptime
# check's /health included (Cloud Run's metrics can't tell paths apart), over the last window_days

resource "google_monitoring_custom_service" "risk" {
  service_id   = local.risk_name
  display_name = local.risk_name

  depends_on = [google_project_service.api]
}

# The error budget: requests that aren't 5xx
resource "google_monitoring_slo" "availability" {
  service             = google_monitoring_custom_service.risk.service_id
  slo_id              = "availability"
  display_name        = format("%.4g%% of requests aren't 5xx, rolling %d days", local.slo.availability_goal * 100, local.slo.window_days)
  goal                = local.slo.availability_goal
  rolling_period_days = local.slo.window_days

  request_based_sli {
    good_total_ratio {
      bad_service_filter   = "${local.requests} AND metric.labels.response_code_class = \"5xx\""
      total_service_filter = local.requests
    }
  }
}

# The latency budget: requests answered within p95_latency_s
resource "google_monitoring_slo" "latency" {
  service             = google_monitoring_custom_service.risk.service_id
  slo_id              = "latency"
  display_name        = format("%.4g%% of requests within %g s, rolling %d days", local.slo.latency_goal * 100, local.alerts.p95_latency_s, local.slo.window_days)
  goal                = local.slo.latency_goal
  rolling_period_days = local.slo.window_days

  request_based_sli {
    distribution_cut {
      distribution_filter = local.latencies
      range {
        max = local.alerts.p95_latency_s * 1000 # ms
      }
    }
  }
}

locals {
  budget = {
    error   = "select_slo_budget_fraction(\"${google_monitoring_slo.availability.name}\")"
    latency = "select_slo_budget_fraction(\"${google_monitoring_slo.latency.name}\")"
  }

  # Cost per 1,000 scores over the last window_days, in PromQL (Google's names: the first / becomes :, the rest _):
  # the service's billable instance seconds and its requests at list price, over the scores it answered
  slo_window = "${local.slo.window_days}d"
  promql = {
    billable = "run_googleapis_com:container_billable_instance_time{monitored_resource=\"cloud_run_revision\",service_name=\"${local.risk_name}\"}"
    requests = "run_googleapis_com:request_count{monitored_resource=\"cloud_run_revision\",service_name=\"${local.risk_name}\"}"
    scores   = "logging_googleapis_com:user_${google_logging_metric.scores.name}{monitored_resource=\"cloud_run_revision\",service_name=\"${local.risk_name}\"}"
  }
  cost_per_1000 = join("", [
    "1000 * (sum(increase(${local.promql.billable}[${local.slo_window}])) * ${format("%.10f", local.instance_second_usd)}",
    " + sum(increase(${local.promql.requests}[${local.slo_window}])) * ${format("%.10f", local.request_usd)})",
    " / sum(increase(${local.promql.scores}[${local.slo_window}]))",
  ])
  scores_in_window = "sum(increase(${local.promql.scores}[${local.slo_window}]))"

  bars = {
    error   = format("%.4g%%", local.alerts.error_rate * 100)
    latency = format("%g s", local.alerts.p95_latency_s)
    cold    = format("%g s", local.alerts.cold_start_s)
  }

  # A chart's series: a filter and how to align it
  series = {
    for k, v in {
      requests  = [local.requests, "60s", "ALIGN_RATE", "REDUCE_SUM", ["metric.label.response_code_class"]]
      instances = ["${local.revisions} AND metric.type = \"run.googleapis.com/container/instance_count\"", "60s", "ALIGN_MAX", "REDUCE_SUM", ["metric.label.state"]]
      uptime    = ["resource.type = \"uptime_url\" AND metric.type = \"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id = \"${google_monitoring_uptime_check_config.health.uptime_check_id}\"", "${local.alerts.uptime_period_s}s", "ALIGN_FRACTION_TRUE", "REDUCE_MEAN", ["metric.label.checker_location"]]
      scores    = ["resource.type = \"cloud_run_revision\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.scores.name}\"", "3600s", "ALIGN_SUM", "REDUCE_SUM", ["metric.label.model_version"]]
      runs      = ["resource.type = \"cloud_run_job\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.refresh_runs.name}\"", "86400s", "ALIGN_SUM", "REDUCE_SUM", ["metric.label.outcome"]]
      drift     = ["resource.type = \"cloud_run_job\" AND metric.type = \"logging.googleapis.com/user/${google_logging_metric.drift_checks.name}\"", "86400s", "ALIGN_SUM", "REDUCE_SUM", ["metric.label.retrain"]]
      } : k => {
      filter = v[0]
      aggregation = {
        alignmentPeriod    = v[1]
        perSeriesAligner   = v[2]
        crossSeriesReducer = v[3]
        groupByFields      = v[4]
      }
    }
  }

  # Google's API drops zero values, so a tile at x or y = 0 leaves that field out (no diff on the next plan)
  tiles = [for t in [
    { x = 0, y = 0, w = 48, h = 5, widget = {
      title = "How to read this"
      text = { format = "MARKDOWN", content = join(" ", [
        "**The top row is the SLO and cost panel** (PLAN §6), for the last ${local.slo.window_days} days.",
        "A budget is how much of that week's allowance is left: 100% untouched, below 0% the SLO is missed.",
        "The error budget lets ${local.bars.error} of requests fail; the latency budget lets ${format("%.4g%%", (1 - local.slo.latency_goal) * 100)} take longer than ${local.bars.latency}.",
        "Cost per 1,000 scores is Cloud Run's list price for the service's instance time and requests (the uptime check's included), over the scores it answered: before the free tier, which makes our real bill $0.",
        "Lines on the charts are deploy.toml's bars. Alerts page Slack (monitoring.tf).",
      ]) }
    } },
    { x = 0, y = 5, w = 12, h = 8, widget = {
      title = "Error budget left: ${google_monitoring_slo.availability.display_name}"
      scorecard = {
        timeSeriesQuery = { timeSeriesFilter = { filter = local.budget.error }, unitOverride = "10^2.%" }
        thresholds      = [{ value = 0.25, color = "YELLOW", direction = "BELOW" }, { value = 0.05, color = "RED", direction = "BELOW" }]
      }
    } },
    { x = 12, y = 5, w = 12, h = 8, widget = {
      title = "Latency budget left: ${google_monitoring_slo.latency.display_name}"
      scorecard = {
        timeSeriesQuery = { timeSeriesFilter = { filter = local.budget.latency }, unitOverride = "10^2.%" }
        thresholds      = [{ value = 0.25, color = "YELLOW", direction = "BELOW" }, { value = 0.05, color = "RED", direction = "BELOW" }]
      }
    } },
    { x = 24, y = 5, w = 12, h = 8, widget = {
      title     = "Cost per 1,000 scores, last ${local.slo.window_days} days (USD, list price)"
      scorecard = { timeSeriesQuery = { prometheusQuery = local.cost_per_1000 } }
    } },
    { x = 36, y = 5, w = 12, h = 8, widget = {
      title     = "Scores answered, last ${local.slo.window_days} days"
      scorecard = { timeSeriesQuery = { prometheusQuery = local.scores_in_window } }
    } },
    { x = 0, y = 13, w = 24, h = 8, widget = {
      title = "Budgets left over time (100% = untouched)"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets = [for name, filter in local.budget : {
          legendTemplate  = "${name} budget"
          plotType        = "LINE"
          targetAxis      = "Y1"
          timeSeriesQuery = { timeSeriesFilter = { filter = filter }, unitOverride = "10^2.%" }
        }]
        yAxis = { scale = "LINEAR" }
      }
    } },
    { x = 24, y = 13, w = 24, h = 8, widget = {
      title = "Cost per 1,000 scores over time, each point the ${local.slo.window_days} days before it (USD, list price)"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets     = [{ plotType = "LINE", targetAxis = "Y1", timeSeriesQuery = { prometheusQuery = local.cost_per_1000 } }]
        yAxis        = { scale = "LINEAR" }
      }
    } },
    { x = 0, y = 21, w = 24, h = 8, widget = {
      title = "Requests per second, by response class"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets     = [{ plotType = "STACKED_AREA", targetAxis = "Y1", timeSeriesQuery = { timeSeriesFilter = local.series.requests } }]
        yAxis        = { scale = "LINEAR" }
      }
    } },
    { x = 24, y = 21, w = 24, h = 8, widget = {
      title = "5xx share of requests, ${local.alerts.error_window_min}-minute windows (alert above ${local.bars.error})"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets = [{ plotType = "LINE", targetAxis = "Y1", timeSeriesQuery = { timeSeriesFilterRatio = {
          numerator   = { filter = "${local.requests} AND metric.labels.response_code_class = \"5xx\"", aggregation = { alignmentPeriod = local.window, perSeriesAligner = "ALIGN_DELTA", crossSeriesReducer = "REDUCE_SUM", groupByFields = ["resource.label.service_name"] } }
          denominator = { filter = local.requests, aggregation = { alignmentPeriod = local.window, perSeriesAligner = "ALIGN_DELTA", crossSeriesReducer = "REDUCE_SUM", groupByFields = ["resource.label.service_name"] } }
        } } }]
        thresholds = [{ label = "alert: ${local.bars.error}", value = local.alerts.error_rate, targetAxis = "Y1" }]
        yAxis      = { scale = "LINEAR" }
      }
    } },
    { x = 0, y = 29, w = 24, h = 8, widget = {
      title = "Request latency p50 and p95, ms (alert above ${local.bars.latency})"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets = [for p in [50, 95] : {
          legendTemplate  = "p${p}"
          plotType        = "LINE"
          targetAxis      = "Y1"
          timeSeriesQuery = { timeSeriesFilter = { filter = local.latencies, aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_DELTA", crossSeriesReducer = "REDUCE_PERCENTILE_${p}" } } }
        }]
        thresholds = [{ label = "alert: p95 ${local.bars.latency}", value = local.alerts.p95_latency_s * 1000, targetAxis = "Y1" }]
        yAxis      = { scale = "LINEAR" }
      }
    } },
    { x = 24, y = 29, w = 24, h = 8, widget = {
      title = "Cold starts: container start-up p50 and p95, ms (bar ${local.bars.cold})"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets = [for p in [50, 95] : {
          legendTemplate  = "p${p}"
          plotType        = "LINE"
          targetAxis      = "Y1"
          timeSeriesQuery = { timeSeriesFilter = { filter = "${local.revisions} AND metric.type = \"run.googleapis.com/container/startup_latencies\"", aggregation = { alignmentPeriod = "3600s", perSeriesAligner = "ALIGN_DELTA", crossSeriesReducer = "REDUCE_PERCENTILE_${p}" } } }
        }]
        thresholds = [{ label = "bar: ${local.bars.cold}", value = local.alerts.cold_start_s * 1000, targetAxis = "Y1" }]
        yAxis      = { scale = "LINEAR" }
      }
    } },
    { x = 0, y = 37, w = 16, h = 8, widget = {
      title = "Instances, active and idle (max ${local.risk.max_instances})"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets     = [{ plotType = "STACKED_AREA", targetAxis = "Y1", timeSeriesQuery = { timeSeriesFilter = local.series.instances } }]
        thresholds   = [{ label = "max", value = local.risk.max_instances, targetAxis = "Y1" }]
        yAxis        = { scale = "LINEAR" }
      }
    } },
    { x = 16, y = 37, w = 16, h = 8, widget = {
      title = "Scores per hour, by model version (a canary shows two)"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets     = [{ plotType = "STACKED_BAR", targetAxis = "Y1", timeSeriesQuery = { timeSeriesFilter = local.series.scores } }]
        yAxis        = { scale = "LINEAR" }
      }
    } },
    { x = 32, y = 37, w = 16, h = 8, widget = {
      title = "/health uptime check passing, by region"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets     = [{ plotType = "LINE", targetAxis = "Y1", timeSeriesQuery = { timeSeriesFilter = local.series.uptime } }]
        yAxis        = { scale = "LINEAR" }
      }
    } },
    { x = 0, y = 45, w = 16, h = 8, widget = {
      title = "Refresh-job runs per day, by outcome"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets     = [{ plotType = "STACKED_BAR", targetAxis = "Y1", timeSeriesQuery = { timeSeriesFilter = local.series.runs } }]
        yAxis        = { scale = "LINEAR" }
      }
    } },
    { x = 16, y = 45, w = 16, h = 8, widget = {
      title = "Drift checks per day, by verdict (retrain true or false)"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets     = [{ plotType = "STACKED_BAR", targetAxis = "Y1", timeSeriesQuery = { timeSeriesFilter = local.series.drift } }]
        yAxis        = { scale = "LINEAR" }
      }
    } },
    { x = 32, y = 45, w = 16, h = 8, widget = {
      title = "Billable seconds per hour: the service and the job"
      xyChart = {
        chartOptions = { mode = "COLOR" }
        dataSets = [for name, filter in { service = local.revisions, job = local.job_filter } : {
          legendTemplate  = name
          plotType        = "STACKED_BAR"
          targetAxis      = "Y1"
          timeSeriesQuery = { timeSeriesFilter = { filter = "${filter} AND metric.type = \"run.googleapis.com/container/billable_instance_time\"", aggregation = { alignmentPeriod = "3600s", perSeriesAligner = "ALIGN_SUM", crossSeriesReducer = "REDUCE_SUM" } } }
        }]
        yAxis = { scale = "LINEAR" }
      }
    } },
    { x = 0, y = 53, w = 48, h = 8, widget = {
      title = "Incidents from the 5 alerts"
      # the widget takes alertPolicies/<id>, not the policy's full name (Google refuses projects/<p>/alertPolicies/<id>)
      incidentList = { policyNames = [for p in [
        google_monitoring_alert_policy.down, google_monitoring_alert_policy.errors, google_monitoring_alert_policy.slow,
        google_monitoring_alert_policy.job_failed, google_monitoring_alert_policy.drift,
      ] : regex("alertPolicies/[^/]+$", p.name)] }
    } },
  ] : merge({ width = t.w, height = t.h, widget = t.widget }, { for k, v in { xPos = t.x, yPos = t.y } : k => v if v > 0 })]
}

resource "google_monitoring_dashboard" "meridian" {
  dashboard_json = jsonencode({
    displayName  = "Meridian (S6): the endpoint, its SLOs and cost, the refresh job"
    mosaicLayout = { columns = 48, tiles = local.tiles }
  })

  depends_on = [google_project_service.api]
}
