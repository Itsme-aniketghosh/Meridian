# Phase 3: deployment

Phase 1 built the data. Phase 2 built the defect-risk model and the product around it, all running locally and in GitHub Actions, with one GCP piece: the model registry. Phase 3 puts it on Google Cloud, keeps it running on its own, and watches it. It follows the course's deployment guideline section by section (§13). Most of what the guideline asks for already exists and only has to be wired up in the cloud: this plan says which.

## 1. What we have to start from

| Piece | Where | State |
|---|---|---|
| The champion model | Artifact Registry `meridian-models` (us-east1), version `85b4684…`, `model.joblib` md5 `2a0c9cc6…` | Pushed by CI on main, Workload Identity, no key |
| Turning a push into the model's features | `Data-Pipeline/meridian/p9.py` | Same code path as training, so features can't drift from the table |
| Retraining that only replaces on a real gain | `Model-Pipeline/model/refresh.py` (`--every 24`) | Built, never run in the cloud |
| Gate, bias, registry, rollback | `model/gate.py`, `model/bias.py`, `model/registry.py` | In CI on every push |
| Model image | `Model-Pipeline/Dockerfile`, `model/run_all.py` | Built in CI |
| Alerts | `Data-Pipeline/meridian/alert.py`, `MERIDIAN_SLACK_WEBHOOK` | Live in the team's Slack since Nov 15 |
| Data pipeline | Two Airflow DAGs, local Docker | Never run in the cloud |
| Views and API | `meridian.views`, standard library, read-only | Local only |

What doesn't exist yet: a serving endpoint, cloud infrastructure as code, a deploy workflow, a scheduled data refresh in the cloud, drift and decay monitoring, and replication docs.

## 2. Cloud or edge

**Cloud, on Google Cloud Platform.** Nothing about the use case is edge: the model scores pushes to a repository, next to CI, with the history of that repository. §4 of the guideline (edge) doesn't apply, and the report says so in one line.

| Service | What runs on it | Why this one |
|---|---|---|
| **Cloud Run (service)** `meridian-risk` | The model's inference endpoint | Scales to zero, so idle costs $0. Runs the container we already build. The model is a 5 MB scikit-learn forest: no GPU, no Vertex endpoint needed |
| Cloud Run (service) `meridian-views` | The product's views and API | Same container style. Stretch (§10 cut order) |
| **Cloud Run job** `meridian-refresh` + Cloud Scheduler | Daily: new Spark commits → new block → drift and decay check → retrain when a threshold is crossed | A job runs to completion and costs only while it runs |
| Artifact Registry | Model versions (exists) and the serving images | Already holds the champion |
| Cloud Storage | The block and monitoring reports, private | The block has real names: never public |
| Cloud Logging + Cloud Monitoring | Request logs, uptime check, alert policies, a dashboard | Built in, free tier covers us |
| GitHub Actions | Build, deploy, smoke test, rollback | Where CI already is |
| Terraform | Everything above | Deployment isn't a manual process |

Ruled out: **Vertex AI endpoint** (always on, about $70+ a month on credits, for a 5 MB model), **GKE** (a cluster to run one container), **Cloud Composer** ($300–400 a month), **Cloud Functions** (we already ship a container; Cloud Run runs it as is).

## 3. What we build

```mermaid
flowchart LR
  gh["GitHub: main"] -->|model.yml: train, gate, bias| reg[("Artifact Registry<br/>champion")]
  reg -->|deploy.yml: pull champion, build, smoke test, shift traffic| risk["Cloud Run<br/>meridian-risk<br/>POST /score"]
  push["a push / a curl"] --> risk
  risk --> logs[("Cloud Logging<br/>one line per score")]
  sched["Cloud Scheduler<br/>daily"] --> job["Cloud Run job<br/>meridian-refresh"]
  job -->|collect new Spark commits| blk[("GCS: block")]
  job -->|drift + decay check| mon[("GCS: monitoring report")]
  job -->|threshold crossed| retrain["model.refresh:<br/>retrain, gate, registry"]
  retrain --> reg
  retrain -->|repository_dispatch| gh
  logs --> cm["Cloud Monitoring<br/>uptime, 5xx, latency, dashboard"]
  mon --> cm
  cm -->|alert policy| slack["Slack"]
  job --> slack
  risk -. stretch .- views["Cloud Run<br/>meridian-views"]
```

### Steps

| Step | What | Output | Owner |
|---|---|---|---|
| **S1** | Terraform for the project: APIs, buckets, registry repos, service accounts, Workload Identity for this repo, Cloud Run services and job, Scheduler, budget | `infra/terraform/`, `terraform apply` from zero | Rochan |
| **S2** | The inference service: `GET /health`, `POST /score` (a commit's numstat, subject and author, or a known Spark SHA) → risk, its percentile on val, the P9 verdict, the model version. Loads the champion named by the registry pointer at start, checks its md5 | `Deploy-Pipeline/serve/`, its Dockerfile, tests | Rachna |
| **S3** | Deploy workflow: on a new champion (after `model.yml` on main, a `repository_dispatch`, or by hand) pull the champion, build and push the image, deploy a new revision with no traffic, smoke test it, then move traffic. A failed smoke test leaves traffic on the old revision and alerts | `.github/workflows/deploy.yml` | Chaitali |
| **S4** | The refresh job: the data pipeline's update for Spark only, in a container, writing the new block to GCS. Then `model.refresh` when S5 says so, pushing to the registry | `Deploy-Pipeline/refresh/`, the job image | Utkarsh |
| **S5** | Monitoring: data drift (the new commits' features against train, Evidently) and decay (PR-AUC on the newest window whose labels have matured, against lines × files). Writes a report, sets custom metrics, decides "retrain" by the thresholds in §5 | `Deploy-Pipeline/monitor/`, tests on a planted drift | Vishwa |
| **S6** | Cloud Monitoring: uptime check on `/health`, alert policies (5xx rate, p95 latency, job failure, drift flag), a dashboard, log-based metrics from S2's log lines. All in Terraform | `infra/terraform/monitoring.tf` | Vishwa, Rochan |
| **S7** | Notifications: retrain triggered, new model deployed, deploy failed and rolled back, drift found. Through `alert.py` | Messages in Slack | Chaitali |
| **S8** | Replication: `DEPLOY.md` and one script from a fresh machine to a working endpoint. Then the video | `Deploy-Pipeline/DEPLOY.md`, `bootstrap.sh`, the video | Aniket |
| S9 | Views on Cloud Run, read-only, from the GCS block | `meridian-views` | Vishwa (stretch) |

## 4. The endpoint

```
POST /score
{"repo": "apache/spark", "sha": "<a commit in the block>"}                       # a known commit
{"repo": "apache/spark", "numstat": [[12, 3, "core/src/.../Foo.scala"], ...],
 "subject": "[SPARK-12345] Fix ...", "author_email_hash": "..."}                 # a new push

200 {"risk": 0.31, "percentile_val": 0.92, "p9": {"rule": "lines_x_files", "alert": false},
     "model": {"version": "85b4684…", "md5": "2a0c9cc6…"}, "features_version": "9c0ab220…"}
```

- **Features through `p9.py`**, from the base block, exactly as the push check computes them. No second implementation.
- **No people out.** The block has real names, so the response carries only scores and versions, and an email comes in hashed. A test checks every response field.
- **Logs:** one structured line per call: latency, model version, the feature values, the risk. Never the email. S5 reads these as the "served" distribution.
- **Auth:** public, so graders can call it, with max 2 instances and a request size cap. No names out means nothing to protect but cost.
- **Model updates** need no code change: a new champion is a new revision.

## 5. Monitoring and retraining

Labels arrive late: a commit counts as bug-inducing only once a fix blames it, up to 365 days later (decision 1). So decay is measured only on windows whose labels have matured, and drift is the early signal.

| Signal | How | Threshold to retrain | Where it's from |
|---|---|---|---|
| **Data drift** | Each new block: the last 30 days' commits against train, per feature. Evidently's drift test and PSI | PSI ≥ 0.2 on any of the top-5 SHAP features (`la`, `nf`, `prior_bug_fixes`, `nd`, `ld`), or Evidently's dataset drift (≥ 50% of features drifted) | The features S2 serves and the block's new commits |
| **Decay** | The newest calendar quarter whose 365-day window has closed and has ≥ 30 positives: model PR-AUC against lines × files, paired bootstrap | The paired interval's lower bound drops below 0 (the model no longer beats the rule), or PR-AUC falls more than 0.05 under the last measured window | The block's labels |
| **Service** | Cloud Monitoring | 5xx > 1% over 10 min, p95 > 2 s, uptime check failing | S2's logs |

When drift or decay crosses its threshold, the job runs `model.refresh`: roll the split forward, rebuild the features, retrain all three models, gate, bias, and the registry. **The registry replaces the champion only on a gain above 2 seed-noise SDs on the same fresh val rows.** If it does, the job sends `repository_dispatch` and S3 deploys it. If not, production keeps the old model and Slack says why. Service alerts page Slack and never retrain.

**The demo needs a real trigger.** Drift on today's data is real: the 7,238 commits from 2025–26 sit outside every split. S5 runs on them first, and the plan records the result before anything is tuned. If nothing drifts, the demo uses `monitor --simulate`: a planted shift (every commit 10× larger) that must cross the drift threshold, retrain, and get "not better, kept the old model". That shows both branches of the loop on camera.

## 6. Pass bars, written before the run

| What | Bar | Measured by |
|---|---|---|
| From zero | `bootstrap.sh` on a fresh project and a fresh machine gives a working `/score` with no manual step but the login and the project id | the video, and one teammate who wasn't involved |
| Endpoint correct | `/score` gives the same risk as `p9.py` locally for 20 val commits, to 1e-9 | `serve` tests |
| No names | No response or log line holds an email, a name or a person id, on 1,000 scored commits | a test |
| Latency | p95 ≤ 2 s warm, cold start ≤ 15 s | Cloud Monitoring |
| Deploy | A new champion is serving within 15 minutes of `model.yml` finishing on main, with no hands | `deploy.yml` timestamps |
| Rollback | A broken image (planted) never takes traffic; the old revision keeps serving | a planted failure on a branch |
| Drift | A planted shift crosses the threshold, a clean week doesn't | `monitor` tests |
| Retrain | Drift → retrain → "not better" keeps the old model; a better model gets deployed | a run of each |
| Alerts | Slack hears: retrain triggered, new model deployed, deploy rolled back, drift found | screenshots |
| Cost | Under $5 for the phase; budget alerts at 50, 90 and 100% of $25 | the billing page |

## 7. Dependency graph

```mermaid
flowchart TD
  S1["S1: Terraform, project · Rochan"] --> S2d["S2 deployed · Rachna"]
  S2["S2: serve, tests · Rachna"] --> S2d
  S2d --> S3["S3: deploy.yml · Chaitali"]
  S1 --> S4["S4: refresh job · Utkarsh"]
  S5["S5: drift + decay · Vishwa"] --> S4
  S2d --> S6["S6: Cloud Monitoring · Vishwa, Rochan"]
  S3 --> S7["S7: notifications · Chaitali"]
  S4 --> S7
  S3 --> S8["S8: DEPLOY.md, bootstrap, video · Aniket"]
  S4 --> S8
  S6 --> S8
  S7 --> S8
  S2d -. stretch .-> S9["S9: views · Vishwa"]
```

Critical path: S1 → S2 deployed → S3 → S8. S2 and S5 start on day 1 with no cloud at all: S2 runs locally against the block, S5 against the 2025–26 commits.

## 8. Roles

| Who | Work | Days |
|---|---|---:|
| Rochan | S1 Terraform (the project's owner, Workload Identity already set up there) 2 · S6 with Vishwa 0.5 · budget and billing checks 0.5 | 3 |
| Rachna | S2 the endpoint, its image and tests 2.5 · the "same as p9.py" check 0.5 | 3 |
| Chaitali | S3 deploy, smoke test, rollback 2 · S7 notifications 0.5 · the planted broken image 0.5 | 3 |
| Utkarsh | S4 the refresh job, Spark-only update in a container, block to GCS 2.5 · retrain step 0.5 | 3 |
| Vishwa | S5 drift and decay 2 · S6 monitoring 1 · S9 views (stretch) | 3 |
| Aniket | Reviews and merges · integration · S8 `DEPLOY.md`, `bootstrap.sh`, the fresh-machine run, the video · the report | end of the graph |

Every live GCP command that costs money goes through the project owner (Rochan) or CI, as in phase 2.

## 9. Running it

```
git clone https://github.com/Itsme-aniketghosh/Meridian && cd Meridian/Deploy-Pipeline
./bootstrap.sh <project-id>          # gcloud login, enable APIs, terraform apply, first deploy
curl -s -X POST "$(terraform -chdir=../infra/terraform output -raw risk_url)/score" \
     -H 'Content-Type: application/json' -d '{"repo": "apache/spark", "sha": "<sha>"}'
./bootstrap.sh <project-id> --destroy   # tears it all down
```

A fresh machine needs only a browser: Cloud Shell has `gcloud`, `terraform`, `docker` and `git`. `DEPLOY.md` also lists the versions for a laptop.

## 10. Risks and cut order

- **Labels lag a year.** Decay can't be seen in days. Drift is the trigger the demo can show; decay is measured on matured windows and stated as such.
- **The data job is heavy.** Spark's history takes most of the pipeline's 16 minutes. The job keeps its clone in GCS between runs, so a daily run reads only new commits.
- **The block has real names.** Private bucket, no names in responses or logs, tests on both.
- **Credits.** Everything scales to zero, the budget alerts are in Terraform, and `--destroy` takes it all down after grading.

Cut from the top if behind: C8 docs → S9 views (and with it C4) → the dashboard (keep the alert policies) → the Spark-only cloud data job (run S4 in GitHub Actions on a schedule instead). Never cut: the endpoint, automated deploy, rollback, drift → retrain → redeploy, the video.

## 11. Schedule

Due date: **to confirm**. With a two-week window:

| Days | Rochan | Rachna | Chaitali | Utkarsh | Vishwa |
|---|---|---|---|---|---|
| 1–2 | S1 Terraform plan, apply in the project | S2 endpoint local, tests | S3 workflow on a branch (dry run) | S4 job image, local run | S5 drift on 2025–26 commits, thresholds signed |
| 3–4 | S1 Workload Identity for deploy, budget | S2 deployed, "same as p9.py" | S3 deploy + smoke + traffic | S4 in Cloud Run job, block in GCS | S5 decay, `--simulate` |
| 5–6 | S6 with Vishwa | latency, cold start | rollback with a broken image, S7 | retrain step, `repository_dispatch` | S6 uptime, alerts, dashboard |
| 7 | **Integration: one drift → retrain → redeploy end to end (Aniket)** | | | | |
| 8–9 | fresh-project run | fixes | fixes | fixes | S9 views (stretch) |
| 10 | **Video on a fresh machine, report** | | | | |

## 12. What phase 3 outputs

| Output | Where |
|---|---|
| Infrastructure | `infra/terraform/` |
| Endpoint | `Deploy-Pipeline/serve/`, Cloud Run `meridian-risk` |
| Deploy workflow | `.github/workflows/deploy.yml` |
| Refresh job, monitoring | `Deploy-Pipeline/refresh/`, `Deploy-Pipeline/monitor/` |
| Replication | `Deploy-Pipeline/DEPLOY.md`, `bootstrap.sh` |
| Report and video | `Deploy-Pipeline/report/REPORT.md`, the video link |

## 13. Guideline coverage

| Guideline | Item | Step | Evidence |
|---|---|---|---|
| §2 | Cloud or edge, provider and service | §2 | GCP, Cloud Run service and job |
| §3.1 | Deployment service and its configuration | S1, S2 | Terraform: instances, memory, concurrency, traffic |
| §3.2 | Automated: pull the latest model, deploy or update, monitor and log | S3, S6 | `deploy.yml`; Cloud Logging and Monitoring |
| §3.3 | Repository connected, redeploy on a new model version | S3 | `deploy.yml` on `model.yml` success and `repository_dispatch` |
| §3.4 | Replication: setup, scripts, verifying the endpoint | S8 | `DEPLOY.md`, `bootstrap.sh`, the `curl` above |
| §4 | Edge | n/a | Cloud only, said in the report |
| §5.1 | Monitor decay and data shift over time | S5, S6 | Monitoring report per block, dashboard |
| §5.2 | Detect data shift against training data | S5 | Evidently, PSI on the top-5 features |
| §5.3 | Thresholds that trigger retraining | §5 | Written before the run |
| §5.4 | Retrain automatically: new data, train, validate, deploy only if better | S4, `model.refresh`, S3 | The registry's 2-SD rule; else keep |
| §5.5 | Notify on retraining and new deployments | S7 | Slack screenshots |
| §6.1–6.5 | Scripts, repo link, monitoring code, environment config, logs | all | The folders in §12 |
| §7 | Video, fresh environment, 5–10 minutes | S8 | Cloud Shell from zero to `/score`, then drift → retrain → redeploy |
| §8 | Evaluation: automated, documented, CI/CD, logs, retraining, video | all | §6's pass bars |

## 14. Carried over from phase 2

Open at the end of phase 2 (`Model-Pipeline/GAPS.md`, "After phase 2"). None blocks deployment, and the first three are small. Deploying the views (S9) is what lets the last ones move at all: the auditor and the walkers only learn from real users.

| # | Open point | What we do | Owner | When |
|---|---|---|---|---|
| C1 | **Graph walkers' texts fall back** 19 of 20 times: each names a team its facts don't hold (gap 27) | Put the teams the walk visits into its facts, then rerun the walk tasks. Needs new live LLM calls (about 20), so through the GCP owner | Utkarsh | Week 1, beside S4 |
| C2 | **The gate rejects 14 correct line lists** written without their path, "formsets.py at lines 9, 344 and 351" (gap 23) | Teach the gate "path at lines a, b", with planted-error tests like #122's; re-gate the cache at $0. The planted-error tables must not change | Vishwa | Week 1, beside S5 |
| C3 | **The packets folder** still holds the contracts, decision logs and report sections, under people's names; code reads three files from it | Move what matters to `docs/model/` and `Model-Pipeline/report/sections/`, the data files and scripts next to the code, update every path and CI's mount; delete the task READMEs | Aniket | Before S8, so `DEPLOY.md` links the final paths |
| C4 | **walk-v1 and the auditor's policies have 0 live matches** | Once the views run on Cloud Run (S9), stall verdicts start the matches. Promotion needs 30, so phase 3 reports the count, not a winner | Rochan | After S9 |
| C5 | **No policy writer** (gap 22) | Stays cut until C4 has matches: every new challenger is another chance at a lucky promotion | Rochan | Not this phase |
| C6 | **Package diffs at 73.2%** against the 90% bar (gap 3) | Learning from owners' edits (decision 15) needs real outcomes, which come only with users. Reported, not worked | Utkarsh | Not this phase |
| C7 | **No shared spend alert** (gap 11) | `budget.tf` in S1 closes it | Rochan | With S1 |
| C8 | **Docs that disagree:** Spark's "Incomplete" ticket count (141 in `03-data.md`, 145 in `05-test.md`); `03-data.md`'s schema sketch vs `schema.sql`; "replay equivalence" in 02 and 05 has no test or result | Settle each from the block or mark it as a design note | Utkarsh | Week 2 |

C1 and C2 change texts the phase 2 report quotes (205 LLM texts, 19 templates; the writer comparison's pass rates), so their PRs also update those numbers in `Model-Pipeline/report/REPORT.md` and say the date they changed.
