# MLflow tracking server with the S3 (MinIO) + PostgreSQL backends it needs.
# The stock image ships neither boto3 nor a Postgres driver, so artifacts could
# only go to local disk — which breaks model rollback (§16/§17).
FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "mlflow==3.14.0" \
    "boto3==1.43.47" \
    "psycopg[binary]==3.2.3"

EXPOSE 5000

# Backend store + artifact destination are provided by the compose command.
ENTRYPOINT ["mlflow"]
