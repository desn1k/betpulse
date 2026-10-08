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

### Migrations — model must equal migration

**Every new table's columns must exactly match its Alembic migration — verify
this before every commit.** On an empty local database the tests build the schema
with `Base.metadata.create_all` (straight from the models). In CI the backend job
first runs `alembic upgrade head → downgrade base → upgrade head`, so pytest runs
against the **migration-built** schema (`create_all` skips tables that already
exist), and production builds it with `alembic upgrade head` too. A column that a
model declares but the migration omits — or vice versa — therefore **passes
locally and only fails in CI**. No test compares the two automatically.

The classic trap: adding `TimestampMixin` (or any mixin that contributes
columns, e.g. `created_at` / `updated_at`) to a model without adding those exact
columns to the migration. The ORM then emits `RETURNING ... updated_at` against a
table that has no such column and errors with `UndefinedColumnError` — but only
on a migration-built database.

Before committing a new or changed model, check both directions:

- Every mapped column on the model (including columns pulled in by mixins) exists
  in the migration's `create_table` / `add_column`.
- Every column in the migration exists on the model.
- If a model does not need a column a mixin would add, drop the mixin and declare
  the columns you do want explicitly (e.g. a join/follow row that is only ever
  created or deleted needs `created_at` but not `updated_at`).

Fastest way to be sure: run the suite against a **migration-built** database, not
just the `create_all` one — `alembic upgrade head` into a scratch database, then
run pytest against it (on Windows, in the Linux test container described in
`HANDOFF.md` §7). Migrations must also round-trip
(`upgrade → downgrade → upgrade`) cleanly.

## Pull requests

- Branch from `main`, keep PRs focused on a single phase or change.
- Include tests for new behaviour; keep backend coverage ≥ 80%.
- Update `docs/DATA_SOURCES.md` when adding or changing a data provider — CI
  verifies every implemented provider has a section there.
