# S2: the serving image, Cloud Run service meridian-risk (deploy.toml [services.risk], image [images] risk).
# The champion baked in, md5-checked at build and again at start. From the repo root, as deploy.yml builds it:
#   docker build -f Model-Deployment/serve/Dockerfile \
#     --build-arg MODEL_VERSION=<version> --build-arg MODEL_MD5=<md5> -t meridian-risk .
#   docker run --rm -p 8080:8080 meridian-risk
# Pushes need the block's history state, Model-Deployment/serve/history.pkl (python -m serve.precompute), in the
# build context: with it, a push scores in milliseconds and the image needs no block or GCS at start; without it,
# the image scores known SHAs only (the start line says so). Model-Deployment/serve/Dockerfile.dockerignore says
# which files reach the build.
FROM python:3.11-slim

# LightGBM's OpenMP runtime, in case a LightGBM champion is ever promoted (the slim image doesn't ship it)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

ARG MODEL_VERSION=""
ARG MODEL_MD5=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    MERIDIAN_HISTORY=/app/Model-Deployment/serve/history.pkl

WORKDIR /app
COPY Model-Deployment/serve/requirements.txt Model-Deployment/serve/requirements.txt
RUN pip install --no-cache-dir -r Model-Deployment/serve/requirements.txt

# The feature code (the push check's p9.py and the model's data.py and train.py) and the config they read
COPY Data-Pipeline/meridian/__init__.py Data-Pipeline/meridian/p9.py Data-Pipeline/meridian/
COPY Data-Pipeline/meridian.toml Data-Pipeline/settings.toml Data-Pipeline/
COPY Model-Pipeline/model.toml Model-Pipeline/model.toml
COPY Model-Pipeline/model/__init__.py Model-Pipeline/model/data.py Model-Pipeline/model/train.py Model-Pipeline/model/
# What a score is read against: the feature table (known SHAs), val's scores (the percentile), P9's rule
COPY Model-Pipeline/data/features.parquet Model-Pipeline/data/features.parquet
COPY Model-Pipeline/reports/champion/champion_val_scores.csv Model-Pipeline/reports/champion/champion_val_scores.csv
COPY Model-Pipeline/reports/p9_rule.json Model-Pipeline/reports/p9_rule.json
# The champion deploy.yml staged here, then refused unless it's the version and md5 it passed
COPY Model-Pipeline/registry/ Model-Pipeline/registry/
COPY Model-Deployment/serve/ Model-Deployment/serve/
RUN cd Model-Deployment && python -m serve.buildcheck --version "$MODEL_VERSION" --md5 "$MODEL_MD5"

# Not root: the endpoint only reads its files
RUN useradd --system --no-create-home --uid 10001 serve
USER serve
WORKDIR /app/Model-Deployment
CMD ["python", "-m", "serve.app"]
