# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016 AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# LightGBM's wheel links the OpenMP runtime, which the slim image lacks: without
# libgomp1 every worker (they import the training code) dies at startup with
# "libgomp.so.1: cannot open shared object file". CI imports it in the image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
# Migrations ship with the image: deploy.sh runs `alembic upgrade head` in a
# one-off api container (CI runs it against Timescale in the built image).
COPY alembic.ini ./
COPY migrations ./migrations

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

# Proxy headers are resolved by the app against TRUSTED_PROXY_CIDRS
# (app/core/client_ip.py); uvicorn must leave the raw peer address untouched.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
