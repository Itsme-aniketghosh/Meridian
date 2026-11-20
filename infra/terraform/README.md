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
| Secret Manager: the names only, stored in us-east1 | `secrets`: `meridian-slack-webhook`, `meridian-github-dispatch` | S7, D3 |
| Budget: $25 for Nov 17 – Dec 7, alerts at 50, 90 and 100%, counted before credits | `budget` | C7 |

Cloud Monitoring (S6) is added in `monitoring.tf`, with Vishwa.

**What each account can do.** Every grant is on the one resource it's needed on, except the metrics writer:

| Account | Can |
|---|---|
| `meridian-ci` (`model.yml`) | push models to `meridian-models` |
| `meridian-deploy` (`deploy.yml`) | read the champion from `meridian-models`; push to `meridian-images`; deploy revisions and move traffic on `meridian-risk` and `meridian-refresh`, running as their own accounts |
| `meridian-risk` (the service runs as it) | read the block bucket |
| `meridian-refresh` (the job runs as it) | read and write the three buckets; push models; read the two secrets; write custom metrics |
| `meridian-scheduler` | start `meridian-refresh` |

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

A plain `apply` makes all of it at once, and nothing runs or bills until S2–S4 ship:
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
printf %s "$MERIDIAN_SLACK_WEBHOOK" | gcloud secrets versions add meridian-slack-webhook --data-file=- --project "$P"
```

### Tear it down (after grading)

`terraform destroy` removes everything except `meridian-models`. That repository is released from Terraform's management, not deleted (`deletion_policy = "ABANDON"`), so phase 2's model versions stay. CI's sign-in goes with the rest.

## Cost

- **Idle costs $0:**
  - the service scales to zero;
  - the schedule is paused until S4;
  - the buckets and the image repo start empty, and keep no soft-deleted copies;
  - a secret with no value isn't billed.
- **The bar** is under $5 for the phase (PLAN §6).
- **The budget** covers Nov 17 – Dec 7, not a calendar month.
  - **When:** at $12.50, $22.50 and $25 of use.
  - **Who:** the billing account's administrators and users, plus the team's emails from `terraform.tfvars`.
  - **Before credits:** with credits counted, the bill stays at $0 and no alert would ever fire.

## Outputs

`terraform output` gives:
- `risk_url`, for PLAN §9's `curl`;
- `workload_identity_provider`, `service_accounts` and `project_number`, which should match `deploy.toml`;
- `images_repo`, `buckets` and `refresh_job`.
