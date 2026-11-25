# S4: the refresh job's image, Cloud Run job meridian-refresh (deploy.toml [jobs.refresh], image [images] refresh).
# The data pipeline's parts and the model's feature code, at the repo's pins. From the repo root:
#   docker build -f Model-Deployment/refresh/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) -t meridian-refresh .
#   docker run --rm -v "$HOME/refresh-data:/work" meridian-refresh --store local:/work/store
# Model-Deployment/refresh/Dockerfile.dockerignore says which files reach the build.
# Pinned: an exact release and its digest, so a rebuild gets the same base. Bump both together, on purpose
FROM python:3.11.15-slim-trixie@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

# git: collect mirrors and checks out apache/spark. curl: collect fetches Jira's tickets with it (collect.curl).
# libgomp1: LightGBM, for the retrain step (model.refresh).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG GIT_SHA=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MERIDIAN_GIT_SHA=${GIT_SHA} \
    MERIDIAN_DATA=/work \
    MLFLOW_TRACKING_URI=sqlite:////work/mlflow.db

WORKDIR /app
# Only the job's packages (refresh/requirements.txt), at the pipeline's pins: no DVC, no LLM clients, no shap
COPY Model-Deployment/refresh/requirements.txt Model-Deployment/refresh/requirements.txt
RUN pip install --no-cache-dir -r Model-Deployment/refresh/requirements.txt

# The data pipeline: its parts and the files they read next to them (common.HERE)
COPY Data-Pipeline/meridian Data-Pipeline/meridian
COPY Data-Pipeline/meridian.toml Data-Pipeline/settings.toml Data-Pipeline/schema.sql Data-Pipeline/KNOWLEDGE-BASE.md Data-Pipeline/
# The model's feature table code and config, and the champion's pointer (its version goes on every D5 line)
COPY Model-Pipeline/model.toml Model-Pipeline/model.toml
COPY Model-Pipeline/model Model-Pipeline/model
COPY Model-Pipeline/registry/champion.json Model-Pipeline/registry/model.joblib Model-Pipeline/registry/
# The retrain step (deploy.toml [retrain]): the committed best params a refit uses. The gate re-scores the champion's
# model.joblib (above) on the new val rows
COPY Model-Pipeline/reports/tuning_logreg.json Model-Pipeline/reports/tuning_forest.json Model-Pipeline/reports/tuning_lgbm.json Model-Pipeline/reports/
# Phase 3: names (D4), the job, S5's monitor
COPY Model-Deployment/deploy.toml Model-Deployment/deploy_config.py Model-Deployment/
COPY Model-Deployment/refresh Model-Deployment/refresh
COPY Model-Deployment/monitor Model-Deployment/monitor
# S2's serve code: the job makes history.pkl with serve.precompute (the endpoint's own code, so they can't drift)
COPY Model-Deployment/serve/*.py Model-Deployment/serve/

# Runs as a user with no root: it owns /work (clones, the block, history.pkl) and nothing in /app
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin meridian \
 && mkdir -p /work && chown meridian:meridian /work
USER 10001:10001
ENV HOME=/home/meridian
WORKDIR /app
ENTRYPOINT ["python", "Model-Deployment/refresh/job.py"]
