# Repository layout

Cricket OS is a modular-monolith Django backend with a root-level `Makefile`
orchestration layer, an `infrastructure/` area for everything Docker/ops, a
`clients/` area for standalone API consumers, and a single root docs tree.
The layout follows the conventions used by the StudioHub reference repo
(see `reports/restructure/MAPPING.md` for the move table and rationale).

## Top-level areas

```
cricket_os/
├── .github/
│   ├── CODEOWNERS               # TODO-marked ownership scaffold (no owners yet)
│   └── workflows/               # ci.yml, migration-drift.yml, schema-ddl.yml
├── backend/                     # all Python code + its tooling (uv project root)
│   ├── apps/                    # 21 first-party Django apps (modular monolith)
│   ├── config/                  # Django project: settings/, urls, asgi, wsgi, celery
│   ├── tests/                   # the test suite (phase suites + per-app + fixtures)
│   ├── scripts/                 # utilities: extract_ddl, benchmarks, game_day/,
│   │   │                        #   security/ scans, ci/ schema-drift tooling
│   │   └── ci/                  # schema manifest/scope/allowlist + ddl_checks.sql
│   ├── loadtests/               # load-test harnesses + safety.py + smoke_gate.py
│   ├── manage.py  pyproject.toml  uv.lock  .python-version
│   ├── .env.example  .coveragerc-*  README.md
├── clients/
│   └── reference_client/        # standalone realtime reference client (stdlib)
├── docs/                        # single root docs tree (architecture, runbooks,
│   │                            #   operations, contracts, plans) + REPO_LAYOUT.md
├── infrastructure/
│   ├── compose/compose.yml      # local stack (postgres:17, redis:8, optional app/worker)
│   ├── docker/django/Dockerfile # application image (build context = repo root)
│   ├── docker/nginx/nginx.conf  # reverse-proxy config
│   └── ops/alerts/              # Prometheus alert rules + local alert sink
├── reports/                     # committed generated evidence (game_day, phase_i, security)
├── Makefile  README.md  .gitignore  .dockerignore  .editorconfig  .gitattributes
```

## Where things run from

| Area | Commands run from |
|---|---|
| All Python (`uv run`, `pytest`, `manage.py`, scripts) | `backend/` — the uv project root. `make` targets and CI use `working-directory: backend`. |
| Docker / compose | Repo root — `docker compose -f infrastructure/compose/compose.yml`. Build context is the repo root (`COPY backend/...` in the Dockerfile). |
| Docs / reports | Repo root — they live at the root and are referenced with repo-root-relative paths. |

## How to add a new Django app

1. `cd backend && uv run python manage.py startapp <name>` (or create
   `backend/apps/<name>/` by hand — every app carries `models.py` +
   `migrations/`).
2. Register it in `backend/config/settings/base.py` `INSTALLED_APPS`.
3. Add its tables to `backend/scripts/ci/schema_drift_scope.txt` (the
   migration-drift workflow enforces parity with
   `docs/CRICKET_DATABASE_SCHEMA.md`).
4. Add tests under `backend/tests/` and a coverage gate if the app is
   high-risk (see `backend/.coveragerc-*`).

## How to add a new script / CI helper

- Repo-level utilities → `backend/scripts/<area>/`.
- Schema-drift / SQL helpers consumed by workflows → `backend/scripts/ci/`.
  Workflows reference them as `scripts/ci/...` because they run with
  `working-directory: backend`.

## How to add a new alert rule

- Add the rule to the right file under `infrastructure/ops/alerts/`.
- Give it a `runbook:` label matching a `## ` heading in `docs/RUNBOOKS.md`
  (the observability contract tests enforce this).
- If the alert needs a new metric, register it via the `apps.core.metrics`
  facade and extend the corresponding observability test.

## How to add a new client

- Put standalone API consumers under `clients/<client>/`. The reference
  client (`clients/reference_client/`) is the example: stdlib-only, runnable
  from the repo root with `python clients/<client>/...`.

## How to add a new service / area (beyond backend)

1. Create the top-level directory with a short `README.md` (one table of
   contents, like `backend/README.md`).
2. Put Docker/ops assets in `infrastructure/` (image → `docker/<service>/`,
   compose overlays → `compose/`, alert rules → `ops/alerts/`).
3. Document the run command in `README.md` and this file.
4. Add a `CODEOWNERS` line once a real owner exists.

## Conventions from the StudioHub reference that were *not* adopted

See `reports/restructure/RECOMMENDATIONS.md` for the full list and rationale:
docs are not renumbered into numbered topics, tests stay in one
`backend/tests/` package (no per-app colocation), and load tests stay under
`backend/loadtests/` (the harnesses are Python and import app code).
