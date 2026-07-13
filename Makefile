# BetPulse — developer entrypoints.
#
# The README documents `make <target>` from the repository root, so the
# Makefile lives here and points at the compose files under infra/.
# Targets for features not yet built (migrate, seed, train, backup, deploy…)
# are stubbed and land in their respective phases.

# The compose files live in infra/, but the .env stays at the repo root, so the
# env file is passed explicitly (otherwise compose would look for infra/.env).
COMPOSE      := docker compose --env-file .env -f infra/docker-compose.yml
COMPOSE_PROD := docker compose --env-file .env -f infra/docker-compose.yml -f infra/docker-compose.prod.yml

.DEFAULT_GOAL := help
.PHONY: help up down logs ps build test test-backend test-frontend lint lint-backend lint-frontend \
        migrate seed bootstrap-history verify-history train backup restore-drill deploy

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

## --- Stack ------------------------------------------------------------------

up: ## Start the local stack (detached)
	$(COMPOSE) up -d --build

down: ## Stop the local stack
	$(COMPOSE) down

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

ps: ## Show service status
	$(COMPOSE) ps

build: ## Build all images
	$(COMPOSE) build

## --- Quality ----------------------------------------------------------------

test: test-backend test-frontend ## Run all tests

test-backend: ## Run backend tests (pytest)
	cd backend && python -m pytest

test-frontend: ## Run frontend tests (vitest)
	cd frontend && npm run test

lint: lint-backend lint-frontend ## Lint and type-check everything

lint-backend: ## Ruff + mypy
	cd backend && ruff check . && mypy

lint-frontend: ## ESLint + tsc
	cd frontend && npm run lint && npm run typecheck

## --- Data & ops (implemented in later phases) -------------------------------

migrate: ## Apply DB migrations (alembic upgrade head)
	cd backend && alembic upgrade head

seed: ## Create the bootstrap admin account
	cd backend && python -m app.bootstrap create-admin

bootstrap-history: ## Ingest football-data.co.uk historical CSVs (HISTORY_ARGS to scope)
	cd backend && python -m app.cli bootstrap-history $(HISTORY_ARGS)

verify-history: ## Print per league/season fixture+odds counts; fail on gaps
	cd backend && python -m app.cli verify-history $(HISTORY_ARGS)

train: ## Train all enabled ML methods (Phase 4)
	@echo "train: not implemented until Phase 4"

backup: ## On-demand encrypted backup (Phase 17 track)
	@echo "backup: not implemented yet"

restore-drill: ## Restore latest backup into a throwaway container (Phase 17 track)
	@echo "restore-drill: not implemented yet"

deploy: ## Pull GHCR images, migrate, up -d, health-check (Phase 14)
	@echo "deploy: not implemented until Phase 14"
