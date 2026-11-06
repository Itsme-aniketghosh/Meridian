# The whole model pipeline in one image (guideline §8.1). From the repo root, because the model's
# requirements.txt pulls in Data-Pipeline/requirements.txt (one set of pins for both pipelines):
#   docker build -f Model-Pipeline/Dockerfile --build-arg GIT_SHA=$(git rev-parse HEAD) -t meridian-model .
#   docker run --rm -v "$PWD/Model-Pipeline/reports:/app/Model-Pipeline/reports" meridian-model --candidate lgbm
# Model-Pipeline/Dockerfile.dockerignore says which files reach the build; the repo's .dockerignore is for
# Data-Pipeline's image.
FROM python:3.11-slim

# LightGBM needs the OpenMP runtime, which the slim image doesn't ship.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# No .git in the image: tracking.py tags runs with this instead (Contract 3, git_sha).
ARG GIT_SHA=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MERIDIAN_GIT_SHA=${GIT_SHA} \
    MLFLOW_TRACKING_URI=sqlite:////app/mlflow.db

WORKDIR /app
COPY Data-Pipeline/requirements.txt Data-Pipeline/requirements.txt
COPY Model-Pipeline/requirements.txt Model-Pipeline/requirements.txt
RUN pip install --no-cache-dir -r Model-Pipeline/requirements.txt

# meridian.toml: tracking.py puts its hash on every run. Then the model's code, config and feature table.
COPY Data-Pipeline/meridian.toml Data-Pipeline/meridian.toml
COPY Model-Pipeline/model.toml Model-Pipeline/model.toml
COPY Model-Pipeline/data Model-Pipeline/data
COPY Model-Pipeline/model Model-Pipeline/model
COPY Model-Pipeline/tests Model-Pipeline/tests

WORKDIR /app/Model-Pipeline
ENTRYPOINT ["python", "-m", "model.run_all"]
