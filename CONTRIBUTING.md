# Contributing

Thanks for helping build the platform. This guide covers local setup and the
quality gate every change must pass.

## Ground rules

- All code, comments, commit messages, identifiers, and docs are in **English**.
- **Conventional Commits** for messages (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, …).
- No legacy or deprecated libraries. No secrets in code — everything from env.
- No raw SQL string building; use the ORM or bound parameters only.
- CI (`ci.yml`) and security scans (`security.yml`) must be **green before merge**.
  Branch protection enforces this.

## Repository layout

```
/backend    FastAPI app (app/api, core, models, schemas, providers, ml, services, workers)
/frontend   Next.js 15 app (App Router, RU/EN i18n)
/infra      docker-compose*, Caddyfile
/docs       DATA_SOURCES.md and other docs
/.github    workflows, dependabot
Makefile    developer entrypoints
```

## Local development

Prerequisites: Docker + Docker Compose, Python 3.12, Node.js 22.

```bash
cp .env.example .env      # fill in the required secrets (see comments in the file)
make up                   # start the stack
make ps                   # check health
```

- API:    http://localhost:8000  (docs at `/docs`, health at `/health`)
- Web:     http://localhost:3000  (health at `/api/health`)
- MLflow:  http://localhost:5000

### Backend

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
mypy
python -m pytest
```

### Frontend

```bash
cd frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
```

Or from the repo root: `make lint` and `make test` run both stacks.

## Pull requests

- Branch from `main`, keep PRs focused on a single phase or change.
- Include tests for new behaviour; keep backend coverage ≥ 80%.
- Update `docs/DATA_SOURCES.md` when adding or changing a data provider — CI
  verifies every implemented provider has a section there.
