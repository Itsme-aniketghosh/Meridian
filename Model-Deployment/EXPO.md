# ML Expo, Tue Dec 8: the demo, and what's left before it

The expo (8:30–1:30, Google Cambridge) asks for working systems, not slides (PLAN §16). Visitors use the Meridian website, which is served by `meridian-views` on Cloud Run and reads one block. A visitor has a few minutes. They should leave knowing two things: what Meridian does for a team retiring an API, and that it runs and watches itself.

## What's on the table

| Screen | What it is | Where it comes from |
|---|---|---|
| **The website** | Home, Retirements, the migration, My team, Check a push, ticket tools | `Data-Pipeline/site` (Vite + React), served by `python -m meridian.views ... --site site/dist`, data from `/api/*` on the real block `c5118b019d642a9b` |
| **The system, live** | Cloud Monitoring's dashboard (SLO budgets, cost per 1,000 scores, scores per hour by model version), the last deploy's run summary (canary, rollback), Slack | S6 `dashboard.tf`, `deploy.yml`, S7 |
| **The laptop** | The repo, a terminal for `curl /health` and the export file | — |

The website's top line on every page says which block it reads, the snapshot date and coverage. Rows that come from the fixture `acme/shop` carry an "Example data" banner. Say so when a visitor is looking at one.

## The demo: three minutes

Start on Home, repository `django/django`.

1. **Home** (40 s).
   - Read the headline: the share of the code that hasn't changed in a year, measured back from Django's latest commit in the block.
   - Click the busiest area in the treemap. "Start here" lists the files a newcomer should read first.
   - Switch the repository to `apache/spark` to show **Where bugs came from**. Only Spark has bug history, because SZZ ran through its Jira keys. Hover a hotspot for its fix keys.
   - **Who calls out** on a Django-family repo shows the methods still calling the retiring APIs.
   - Click **Export for Claude Code**: one file a coding agent reads first. It holds facts only (no people, no ticket text), and nothing is sent anywhere.
2. **A stalled team** (60 s). Go to Retirements, then `url` in `django-oscar/django-oscar`, then The migration.
   - The team is **Stalled**: no change to its 247 call sites since Dec 2019.
   - Its reasons are ranked in the auditor's order (never notified, orphaned owner, test gap with 28 pieces of evidence): reasons, never probabilities.
   - **Copy note** gives the template a lead would paste.
   - Open the team's list: 28 files, riskiest first.
3. **Score a push** (40 s). Go to Check a push and press **Score it**.
   - The live model answers with a risk and its percentile among validation commits: a ranking.
   - P9 flags on lines × files, not on the model. The model can't reach the 0.80 precision bar, so it never decides an alert.
   - Nothing blocks a merge.
4. **The system, live** (40 s), on the second screen:
   - the dashboard's budgets and cost;
   - the deploy run where the canary took 10% for 3 minutes and moved to 100%;
   - the planted slow image the canary rolled back;
   - Slack's messages.

   Then the drift story: real drift (`prior_bug_fixes`, PSI 0.33) triggers a retrain, and the gate keeps the old model when the new one isn't better.

Close on Home's **What broke, and how we fixed it** board when a visitor asks what went wrong.

### The stalled-team story: settled

The real block has **no `blocked_by` rows**, so it can't show a team waiting on another team's code. Use the two halves:

- **Real (door 2 above):** `django-oscar/django-oscar` on `url`. It's stalled, with ranked reasons and evidence, from block `c5118b01`.
- **Upstream wait, from the fixture, labelled:** Fulfillment waits on Platform's `shared/client.py`. Show it only if asked "can it tell who's blocking whom?", and point at the Example data banner.

### Questions visitors ask

| Question | Answer |
|---|---|
| Does it name people? | No. Teams only, on every page and in the export. A test renders every page on the real block and fails on any name (S9). |
| Why doesn't the model raise the alert? | It ranks pushes well (val PR-AUC 0.41 against a 0.12 base rate) but can't reach 0.80 precision at any useful recall, so P9 alerts on lines × files and the model is shown as a ranking. |
| What does it cost? | About cents a day. It scales to zero, and the uptime check keeps one warm. The dashboard's panel shows cost per 1,000 scores; the phase budget is about $10. |
| What happens when a deploy is bad? | The smoke test compares `/score` with the model pipeline's own risk. A canary gets 10% for 3 minutes on errors and p95. Any failure rolls back, and Slack says so. |
| Does an LLM see my input? | No. Every input is a fixed choice. The texts come from the block, gated against its facts. |

## The video (Dec 3–4, 5–10 minutes, from Cloud Shell)

1. `bootstrap.sh` on a fresh project (S8), sped up, ending in `curl /health`.
2. The website's three doors, as in the demo.
3. A push to `Model-Pipeline` that changes the model:
   - `model.yml` promotes it;
   - `deploy.yml` builds it;
   - smoke test, canary, 100%;
   - Slack "deployed".
4. The planted slow image: the canary fails, rolls back, and Slack says so.
5. The refresh job: drift found, retrain, the gate's verdict.
6. The dashboard: budgets, cost, scores by model version.

## Before Dec 8: what each person does

Everything below is in a PR or on main already, unless it says otherwise. Ask in the group if a step doesn't match what you see.

### Rachna: `/score` from the browser

- **CORS is not wired yet.** `serve/app.py` reads `MERIDIAN_CORS_ORIGINS`, but neither `infra/terraform/run.tf` nor `deploy.yml` sets it, so the browser's `/score` is refused (403 on the preflight, checked Nov 28).
  - Add `cors_origins = ["<meridian-views URL>"]` under `[services.risk]` in `deploy.toml`.
  - Pass it as the `MERIDIAN_CORS_ORIGINS` env (comma-joined) in `run.tf`'s risk service, and in `deploy.yml`'s revision deploy if it sets env there.
  - Check it: `curl -i -X OPTIONS -H "Origin: <views URL>" -H "Access-Control-Request-Method: POST" <risk URL>/score` should return `Access-Control-Allow-Origin`.
- **Example commits** for "Score it": send three validation commits (low, middle and high risk) with their repo. They go in `EXAMPLES` in `Data-Pipeline/site/src/views.js`, which today has only the smoke commit.

### Vishwa: S9, the views on Cloud Run

- **The image:** Data-Pipeline's environment plus a Node build stage: `cd Data-Pipeline/site && npm ci && npm run build`. Run `python -m meridian.views --run-id c5118b019d642a9b --db <block> --serve $PORT --site site/dist`. `--site` comes with the S11 API PR.
- **Listen on 0.0.0.0.** `views.py`'s `serve()` binds `127.0.0.1` only, which Cloud Run can't reach. Add a `--host` option (default `127.0.0.1`, Cloud Run `0.0.0.0`).
- **The block:** copy it from the private block bucket at start, read-only, by the service's own account. No person table is needed for any view, so consider shipping a block without the `person` and `person_alias` tables.
- **The no-names test** (PLAN S9): render every page and `/api/*` response on the real block, `/api/home` and `/api/export` included, and fail on any `person.name` or email.
- **One request at a time:** `views.py` holds a lock. That's fine for the expo; keep `max_instances` at 2.

### Rochan: hosting and warm instances

- Terraform for `meridian-views`:
  - from `deploy.toml [services.views]`: CPU, memory, `min_instances = 0`, `max_instances = 2`;
  - public invoker;
  - its own service account with read access on the block bucket only;
  - output its URL for Rachna's CORS.
- **Warm for the expo:** set `min_instances = 1` on both `meridian-risk` and `meridian-views` from Dec 7 evening to Dec 8 at 2 pm, then back to 0. Cost is a few dollars, inside the $10 bar.
- **The live session** with Utkarsh (see below).

### Chaitali: deploys

- Deploy `meridian-views` on main the way `deploy.yml` deploys the risk service, or in its own `views.yml`:
  - build;
  - smoke-test `GET /api/home` (200, `meta.run_id` present);
  - move traffic, with rollback on a failure.
- Collect the live session's evidence for the report: run links, the summary tables and Slack screenshots of all four messages.

### Utkarsh (and Rochan): the live session

Run it with `deploy.yml` on, in one sitting, then back off. Check `/health` after each step (it should still answer `85b4684`).

1. `plant = wrong-risk`: stopped at the revision's smoke test, no traffic.
2. `plant = slow`: the canary fails on p95 and rolls back.
3. A normal run: the canary passes, plus the warm-cache build time and Slack "deployed".

Also:

- Set the refresh job's Slack webhook, then unpause the daily schedule.
- C8: settle the docs that disagree.

### Aniket

- **PRs:**
  - C3: the packets move.
  - The S11 API (`/api/home`, `/api/export`, `/api/scopes`, `--site`).
  - The S11 site.
  - S8: `DEPLOY.md` and `bootstrap.sh`.
  - The sharded CI.
  - The report draft.
- On Dec 2, a teammate who didn't build it runs `bootstrap.sh` on a fresh project.
- The video on Dec 3–4; rehearsals on Dec 5 and Dec 7 on the live URL.
