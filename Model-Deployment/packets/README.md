# Phase 3 work packets

Each person has one folder here with their steps, dates, checklist and "done when". The plan is [../PLAN.md](../PLAN.md). Due **Mon Dec 7**, code freeze **Wed Dec 2**.

| Who | Packet | Steps |
|---|---|---|
| Rochan | [rochan-infra](rochan-infra/README.md) | S1 Terraform · S6 with Vishwa · C4, C7 · S10 replay matches |
| Rachna | [rachna-serve](rachna-serve/README.md) | S2 the endpoint · S10 retrain speed |
| Chaitali | [chaitali-cicd](chaitali-cicd/README.md) | S3 deploy and rollback · S7 Slack · S10 deploy speed |
| Utkarsh | [utkarsh-refresh](utkarsh-refresh/README.md) | S4 refresh job · C1 walker facts · C8 docs · S10 refresh speed |
| Vishwa | [vishwa-monitor](vishwa-monitor/README.md) | S5 drift and decay · S6 monitoring · C2 gate line lists · S9 views · S10 fallback report |
| Aniket | [aniket-lead](aniket-lead/README.md) | Reviews · integration · C3 packets move · S8 guide, bootstrap, video · report |

## Ground rules

- **Everything in Terraform or a workflow.** No change made by hand in the console. If you clicked it, it isn't deployed.
- **Live GCP runs that cost money** go through Rochan (the project owner) or CI, as in phase 2. Live LLM calls go through Utkarsh.
- **No people out.** The block has real names. No response, log line, report or Slack message carries an email, a name or a person id. Every packet that writes one has a test for it.
- **Bars before runs.** Each step's pass bar is in PLAN §6. Write your measured number next to it, with its source, in your PR.
- **The push check stays LLM-free**, and P9 stays lines × files: the model ranks, it doesn't alert (phase 2 §10).
- **One PR per step**, merged by Aniket. Reruns that cost $0 say so in the PR.
- **Cost:** under $5 for the phase. Anything that bills while idle (Vertex endpoint, GKE, Composer, Cloud SQL, a VM) needs Aniket's OK first.

## Contracts

Fixed on day 1 so nobody waits on anybody's code. A change to a contract is a PR that names everyone it touches.

### D1: the endpoint (Rachna serves it; Chaitali smoke-tests it; Aniket's video calls it)

```
GET  /health  -> 200 {"ok": true, "model": {"version": "85b4684…", "md5": "2a0c9cc6…"}}
POST /score
  {"repo": "apache/spark", "sha": "<a commit in the block>"}
  {"repo": "apache/spark", "numstat": [[added, deleted, "path"], ...], "subject": "...", "author_email_hash": "<sha256>"}
-> 200 {"risk": float, "percentile_val": float, "p9": {"rule": "lines_x_files" | "model", "alert": bool},
        "model": {"version": str, "md5": str}, "features_version": str}
-> 400 {"error": "..."} on a bad body; 422 for a repo the model wasn't trained on
```

### D2: the monitoring report (Vishwa writes it; Utkarsh's job reads `retrain`; Vishwa's dashboard reads the rest)

`gs://<project>-meridian-monitor/reports/<run_id>.json`:

```json
{"run_id": "...", "at": "2026-11-18T03:00:00Z", "n_new": 412,
 "drift": {"psi": {"la": 0.04, "nf": 0.21, ...}, "evidently_share": 0.33, "flag": true, "why": "PSI 0.21 on nf ≥ 0.2"},
 "decay": {"window": "2025Q3", "n": 2900, "positives": 210, "pr_auc": 0.27, "lines_x_files": 0.22,
           "paired": [0.01, 0.08], "flag": false, "why": "..."},
 "retrain": true}
```

### D3: the retrain → deploy hand-off (Utkarsh sends it; Chaitali's workflow receives it)

`repository_dispatch`, `event_type: "champion-updated"`, `client_payload: {"version", "md5", "reason", "report"}`. Sent only when the registry promoted a new champion. "Not better, kept" sends a Slack message, not a dispatch.

### D4: names and config (Rochan owns `deploy.toml`; everyone reads it)

`Model-Deployment/deploy.toml`: project, region `us-east1`, bucket names, service names `meridian-risk`, `meridian-views`, job `meridian-refresh`, image repo, the thresholds in PLAN §5, the schedule. No value is hard-coded anywhere else. Secrets: `MERIDIAN_SLACK_WEBHOOK` in GitHub and Secret Manager; GCP access only through Workload Identity.

### D5: log lines (Rachna and Utkarsh write them; Vishwa's metrics read them)

One JSON object per line on stdout, so Cloud Logging parses it:
`{"severity": "INFO", "event": "score" | "refresh" | "deploy" | "drift" | "retrain", "model_version": str, "latency_ms": int, ...}`.
Never an email, a name or a person id.

## Integration day (Wed Nov 25)

Before it, each of these is merged and green: D1 behind a real URL, `deploy.yml` with a smoke test, the refresh job writing a D2 report, alert policies in Terraform. On the day, Aniket runs the whole loop twice: a planted shift that retrains and deploys, and one that retrains and keeps the old model.
