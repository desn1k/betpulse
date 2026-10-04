# MLflow tracking server with the PostgreSQL driver it needs (the stock image
# ships none). Artifacts are stored on a local volume and served by the server
# itself (--serve-artifacts), so no object-storage client is installed.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

RUN pip install --no-cache-dir \
    "mlflow==3.14.0" \
    "psycopg[binary]==3.2.3"

EXPOSE 5000

# Backend store + artifact destination are provided by the compose command.
ENTRYPOINT ["mlflow"]
