# infra/terraform: Meridian on Google Cloud (S1)

Everything phase 3 runs on GCP, as code (`Model-Deployment/PLAN.md` §2–3). Every name and setting comes from `Model-Deployment/deploy.toml` (contract D4), read through `deploy_config.py`. Change a name there, never here: `Model-Deployment/tests/test_deploy_config.py` fails if a `.tf` file types one.

## What it makes

| Resource | Name in `deploy.toml` | For |
|---|---|---|
| Cloud Run service, public, 0–2 instances, 1 CPU, 1 GiB | `services.risk`: `meridian-risk` | S2's endpoint (D1), deployed by S3 |
| Cloud Run job, plus a daily Cloud Scheduler trigger at 03:00 UTC, created paused | `jobs.refresh`: `meridian-refresh`, `meridian-refresh-daily` | S4 |
| Three private buckets with uniform access | `buckets`: `<project>-meridian-block`, `-monitor` (D2 reports), `-cache` | S4, S5 |
| Artifact Registry | `registry.models`: `meridian-models` (phase 2's, adopted); `registry.images`: `meridian-images`, Docker, which keeps the newest 3 of each image | the registry; S3 |
| Service accounts | `service_accounts`: `meridian-ci` (phase 2's), `-deploy`, `-risk`, `-refresh`, `-scheduler` | below |
| Workload Identity: GitHub Actions on this repository's main branch only | `workload_identity`: pool `github`, provider `meridian` (phase 2's, adopted) | `model.yml`, `deploy.yml` |
| Secret Manager: the names only | `secrets`: `meridian-slack-webhook`, `meridian-github-dispatch` | S7, D3 |
| Budget: $25, alerts at 50, 90 and 100%, counted before credits | `budget` | C7 |

Cloud Monitoring (S6) is added in `monitoring.tf`, with Vishwa.

**What each account can do.** Every grant is on the one resource it's needed on, except the metrics writer:

| Account | Can |
|---|---|
| `meridian-ci` (`model.yml`) | push models to `meridian-models` |
| `meridian-deploy` (`deploy.yml`) | read the champion from `meridian-models`; push to `meridian-images`; deploy revisions and move traffic on `meridian-risk` and `meridian-refresh`, running as their own accounts |
| `meridian-risk` (the service runs as it) | read the block bucket |
| `meridian-refresh` (the job runs as it) | read and write the three buckets; push models; read the two secrets; write custom metrics |
| `meridian-scheduler` | start `meridian-refresh` |

GitHub gets tokens for `meridian-ci` and `meridian-deploy` only from this repository's `main`, with no key: the university's organization blocks service-account keys. In `deploy.yml`, use:

```yaml
- uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: <terraform output workload_identity_provider>
    service_account: <terraform output -json service_accounts | the "deploy" one>
```

## Run it

You need:
- Terraform 1.7 or later;
- `gcloud`;
- Python 3.11 or later, which reads `deploy.toml`.

Cloud Shell has all three. Sign in once with `gcloud auth application-default login`.

Live commands that cost money go through the project owner or CI (packets ground rules). `apply` creates accounts and permissions, so the project owner runs it.

### The team's project (once)

```bash
CFG=Model-Deployment/deploy_config.py
P=$(python3 $CFG --get gcp.project); R=$(python3 $CFG --get gcp.region); S=$(python3 $CFG --get buckets.tfstate)

# The state bucket, made once, before Terraform: private, versioned
gcloud storage buckets create "gs://$S" --project "$P" --location "$R" --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update "gs://$S" --versioning

cd infra/terraform
cp terraform.tfvars.example terraform.tfvars    # the billing account id; adopt_existing = true
terraform init -backend-config="bucket=$S"
terraform plan                                  # Nov 18: 4 to import, 46 to add, 0 to change, 0 to destroy
terraform apply
```

`adopt_existing = true` imports phase 2's `meridian-models`, `meridian-ci` and the Workload Identity pool and provider with the settings they already have, so `plan` shows them with 0 changes and CI's registry push keeps working through the change.

A plain `apply` makes all of it at once, and nothing runs or bills until S2–S4 ship:
- the service and the job start on Google's placeholder images;
- the schedule is paused.

To follow the packet's days instead, apply in stages:

```bash
terraform apply -target=google_project_service.api -target=google_storage_bucket.data \
  -target=google_artifact_registry_repository.models -target=google_artifact_registry_repository.images \
  -target=google_service_account.sa                                       # buckets, registry repos, accounts
terraform apply -target=google_service_account_iam_member.github -target=google_billing_budget.project   # deploy's identity, budget
terraform apply                                                           # Cloud Run, Scheduler and the rest
```

After a stage, `deploy.yml` and the job replace the images; Terraform never puts its placeholder back (`ignore_changes`). Unpause the schedule by setting `schedule.paused = false` in `deploy.toml` and applying, once S4's image is in.

### A fresh project

`bootstrap.sh <project-id>` (S8) does it:
1. enables `serviceusage` and `cloudresourcemanager`;
2. makes the state bucket the same way;
3. runs `terraform init` and `terraform apply -var project_id=<project-id>`, with `adopt_existing` left false.

### Add a secret's value

Once, by a person, so it never lands in git or in Terraform's state:

```bash
printf %s "$MERIDIAN_SLACK_WEBHOOK" | gcloud secrets versions add meridian-slack-webhook --data-file=- --project "$P"
```

### Tear it down (after grading)

The registry's model versions stay as phase 2's record:

```bash
terraform state rm google_artifact_registry_repository.models
terraform destroy
```

## Cost

- **Idle costs $0:**
  - the service scales to zero;
  - the schedule is paused until S4;
  - the buckets and the image repo start empty;
  - a secret with no value isn't billed.
- **The bar** is under $5 for the phase (PLAN §6).
- **The budget** emails the billing account's admins at $12.50, $22.50 and $25 of use. It counts usage before credits, because with credits counted the bill stays at $0 and no alert would ever fire.

## Outputs

`terraform output` gives:
- `risk_url`, for PLAN §9's `curl`;
- `workload_identity_provider` and `service_accounts`, for `deploy.yml`;
- `images_repo`, `buckets` and `refresh_job`.
