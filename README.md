# Cricket OS

Cricket scores and statistics backend. This repository currently contains
**Phase A (Foundation)**: a runnable, conventional Django project with
configuration, structured logging, a standard error envelope, UUIDv7 primary
keys, pagination and test infrastructure — and **no domain models yet**. The
full design lives in the [planning package](docs/README.md).

- Conventions for the database: [docs/DB_CONVENTIONS.md](docs/DB_CONVENTIONS.md)
- How we change the schema safely: [docs/MIGRATION_WORKFLOW.md](docs/MIGRATION_WORKFLOW.md)

## Stack

Python 3.14 · Django 6 · Django REST Framework · PostgreSQL 17 (psycopg 3) ·
Redis 8 · Celery + Beat · Channels + Uvicorn · uv · ruff · mypy ·
pytest / pytest-django / pytest-cov · factory_boy · hypothesis · import-linter.

## Quick start (under 10 minutes)

Prerequisites: [Docker](https://docs.docker.com/get-docker/) (with Compose) and
[uv](https://docs.astral.sh/uv/). `uv` downloads the pinned Python 3.14
interpreter for you — you do not need Python pre-installed.

```bash
# 1. Get the code and create your local environment file
git clone <repo-url> cricket_os && cd cricket_os
cp .env.example .env                 # dev-safe defaults; never commit .env

# 2. Install dependencies (creates .venv + uv.lock-backed environment)
uv sync

# 3. Start PostgreSQL 17 + Redis 8 and wait for them to be healthy
make up

# 4. Create the `cricket` schema and Django tables
make migrate

# 5. Run the API (ASGI via uvicorn)
make run            # http://localhost:8000

# 6. In another terminal: check health
curl -s localhost:8000/api/v1/health
# {"data":{"status":"ok","checks":{"database":"ok","redis":"ok"}}}
```

If host ports `5432`/`6379` are already in use, set `POSTGRES_PORT`/`REDIS_PORT`
(and the matching `DATABASE_URL`/`REDIS_URL`) in your `.env` — the compose file
reads the same variables.

## Common commands

| Command | Purpose |
|---|---|
| `make up` / `make down` | Start/stop PostgreSQL + Redis |
| `make migrate` | Apply database migrations |
| `make run` | Run the ASGI dev server (uvicorn, port 8000) |
| `make worker` | Run a Celery worker |
| `make test` | Full test suite with the coverage gate |
| `make test-nocov` | Fast inner loop (no coverage) |
| `make lint` | ruff lint + format check |
| `make fmt` | Auto-fix lint and format |
| `make typecheck` | mypy (django-stubs + DRF stubs) |
| `make imports` | import-linter boundaries |
| `make check` | Everything CI enforces (lint, types, imports, tests, coverage) |

## What exists in Phase A

- `apps/core/` — `uuid7`, `time`, `logging`, `middleware`, `errors`,
  `pagination`, `enums`, `health`, abstract base models, and the initial
  migration that creates the `cricket` schema + `pg_trgm`/`btree_gin`.
- `GET /api/v1/health` — unauthenticated readiness probe (200 healthy, 503 with
  the standard error envelope when a dependency is down).
- Settings split into `base`/`dev`/`prod`/`test`; `prod` refuses to start with
  `DEBUG` on, the default secret key, or empty `ALLOWED_HOSTS`.

See the [roadmap](docs/CRICKET_IMPLEMENTATION_ROADMAP.md) for what comes next.
