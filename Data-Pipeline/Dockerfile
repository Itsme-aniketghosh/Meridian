# Airflow plus the pipeline's own environment. Airflow and the pipeline pin different versions of the same libraries,
# so the parts run from /opt/meridian-venv (settings.toml [orchestration] python), built from requirements.txt.
# Build context: the repo root (docker-compose.yaml sets it).
ARG AIRFLOW_IMAGE=apache/airflow:2.10.5-python3.11
FROM ${AIRFLOW_IMAGE}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir /opt/meridian-venv && chown airflow:0 /opt/meridian-venv   # /opt is root's; the venv is airflow's

USER airflow
COPY Data-Pipeline/requirements.txt /tmp/meridian-requirements.txt
RUN python -m venv /opt/meridian-venv \
 && /opt/meridian-venv/bin/pip install --no-cache-dir -r /tmp/meridian-requirements.txt
