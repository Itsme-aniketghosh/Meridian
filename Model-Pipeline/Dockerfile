# The whole model pipeline in one image (guideline §8.1). From Model-Pipeline/:
#   docker build -t meridian-model .
#   docker run --rm -v "$PWD/reports:/app/reports" meridian-model --candidate lgbm
FROM python:3.11-slim

# LightGBM needs the OpenMP runtime, which the slim image doesn't ship.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MLFLOW_TRACKING_URI=sqlite:////app/mlflow.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code, config and the committed feature table. .dockerignore keeps out reports, MLflow runs and caches.
COPY . .

ENTRYPOINT ["python", "-m", "model.run_all"]
