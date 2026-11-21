# S4: the refresh job's image, Cloud Run job meridian-refresh (deploy.toml [jobs.refresh], image [images] refresh).
# The data pipeline's parts and the model's feature code, at the repo's pins. From the repo root:
#   docker build -f Model-Deployment/refresh/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) -t meridian-refresh .
#   docker run --rm -v "$HOME/refresh-data:/work" meridian-refresh --store local:/work/store
# Model-Deployment/refresh/Dockerfile.dockerignore says which files reach the build.
FROM python:3.11-slim

# git: collect mirrors and checks out apache/spark. libgomp1: LightGBM, for the retrain step (model.refresh).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git libgomp1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ARG GIT_SHA=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MERIDIAN_GIT_SHA=${GIT_SHA} \
    MERIDIAN_DATA=/work \
    MLFLOW_TRACKING_URI=sqlite:////work/mlflow.db

WORKDIR /app
COPY Data-Pipeline/requirements.txt Data-Pipeline/requirements.txt
COPY Model-Pipeline/requirements.txt Model-Pipeline/requirements.txt
RUN pip install --no-cache-dir -r Model-Pipeline/requirements.txt

# The data pipeline: its parts and the files they read next to them (common.HERE)
COPY Data-Pipeline/meridian Data-Pipeline/meridian
COPY Data-Pipeline/meridian.toml Data-Pipeline/settings.toml Data-Pipeline/schema.sql Data-Pipeline/KNOWLEDGE-BASE.md Data-Pipeline/
# The model's feature table code and config, and the champion's pointer (its version goes on every D5 line)
COPY Model-Pipeline/model.toml Model-Pipeline/model.toml
COPY Model-Pipeline/model Model-Pipeline/model
COPY Model-Pipeline/registry/champion.json Model-Pipeline/registry/champion.json
# Phase 3: names (D4), the job, and S5's monitor
COPY Model-Deployment/deploy.toml Model-Deployment/deploy_config.py Model-Deployment/
COPY Model-Deployment/refresh Model-Deployment/refresh
COPY Model-Deployment/monitor Model-Deployment/monitor

RUN mkdir -p /work
WORKDIR /app
ENTRYPOINT ["python", "Model-Deployment/refresh/job.py"]
