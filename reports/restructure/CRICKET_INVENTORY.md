# Cricket OS Repo Inventory (for path-restructure planning)

**Repo:** `/home/prafull.sakharkar/Repository/github/cricket_os`
**Branch:** `chore/restructure-like-studiohub` (clean tree, identical to `develop` at `47f9002`)
**Tracked files:** 714 · `git status --porcelain`: empty. Local ignored artifacts present (`.venv/`, `.coverage`, `coverage.xml`, `mutants/`, `.benchmarks/`, `.hypothesis/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.import_linter_cache/`, `.schemathesis/`, `.superpowers/`, `.env`). `.env` exists locally but was **not** read (names only).

## 1. Current tree (git ls-files, depth ≤ 2 + notable substructure)

```
/ (repo root)
├── .coveragerc-identity .coveragerc-phasec .coveragerc-phasef .coveragerc-phaseh .coveragerc-phasei
├── .dockerignore .env.example .gitignore .python-version ("3.14")
├── .github/workflows/{ci.yml, migration-drift.yml, schema-ddl.yml}
├── Dockerfile
├── Makefile  README.md  docker-compose.yml  manage.py  pyproject.toml  uv.lock
├── apps/                       # 21 Django apps (modular monolith)
│   ├── administration competitions core deliveries fantasy ingestion innings live
│   ├── matches players providers rankings realtime scorecards scoring search
│   ├── shadow statistics synchronization teams venues
│   └── __init__.py             # (each app: models.py, migrations/, apps.py; most have
│                               #  api/, services/, selectors/; some have management/commands/,
│                               #  templates/admin/, adapters/, engine/ (fantasy), laws/ (scoring))
├── ci/
│   ├── ddl_checks.sql  schema_drift_allowlist.txt  schema_drift_scope.txt  schema_manifest.sql
├── config/
│   ├── __init__.py  asgi.py  celery.py  urls.py  wsgi.py
│   └── settings/{__init__, base, dev, prod, test}.py
├── docs/                       # 45 .md at docs/ root + docs/superpowers/plans/*.md
│   ├── README.md  RUNBOOKS.md  OPERATIONS.md  CRICKET_DATABASE_SCHEMA.md
│   ├── CRICKET_BACKEND_ARCHITECTURE.md  SECURITY_REVIEW.md  ... (see §2)
│   └── superpowers/plans/{phase-b,phase-d,phase-e,phase-f,phase-h,phase-i}-*.md
├── loadtests/                  # namespace package (no __init__.py)
│   ├── harness_common.py harness_correction_burst.py harness_deliveries_feed.py
│   ├── harness_ingest_throughput.py harness_marquee_spike.py harness_read_soak.py
│   ├── harness_realtime_reconnect.py k6/{cache_reads.js, sse_fanout.js}
│   ├── README.md safety.py smoke_gate.py soak.py
├── ops/                        # namespace package (no __init__.py)
│   ├── alerts/{fantasy_alerts, live_alerts, realtime_alerts, shadow_alerts, statistics_alerts}.yml
│   ├── alerts/local_sink.py
│   └── proxy/nginx.conf
├── reports/                    # generated evidence, committed
│   ├── game_day/ (alerts.log + drill scenario .md reports)
│   ├── phase_i/ (task evidence + load/*.json harness results)
│   └── security/ (dast-*.md)
├── scripts/                    # namespace package (no __init__.py)
│   ├── benchmark_importer.py benchmark_live_path.py benchmark_statistics.py
│   ├── consumer_ladder.py extract_ddl.py fantasy_benchmark.py regenerate_api_golden.py
│   ├── game_day/{common.py, drill_rebuild_derived.py, drill_restore.py,
│   │            scenario_*.py (6 scenarios)}
│   └── security/{scan_secrets.py, dast_scan.py}
├── tests/                      # Python package (has __init__.py)
│   ├── conftest.py  http_urls.py  __init__.py
│   ├── admin/ administration/ api/ competitions/ contracts/ dummy_app/ factories/
│   ├── fixtures/ players/ providers/ resolvers/ scoring/ teams/ venues/
│   ├── phase_c/ phase_d/ phase_e/ phase_f/ phase_h/ phase_i/
│   └── test_*.py (~25 top-level test modules: asgi, celery, enums, errors, events,
│       gate_docs, health, http, logging, middleware, migrations, pagination, schema,
│       schemathesis_smoke, settings, time, uuid7 ...)
└── tools/reference_client/{README.md, realtime_client.py}
```

## 2. Role of every top-level path

| Path | Role (inferred from contents) |
|---|---|
| `apps/` | The Django modular monolith: 21 first-party apps (`core`, `scoring`, `matches`, `players`, `teams`, `venues`, `competitions`, `deliveries`, `innings`, `scorecards`, `live`, `realtime`, `ingestion`, `synchronization`, `providers`, `administration`, `statistics`, `search`, `rankings`, `shadow`, `fantasy`). Each app carries `models.py` + `migrations/`; most add `api/`, `services/`, `selectors/`; some add `management/commands/`, `templates/admin/`, `adapters/`, `engine/`, `laws/`, `cache/`. Import-linter `root_packages=["apps"]`. |
| `config/` | Django project package: `settings/{base,dev,prod,test}.py`, `urls.py`, `asgi.py`, `wsgi.py`, `celery.py`. `BASE_DIR = parents[2]` (repo root). Reads `.env` at `BASE_DIR/.env`. |
| `ci/` | Schema-drift tooling: `schema_manifest.sql` (manifest query), `schema_drift_scope.txt` (59 scoped tables), `schema_drift_allowlist.txt`, `ddl_checks.sql`. Read by `migration-drift.yml` + `schema-ddl.yml` and referenced in their `paths:` filters. |
| `config/` + `manage.py` + `pyproject.toml` + `uv.lock` + `.python-version` | The Python project root: uv project manifest/lock, Django entrypoint. All `uv run`/`pytest`/`manage.py` commands run from repo root today. |
| `docs/` | Single root docs tree: architecture (`CRICKET_BACKEND_ARCHITECTURE`), schema (`CRICKET_DATABASE_SCHEMA`), runbooks (`RUNBOOKS.md`), operations (`OPERATIONS.md`), security (`SECURITY_REVIEW.md`), planning/phase docs (`FANTASY`, `SHADOW_MODE`, `LIVE_INGESTION`, …), contracts (`FRONTEND_CONTRACT`, `API_DEPRECATION_POLICY`), gates (`GO_NO_GO`, `LAUNCH_CRITERIA`, `PROVIDER_DECISION`, `STAGING_ACCESS`), plus `superpowers/plans/` (SDD plans). |
| `loadtests/` | Phase E load harnesses (namespace package): `smoke_gate.py` (CI-gated p95 budgets), `soak.py`, per-scenario `harness_*.py`, k6 JS scripts, `safety.py` (G3 target allow-list). |
| `ops/` | Ops artifacts (namespace package): `alerts/*.yml` (Prometheus rules), `alerts/local_sink.py` (Python alert sink), `proxy/nginx.conf`. |
| `reports/` | Committed generated evidence: `game_day/` drills, `phase_i/` task evidence + load JSON, `security/` DAST reports. |
| `scripts/` | Standalone utilities (namespace package): `extract_ddl.py` (workflow-called), `benchmark_*.py`, `consumer_ladder.py`, `fantasy_benchmark.py`, `regenerate_api_golden.py`, `game_day/` (drills + scenarios), `security/` (`scan_secrets.py`, `dast_scan.py`). |
| `tests/` | Top-level test package (single suite split by phase/app area): `phase_c..phase_i`, per-app dirs (`api`, `scoring`, `players`, …), `contracts/`, `dummy_app/` (installed app for tests), `factories/`, `fixtures/`. |
| `tools/reference_client/` | Standalone realtime reference client (`realtime_client.py`) + README; consumed by `scripts/consumer_ladder.py` (sys.path insert) and `tests/phase_e/test_reference_client.py` (subprocess). |
| `.github/workflows/` | CI: `ci.yml` (mirrors `make check`), `migration-drift.yml`, `schema-ddl.yml`. |
| `Dockerfile` + `docker-compose.yml` | Single-stage-ish Django image (repo-root build context) + compose with postgres/redis/django services. |
| `Makefile` | Root orchestration: `UV ?= uv`, `COMPOSE ?= docker compose`, `MANAGE := $(UV) run python manage.py`, `.DEFAULT_GOAL := help`; targets `up/down/logs/migrate/makemigrations/run/worker/test*/lint/fmt/typecheck/imports/check/shell`. |
| Root files | `.env.example` (all env vars documented), `.python-version`, 5× `.coveragerc-*` (per-phase coverage gates), `.gitignore`, `.dockerignore`. |

## 3. Reference map (who references what)

### 3.1 Makefile (`UV ?= uv` at line 2)
- All Python targets run `$(UV) run ...` **from the repo root** — e.g. `test` runs `uv run pytest --cov=apps/...`; `test-loadtest-smoke` runs `DJANGO_SETTINGS_MODULE=config.settings.test uv run python loadtests/smoke_gate.py`; `test-secrets` runs `uv run python scripts/security/scan_secrets.py`; `test-security` runs the same plus a pytest file list.
- Coverage gates reference the 5 `.coveragerc-*` files and `--cov=apps/...` packages (package names unchanged by a move).
- `migrate/run/worker/shell` use `$(MANAGE)` / `uvicorn config.asgi` / `celery -A config.celery`.

### 3.2 GitHub workflows
- **`ci.yml`**: no `working-directory` today — every step runs from repo root (`uv sync --frozen`, `uv run ruff check .`, `uv run mypy apps config`, `uv run lint-imports`, `uv run python manage.py makemigrations --check --dry-run`, `uv run pytest --cov=apps/...`, `uv run python loadtests/smoke_gate.py`, upload artifact `coverage.xml`). `on: push: branches: [main]` + `pull_request`.
- **`migration-drift.yml`**: `paths:` filters `apps/**`, `config/**`, `docs/CRICKET_DATABASE_SCHEMA.md`, `ci/**`, `scripts/extract_ddl.py`, `pyproject.toml`, `uv.lock`, workflow file. Steps run from root: `python scripts/extract_ddl.py | psql -d ref_db ...`, `uv run python manage.py migrate --noinput`, `grep -vE ... ci/schema_drift_scope.txt`, `psql ... -f ci/schema_manifest.sql`, `ci/schema_drift_allowlist.txt`.
- **`schema-ddl.yml`**: `paths:` filters `docs/CRICKET_DATABASE_SCHEMA.md`, `scripts/extract_ddl.py`, `ci/ddl_checks.sql`, workflow file. Steps run from root: `python scripts/extract_ddl.py > reference.sql`, `psql ... -f ci/ddl_checks.sql`.

### 3.3 pyproject.toml (repo root)
- `[project] name = "cricket-os"`, `version = "0.1.0"`, `[tool.uv]`.
- `[tool.pytest.ini_options] testpaths = ["tests"]`, `addopts = [...]`.
- `[tool.coverage.run]` + `[tool.coverage.report]` (root config for the app-wide gate).
- `[tool.ruff]`, `[tool.ruff.lint]`, `per-file-ignores`, `[tool.ruff.lint.isort]`.
- `[tool.mypy]`, `module = ["*.migrations.*"]`, ignore modules, `[tool.django-stubs] django_settings_module = "config.settings.test"`.
- `[tool.importlinter] root_packages = ["apps"]` + ~12 contracts ("core is a leaf…", "selectors must not import services", "api must not import provider adapters…", "domain apps never import administration/ingestion/realtime", "fantasy engine is stdlib-only…", "deliveries must not write scorecard rows", "scoring may import only core", "realtime depends only on core", "teams model module never imports players models", "players model module never imports teams models", …).

### 3.4 config/settings
- `base.py:26` `BASE_DIR = Path(__file__).resolve().parents[2]` → **repo root**. Paths derived from it: `BASE_DIR / ".env"` (line 72), `RANKINGS_FIXTURE_DIR = BASE_DIR / "tests" / "fixtures" / "rankings"` (line 200), `DIRS: [BASE_DIR / "templates"]` (line 284), `STATIC_ROOT = BASE_DIR / "staticfiles"` (line 520), `MEDIA_ROOT = BASE_DIR / "media"` (line 523). `SHADOW_REPORT_DIR = env("SHADOW_REPORT_DIR")` (line 131; default `"reports/shadow"` in env spec line 67) — no root-path assertion; tests override via `override_settings`.
- `manage.py` defaults to `DJANGO_SETTINGS_MODULE = config.settings.dev`.

### 3.5 Scripts
- `scripts/extract_ddl.py:14` default doc path `docs/CRICKET_DATABASE_SCHEMA.md` (workflows call it with **no** argument).
- `scripts/consumer_ladder.py:38` `ROOT = Path(__file__).resolve().parents[1]`; sys.path-inserts `ROOT` and `ROOT/"tools"/"reference_client"`.
- `scripts/game_day/common.py:27` `ROOT = Path(__file__).resolve().parents[2]`; `REPORTS_DIR = ROOT/"reports"/"game_day"`; imports `from loadtests.safety import assert_target_allowed`.
- `scripts/game_day/*.py` all `from scripts.game_day import common` (package-relative).

### 3.6 ops
- `ops/alerts/local_sink.py:25` `RUNBOOKS = ROOT / "docs" / "RUNBOOKS.md"` (ROOT computed from parents); runbook anchors validated to exist.
- `ops/alerts/*.yml` Prometheus rules with `runbook:` labels pointing to `docs/RUNBOOKS.md` sections.
- **Tests scanning ops paths:** `tests/phase_d/test_observability.py` (hard-coded `ops/alerts/{live,realtime,statistics,fantasy,shadow}_alerts.yml` dict, lines 121–154); `tests/phase_i/test_observability.py:21` `SHADOW_ALERT_FILE = "ops/alerts/shadow_alerts.yml"`; `tests/phase_i/test_game_day.py` imports `from ops.alerts.local_sink import route, runbook_link` (lines 90, 113) and `ROOT = parents[2]`.

### 3.7 loadtests
- `loadtests/smoke_gate.py` — run by CI and `make test-loadtest-smoke` under `config.settings.test`; migrates its own DB.
- `loadtests.safety` (`TargetNotAllowed`, `allow_list`, `assert_target_allowed`) imported by `scripts/game_day/common.py:25` and `tests/phase_i/test_harness_safety.py:22`.

### 3.8 tools/reference_client
- Referenced by `scripts/consumer_ladder.py` (sys.path insert + subprocess) and `tests/phase_e/test_reference_client.py:21` (`Path(__file__).resolve().parents[2] / "tools" / "reference_client" / "realtime_client.py"` via subprocess).

### 3.9 tests (path assumptions)
- `tests/phase_i/test_security.py:30-32` `ROOT = parents[2]`; `SECRET_SCAN = ROOT/"scripts"/"security"/"scan_secrets.py"`; `DAST_SCAN = ROOT/"scripts"/"security"/"dast_scan.py"`; subprocess `cwd=ROOT`.
- `tests/phase_i/test_harness_safety.py:22` `from loadtests.safety import ...` (namespace package import).
- `tests/phase_i/test_game_day.py:24` `ROOT = parents[2]`; `ops.alerts.local_sink` imports.
- `tests/phase_e/test_reference_client.py:21` subprocess path into `tools/reference_client`.
- `tests/phase_i/test_shadow_*.py` override `SHADOW_REPORT_DIR` via `override_settings` (no root-path coupling).
- `tests/dummy_app/` installed as `tests.dummy_app` in settings (`INSTALLED_APPS`), importable as `tests.*` from root.

### 3.10 Namespace packages
- `loadtests/`, `scripts/`, `ops/` have **no `__init__.py`** → PEP 420 namespace packages. `tests/` HAS `__init__.py` (regular package).
- Cross-imports that must keep working: `scripts.game_day.common` ⇄ `scripts.game_day.*`; `scripts.security.*`; `loadtests.safety` ⇄ `scripts/game_day/common.py` + tests; `ops.alerts.local_sink` ⇄ `tests/phase_i/test_game_day.py`.

### 3.11 Docker
- Root `Dockerfile` (build context = repo root, no `COPY backend/...`), `docker-compose.yml` (postgres/redis/django services; volumes; build context root), `ops/proxy/nginx.conf` (proxy service).

### 3.12 Ignore/env files
- `.gitignore`: Python/caches/mutation artifacts/Django static/log, `.env` + `.env.*` **except** `!.env.example`, `data/`, `.superpowers/`, `.schemathesis/`.
- `.dockerignore`: `__pycache__`, `.venv`, `.env`, `.git`, caches, `data/`.
- `.env.example` at root documents every env var. No per-area `.env.example` today.

## 4. Test layout
- Single top-level `tests/` package. Split: `tests/phase_c`, `phase_d`, `phase_e`, `phase_f`, `phase_h`, `phase_i` (phase suites) + per-app dirs (`api`, `scoring`, `players`, `teams`, `venues`, `administration`, `competitions`, `providers`, `resolvers`, `contracts`, `admin`) + support (`factories/`, `fixtures/`, `dummy_app/`) + ~25 root test modules.
- Makefile/CI gates: `test` (app-wide ≥90%), `test-scoring` (≥95%), `test-identity` (100%), `test-phasec/f/h/i` (per-phase `.coveragerc-*` gates), `test-nocov`, `test-loadtest-smoke`, `test-secrets`, `test-security`.
- Baseline (Stage 0, pre-restructure): `make check` exit 0 — 182 phase_i tests, 96.12% line+branch coverage.

## 5. Top 10 most path-reference-sensitive couplings (most likely to break on move)
1. `config/settings/base.py` `BASE_DIR = parents[2]` → becomes `backend/` after moving `config/` under `backend/` (all derived paths follow: `.env`, fixtures, templates, static/media).
2. pytest rootdir: `pyproject.toml` moves to `backend/`; `testpaths=["tests"]` and all `--cov-config=.coveragerc-*` and `--cov=apps/...` args must resolve from `backend/`.
3. Makefile: every `$(UV) run ...` currently runs from repo root; must run from `backend/` (uv `--directory backend` or `cd backend`).
4. Workflows: no `working-directory` today; `uv sync`, `mypy apps config`, `pytest`, `manage.py`, `loadtests/smoke_gate.py`, `scripts/extract_ddl.py`, `ci/*.sql` all root-relative — need `defaults.run.working-directory: backend` + adjusted `paths:` filters (`apps/**`→`backend/apps/**` etc.).
5. `scripts/extract_ddl.py` default doc path `docs/CRICKET_DATABASE_SCHEMA.md` (called with no arg by two workflows) — if script moves to `backend/scripts/ci/extract_ddl.py`, the default must become `../../docs/...` or an explicit arg.
6. `tests/phase_i/test_security.py` subprocess `cwd=ROOT` + `SECRET_SCAN/DAST_SCAN` paths into `scripts/security/`.
7. `ops.alerts.local_sink` imported as a package by tests — if `ops/` moves to `infrastructure/`, the import mechanism must change (load-by-path, `spec_from_file_location`) and `RUNBOOKS` root path must be fixed.
8. `scripts.game_day.common` / `scripts.security` / `loadtests.safety` namespace-package imports across tests + scripts (all resolve from `backend/` once moved there together).
9. `tools/reference_client` consumed by `scripts/consumer_ladder.py` and `tests/phase_e/test_reference_client.py` via parents-based ROOT + sys.path inserts.
10. Docker: root `Dockerfile` + `docker-compose.yml` build contexts/volumes reference repo-root layout; moving to `infrastructure/` requires `COPY backend/...` + compose `context: ..` adjustments. Import-linter `root_packages=["apps"]`, mypy `apps config`, ruff `check .` — all must still target the same packages from `backend/`.
