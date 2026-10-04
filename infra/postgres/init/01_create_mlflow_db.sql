-- MLflow keeps its tracking metadata in a dedicated database in the same
-- PostgreSQL instance (queryable via SQL); model artifacts live on the
-- mlflow_artifacts volume, served by the MLflow server.
SELECT 'CREATE DATABASE mlflow OWNER ' || current_user
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow')\gexec
