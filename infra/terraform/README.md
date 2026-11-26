# infra/terraform: Meridian on Google Cloud (S1)

Everything phase 3 runs on GCP, as code (`Model-Deployment/PLAN.md` §2–3). Every name and setting comes from `Model-Deployment/deploy.toml` (contract D4), read through `deploy_config.py`. Change a name there, never here: `Model-Deployment/tests/test_deploy_config.py` fails if a `.tf` file types one, or if phase 2's copies in `Model-Pipeline/model.toml` and `model.yml` disagree with it.

## What it makes

| Resource | Name in `deploy.toml` | For |
|---|---|---|
| Cloud Run service, public, 0–2 instances, 1 CPU, 1 GiB | `services.risk`: `meridian-risk` | S2's endpoint (D1), deployed by S3 |
| Cloud Run job, plus a daily Cloud Scheduler trigger at 03:00 UTC, created paused | `jobs.refresh`: `meridian-refresh`, `meridian-refresh-daily` | S4 |
| Three private buckets with uniform access and no soft delete | `buckets`: `<project>-meridian-block`, `-monitor` (D2 reports), `-cache` | S4, S5 |
| Artifact Registry | `registry.models`: `meridian-models` (phase 2's, adopted, and never deleted by `destroy`); `registry.images`: `meridian-images`, Docker, which keeps the newest 3 of each image | the registry; S3 |
| Service accounts | `service_accounts`: `meridian-ci` (phase 2's), `-deploy`, `-risk`, `-refresh`, `-scheduler` | below |
| Workload Identity: GitHub Actions on this repository's main branch only | `workload_identity`: pool `github`, provider `meridian` (phase 2's, adopted) | `model.yml`, `deploy.yml` |
| Secret Manager: the names only, stored in us-east1 | `secrets`: `MERIDIAN_SLACK_WEBHOOK`, `MERIDIAN_GITHUB_DISPATCH` | S7, D3 |
| Budget: $25 for Nov 17 – Dec 7, alerts at 50, 90 and 100%, counted before credits | `budget` | C7 |
| An uptime check on `/health` from 3 US regions every 5 minutes, and 5 alerts ([Alerts](#alerts-s6)) | `service_alerts` | S6 |
| A dashboard whose top row is the SLO and cost panel, the endpoint's 2 SLOs, and 4 log-based metrics from the D5 lines ([Dashboard](#dashboard-slos-and-cost-s6)) | `slo`, `cost` | S6 |
| A guard: on the team's project, nothing is deleted or replaced by accident ([Tear it down](#tear-it-down-after-grading)) | | Aniket's review |
| Access for the people who deploy and run the refresh job by hand: that job, not the project ([Add or remove a person](#add-or-remove-a-person)) | `refresh_developers` in `terraform.tfvars`, so no email is in git | S4 |

**What each account can do.** Every grant is on the one resource it's needed on, except two that Google grants only per project: the job's metrics writer and the people's log reader:

| Account | Can |
|---|---|
| `meridian-ci` (`model.yml`) | push models to `meridian-models` |
| `meridian-deploy` (`deploy.yml`) | read the champion from `meridian-models`; read `history.pkl` from `[serve_history]`'s folder only (`history/` in the cache bucket, by an IAM condition), so nothing else there and nothing in the block; push to `meridian-images`; deploy revisions and move traffic on `meridian-risk` and `meridian-refresh`, running as their own accounts |
| `meridian-risk` (the service runs as it) | no bucket: its champion and `history.pkl` are baked into its image |
| `meridian-refresh` (the job runs as it) | read and write the three buckets; push models; read the two secrets; write custom metrics |
| `meridian-scheduler` | start `meridian-refresh` |
| The people in `refresh_developers` (S4) | deploy an image to `meridian-refresh`, run it, see and cancel its executions: that job only, never `meridian-risk`. Its deploys run as `meridian-refresh`. Push to `meridian-images`; read the cache bucket, never the block; read the project's logs; add the two secrets' values without being able to read them |

## For `deploy.yml` and the images

GitHub gets tokens for `meridian-ci` and `meridian-deploy` with no key, because the university's organization blocks service-account keys. It gets them **only on this repository's `main`**; a run on a branch can't sign in.

A workflow can't run `terraform output` before it signs in, so it reads the names from `deploy.toml`:

```yaml
- id: cfg
  run: |
    echo "provider=$(python3 Model-Deployment/deploy_config.py --get derived.workload_identity_provider)" >> "$GITHUB_OUTPUT"
    echo "account=$(python3 Model-Deployment/deploy_config.py --get derived.service_accounts.deploy)" >> "$GITHUB_OUTPUT"
    echo "image=$(python3 Model-Deployment/deploy_config.py --get derived.images.risk)" >> "$GITHUB_OUTPUT"
- uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: ${{ steps.cfg.outputs.provider }}
    service_account: ${{ steps.cfg.outputs.account }}
```

So that Terraform and the deploys never undo each other:
- **Deploys change the image and the traffic only.** Use `gcloud run deploy --image … --no-traffic --tag …`, then `gcloud run services update-traffic`. The instances, CPU, memory, concurrency and account live here and in `deploy.toml`, and the next `apply` puts them back if a deploy changes them.
- **Each container's settings.** Terraform sets `MERIDIAN_DEPLOY_PROJECT` on the service and the job when it creates them. Inside a container, `deploy_config.load()` reads it, so a fresh project gets its own bucket names. Any other variable a deploy adds is left alone.
- **The service listens on port 8080.**
- **The job, by hand (S4).** `gcloud run jobs update meridian-refresh --image … --region us-east1`, then `gcloud run jobs execute meridian-refresh --region us-east1 --wait`.
  - Add variables with `--update-env-vars` or `--update-secrets`, never `--set-env-vars`: it drops `MERIDIAN_DEPLOY_PROJECT`, and Terraform doesn't put it back.
  - No `--args` or `--command`: the image's entrypoint and `job.py`'s defaults need none, and the next `apply` would take them off.
  - CPU, memory, timeout and retries are `deploy.toml` `[jobs.refresh]`. Mounting the cache bucket is a change here. Both go through an `apply`.

## Run it

You need:
- Terraform 1.7 or later;
- `gcloud`;
- Python 3.11 or later, which reads `deploy.toml` (or `pip install tomli` on an older Python).

**Cloud Shell** has `gcloud` and Python, but since June 20, 2026 it no longer includes Terraform. Install a pinned one into your home directory, which persists between sessions:

```bash
V=1.16.4; mkdir -p ~/bin && cd "$(mktemp -d)"
curl -fsSLO "https://releases.hashicorp.com/terraform/${V}/terraform_${V}_linux_amd64.zip"
curl -fsSLO "https://releases.hashicorp.com/terraform/${V}/terraform_${V}_SHA256SUMS"
grep " terraform_${V}_linux_amd64.zip$" "terraform_${V}_SHA256SUMS" | sha256sum -c -
unzip -o "terraform_${V}_linux_amd64.zip" -d ~/bin && export PATH="$HOME/bin:$PATH" && cd -
```

Sign in once with `gcloud auth application-default login`.

Live commands that cost money go through the project owner or CI (the packets' ground rules). `apply` creates accounts and permissions, so the project owner runs it.

### The team's project (once)

```bash
CFG=Model-Deployment/deploy_config.py
P=$(python3 $CFG --get gcp.project); R=$(python3 $CFG --get gcp.region); S=$(python3 $CFG --get buckets.tfstate)

# The state bucket, made once, before Terraform: private, versioned
gcloud storage buckets create "gs://$S" --project "$P" --location "$R" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$S" --versioning

cd infra/terraform
cp terraform.tfvars.example terraform.tfvars    # the billing account id and the team's emails (both required)
terraform init -backend-config="bucket=$S"
terraform plan                                  # Nov 18: 4 to import, 46 to add (+1 per alert email), 1 to change, 0 to destroy
terraform apply                                 # Nov 19: 4 imported, 46 added, 1 changed, 0 destroyed; a plan after it: no changes
```

**Phase 2's resources are taken over on `deploy.toml`'s own project** (`imports.tf`):
- `meridian-models`;
- `meridian-ci`;
- the Workload Identity pool and provider.

They keep the settings they already have, so CI's registry push keeps working through the change. The plan's one change is Terraform's own `deletion_policy` on `meridian-models`, going from `"DELETE"` to `"ABANDON"`; nothing changes in GCP.

A plain `apply` makes all of it at once, and nothing costs money until S2–S4 ship:
- the service and the job start on Google's placeholder images;
- the schedule is paused.

To follow the packet's days instead, apply in stages:

```bash
# 1. buckets, registry repos, accounts
terraform apply -target=google_project_service.api -target=google_storage_bucket.data \
  -target=google_artifact_registry_repository.models -target=google_artifact_registry_repository.images \
  -target=google_service_account.sa
# 2. deploy's identity (sign-in, image push, the champion's read) and the budget
terraform apply -target=google_service_account_iam_member.github \
  -target=google_artifact_registry_repository_iam_member.images_deploy \
  -target=google_artifact_registry_repository_iam_member.models -target=google_billing_budget.project
# 3. Cloud Run, Scheduler and the rest
terraform apply
```

To unpause the schedule once S4's image is in, set `schedule.paused = false` in `deploy.toml` and apply.

### A fresh project

`bootstrap.sh <project-id>` (S8) does it:
1. enables `serviceusage` and `cloudresourcemanager`;
2. makes the state bucket the same way;
3. runs `terraform init` and `terraform apply -var project_id=<project-id>`.

Nothing is imported there.

One catch: a deleted Workload Identity pool keeps its id for 30 days. To rebuild a torn-down project within that time, either use a new project, or run `gcloud iam workload-identity-pools undelete github --location=global --project <project-id>` and import the pool.

### If the public access is refused

If the organization blocks `allUsers` (domain-restricted sharing), `apply` fails on `google_cloud_run_v2_service_iam_member.public`. The fix is to set `invoker_iam_disabled = true` on the service and remove that resource, which is Cloud Run's documented way to make a service public under that policy.

It isn't expected to fail here:
- on Nov 10, the organization allowed members from outside it;
- on Nov 12, a personal Google account was added to the project.

### Add a secret's value

Once, by a person, so it never lands in git or in Terraform's state:

```bash
printf %s "$MERIDIAN_SLACK_WEBHOOK" | gcloud secrets versions add MERIDIAN_SLACK_WEBHOOK --data-file=- --project "$P"
```

The people in `refresh_developers` can add the job's two values themselves, from their own machine: they can add a version but never read one.

### Add or remove a person

The people who deploy and run the refresh job by hand are listed in `terraform.tfvars`, which git ignores:

```hcl
refresh_developers = ["teammate@example.com"]
```

Then `terraform apply`. To remove someone, delete their email and `apply`: only their grants go, and the guard doesn't stop it. A fresh project has nobody on the list.

### Tear it down (after grading)

**On the team's project, a run stops before deleting anything** (`guard.tf`, from Aniket's review) if it would:
- destroy it, even with `-target`;
- replace CI's sign-in, its account, the registry or a bucket.

**No run can use another project's state** (`deploy_config.py`). A fresh project's run left on the team's state stops before it plans anything, even with `-target`.

**Ordinary changes go through.**
- Deleting a resource from the code fails the plan until it's also taken out of `guard.tf`'s list.
- Dropping CI's key from a list fails the plan too, since the guard names it.
- An `apply -target` skips the guard. Before you type yes, read its plan for "must be replaced".

To tear the team's project down on purpose:

```bash
terraform apply -var allow_team_destroy=true   # 1 change: the guard steps aside
terraform destroy
```

`terraform destroy -var allow_team_destroy=true` alone does nothing, because the guard keeps the setting from the last apply. The next plain `apply` turns it back on.

That removes everything except `meridian-models`. That repository is released from Terraform's management, not deleted (`deletion_policy = "ABANDON"`), so phase 2's model versions stay. CI's sign-in goes with the rest.

**Two cases need a different route:** replacing a guarded resource on purpose, or the apply above being refused because a guarded replacement is already pending. In either case, run `terraform state rm terraform_data.team_guard`, then `terraform apply`. The change goes through, and the guard is created again, last.

The guard stops accidents, not intent: deleting `guard.tf`, or `terraform state rm` or `taint` on the guard, gets past it. It also protects only runs from a checkout that has `guard.tf`.

A fresh project tears down with no extra step. Its run (S8's `bootstrap.sh`, still to come) must start Terraform on that project's own state bucket with `terraform init -reconfigure`, never `-migrate-state`, which would copy the team's state.

## Alerts (S6)

`monitoring.tf` checks `/health` from 3 US regions every 5 minutes and pages on PLAN §5's service rules. Every threshold from the plan is in `deploy.toml` `[service_alerts]`; the rest are Google's usual settings, such as the uptime alert's 2 regions.

| Alert | Fires when |
|---|---|
| `meridian-risk: /health failing` | `/health` fails from 2 or more regions |
| `meridian-risk: 5xx above 1% of requests` | over the last 10 minutes |
| `meridian-risk: p95 latency above 2 s` | over the last 10 minutes; container start-up isn't counted |
| `meridian-refresh failed` | an execution of the job fails |
| `Drift found: retrain due` | the job's D5 line has `"event": "drift"` and `"retrain": true` (a log-based alert) |

- **An alert closes within 30 minutes** once its data stops (missing data counts as resolved), so the next failure pages again. Google's default would keep it open 7 days.
- **The 5xx and p95 alerts count the uptime check's own `/health` calls too:** Cloud Run's metrics can't tell paths apart. They also count revisions with no traffic, so a smoke test that fails on one (`deploy.yml`) can page.
- **Cost: $0.**
  - The uptime check runs about 26,000 times a month, and Google gives each project 1 million.
  - Google charges nothing for alerting until at least Sept 1, 2027. After that it's about $0.35 a month per alert, except the uptime alert.
- **Cold start** (≤ 15 s, PLAN §6) is a chart on the dashboard, not an alert. The uptime check keeps an instance warm, so cold starts are rare: the 15 s bar needs a deliberate test.
- **Alerts never retrain:** that's the refresh job's decision (S4, S5).

**Who hears them.** Until one of these is set, the alerts only open incidents in the console, and every plan warns.
- the team's emails, from `alert_emails` in `terraform.tfvars`;
- **Slack**, once a person connects it. Google does this only in the console:
  1. Monitoring → Alerting → **Edit notification channels** → Slack → **Add new**.
  2. Sign in to the team's Slack, pick the channel, and give it a display name.
  3. Set `slack_channel = "<that display name>"` in `terraform.tfvars` and apply.

  On the team's project that's `#meridian-alerts`, display name `Meridian Slack`, connected Nov 24. The console's **Add new** opens Slack in a pop-up, so a browser that blocks pop-ups shows "Unable to open the Slack app".

## Dashboard, SLOs and cost (S6)

`dashboard.tf` makes one dashboard (`terraform output dashboard_url`). Its top row is the **SLO and cost panel** the expo asks for (PLAN §6 and §16), over the last 7 days:

| Tile | What it shows |
|---|---|
| Error budget left | SLO `availability`: 99% of requests aren't 5xx. 100% means untouched; below 0% means the SLO is missed |
| Latency budget left | SLO `latency`: 95% of requests within 2 s |
| Cost per 1,000 scores | Cloud Run's list price for the service's instance time and requests, over the scores it answered |
| Scores answered | from the service's D5 lines |

- **The bars are the plan's.** The error budget is the 5xx alert's 1% (`[slo] availability_goal`, which a test keeps at 1 − `[service_alerts] error_rate`). The latency budget is the p95 bar. The window is `[slo] window_days`.
- **Below the panel:**
  - the budgets and the cost over time;
  - requests by response class, and the 5xx share against the alert's 1%;
  - latency p50 and p95 against 2 s, and cold starts against PLAN §6's 15 s;
  - instances against the max of 2, scores per hour by model version (a canary shows two), and `/health` by region;
  - refresh-job runs by outcome, drift checks by verdict, and billable seconds of the service and the job;
  - the incidents from the 5 alerts.
- **Cost per 1,000 scores** is `1000 × (billable instance seconds × (CPU × vCPU price + GiB × memory price) + requests × request price) ÷ scores`.
  - The prices are `deploy.toml` `[cost]`: us-east1, request-based billing, from Google's pricing page on Nov 24.
  - It's before the free tier, which makes the real bill $0, so it shows what the scores would cost at scale.
  - It counts the uptime check's requests, which cost money but aren't scores.
  - It shows no data until the service answers scores: the placeholder image writes no D5 lines.
- **4 log-based metrics** read the D5 lines:
  - `meridian_scores`: answered scores, by model version;
  - `meridian_score_latency`: the service's own time per score;
  - `meridian_refresh_runs`: the job's runs, by outcome (ok, nothing new, failed);
  - `meridian_drift_checks`: S5's drift verdicts, by retrain.
- **Both SLOs count every request,** the uptime check's `/health` included, as the alerts do.
- **Cost: $0.** Google doesn't bill dashboards or SLOs. The log-based metrics are custom metrics of a few MiB a month, within the 150 MiB each billing account gets free.

## Airflow (the hosted pipeline)

`airflow.tf`, from `deploy.toml [airflow]`: Data-Pipeline's docker compose on one VM (`e2-standard-2`, a 50 GB data disk), for the full product pipeline (every dataset) and the weekly rebuild. Spark's daily update stays with the refresh job. The full block goes under `[airflow] publish_prefix` in the block bucket, the only place the VM can write, so it never moves the job's `latest.json`.

- **When it runs:** an instance schedule starts it at `window_start` and stops it at `window_stop` (UTC), around the DAGs' schedules in `Data-Pipeline/settings.toml` (a test keeps them inside). The data disk keeps the clone, the caches and `published/` between windows, with a daily snapshot kept `snapshot_days`.
- **No public IP:** outbound goes through Cloud NAT; inbound is SSH from Google's IAP range only. The UI and the API listen on the VM's localhost.
- **At each start** (`airflow-startup.sh.tftpl`): mount the data disk, install Docker once, check out `[airflow] ref`, write `Data-Pipeline/.env` from Secret Manager, `docker compose up`, unpause the DAGs.

Before the first start, a person adds the two passwords (the VM won't start Airflow without them):

```bash
printf %s "$ADMIN_PASSWORD" | gcloud secrets versions add MERIDIAN_AIRFLOW_ADMIN_PASSWORD --data-file=-
printf %s "$DB_PASSWORD" | gcloud secrets versions add MERIDIAN_AIRFLOW_DB_PASSWORD --data-file=-
```

Open the UI (people on `airflow_users` in `terraform.tfvars`), while the VM is on, then browse http://localhost:8080:

```bash
gcloud compute ssh meridian-airflow --zone us-east1-b --tunnel-through-iap -- -L 8080:localhost:8080
```

Start it outside the window: `gcloud compute instances start meridian-airflow --zone us-east1-b` (the schedule still stops it at `window_stop`).

Not yet: pushes don't trigger it (`trigger-update.yml` would need Airflow's API on a public address), and the DAG's publish writes `published/` on the VM; copying the full block to `publish_prefix` is the next step.

## Cost

- **Idle costs $0:**
  - the service is billed only while a request runs (`cpu_idle`), so the instance the uptime check keeps warm costs nothing;
  - the schedule is paused until S4;
  - the buckets and the image repo start empty, and keep no soft-deleted copies;
  - a secret with no value isn't billed.
- **The bar** is under $5 for the phase (PLAN §6).
- **The Airflow VM is the one thing billed while idle.** Running ~4.25 h a day: compute ~$8.50 a month (e2-standard-2 at ~$0.067/h), plus its disks ~$3 (20 + 50 GB standard), snapshots ~$1 and NAT under $1: **about $12 a month, ~$5 for the rest of the phase**. Stopped, only the disks and snapshots bill. It was added after the $5 bar was set (Aniket signed off Nov 25).
- **The alerts and the uptime check** cost nothing ([Alerts](#alerts-s6)), and neither do the dashboard, the SLOs and the log-based metrics ([Dashboard](#dashboard-slos-and-cost-s6)).
- **The budget** covers Nov 17 – Dec 7, not a calendar month.
  - **When:** at $12.50, $22.50 and $25 of use.
  - **Who:** the billing account's administrators and users, plus the team's emails from `terraform.tfvars`.
  - **Before credits:** with credits counted, the bill stays at $0 and no alert would ever fire.

## Outputs

`terraform output` gives:
- `risk_url`, for PLAN §9's `curl`;
- `dashboard_url`, S6's dashboard;
- `workload_identity_provider`, `service_accounts` and `project_number`, which should match `deploy.toml`;
- `images_repo`, `buckets` and `refresh_job`.
