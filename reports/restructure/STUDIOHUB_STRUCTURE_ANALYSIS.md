# StudioHub Repository Structure & Conventions Analysis

**Repo analyzed:** `/home/prafull.sakharkar/Repository/github/studiohub` (git@github.com:prafullsakharkar/studiohub.git)
**Method:** read-only; based on `git ls-files` (3,680 tracked files), `git status --porcelain` (names only), and contents of tracked configs/docs/representative files. No `.env*` contents were read (names only). No secrets/ignored paths read.
**Purpose:** blueprint for restructuring a Django backend repo (cricket_os) to match StudioHub's LAYOUT and CONVENTIONS — not its code/content.

---

## 0. Executive summary (the 6 most important conventions for a Django repo to mirror)

1. **Stack-siloed monorepo at top level** — `backend/`, `frontend/`, `infrastructure/`, `docs/`, `scripts/`, `.github/` each own their entire concern. No cross-cutting top-level dirs (no top-level `tests/`, `src/`, `config/`, `docker/`, `ci/`). CI lives under `.github/workflows/`, Docker/compose under `infrastructure/`, docs in one root `docs/` tree.
2. **Each sub-project is a self-contained package** — `backend/` carries its own `pyproject.toml` + `uv.lock` + `ruff.toml` + `.python-version` + `manage.py` + `conftest.py` + `.env.example` + `.env.test`; `frontend/` carries `package.json` + `pnpm-lock.yaml` + `pnpm-workspace.yaml`. Dependency manifests and lockfiles are **per sub-project, never hoisted to root**. Root has only a `Makefile` (no root pyproject/package.json).
3. **Django modular-monolith layout** — `backend/apps/<domain>/` where each domain app is a strict layered package: `api/{viewsets,views,serializers,filtersets,routers,urls*.py}`, `services/`, `selectors/`, `managers/`, `querysets/`, `models/`, `validators/`, `events/`, `signals/`, `permissions/`, `tasks/`, `choices/`, `constants/`, `exceptions/`, `management/commands/`, and **colocated `tests/`** mirroring the package tree. `apps/core` is the shared kernel. Project-level Django config is `config/settings/{base,local,docker,testing,production,ci}.py` + `config/settings/components/*.py`, with `config/env.py` as the *only* env-var reader.
4. **Infra is centralized under `infrastructure/`** — `infrastructure/compose/compose{.dev,.prod,.test}.yml` (layered compose overlays), `infrastructure/docker/<service>/` (Dockerfiles + nginx configs + entrypoint/start scripts), `infrastructure/scripts/`, `infrastructure/k6/`. The root `Makefile` fans out to docker compose (`COMPOSE := docker compose -f infrastructure/compose/compose.yml -f infrastructure/compose/compose.dev.yml`) and `docker compose exec django ...` for every backend command. Dockerfiles use **repo root as build context** and copy `backend/`.
5. **Single docs/ tree, numbered topics + ADRs + dated design docs** — `docs/NN-<topic>/` (01-getting-started … 16-data-platform), `docs/adr/ADR-NNNN-<kebab-slug>.md`, `docs/superpowers/{plans,specs}/YYYY-MM-DD-<slug>.md`, Mermaid diagrams as `.mmd` + `.md` pairs in `docs/09-diagrams/`, indexed by `docs/README.md` + `docs/SUMMARY.md`. Docs are considered part of the implementation.
6. **CI is root-level with per-job `working-directory:` + `paths:` filters** — `.github/workflows/{ci,containers,dependency-audit}.yml`; the backend job sets `defaults.run.working-directory: backend` and runs `uv sync --frozen` / `uv run ruff check .` / `uv run pytest -q --cov=apps`; container/dependency jobs gate on `paths: ["infrastructure/**", "backend/uv.lock", "backend/pyproject.toml", ...]`. Env files are per sub-project (`.env.example` at root, `backend/`, `frontend/`, `infrastructure/compose/`).

---

## 1. Top-level tree (depth 3, from `git ls-files`; `.git`, caches, node_modules excluded)

```
studiohub/
├── .github/
│   ├── CODEOWNERS
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml                      # backend + frontend + secrets + docs jobs
│       ├── containers.yml              # Trivy image & misconfig scan
│       └── dependency-audit.yml        # pip-audit on locked runtime deps
├── .opencode/
│   └── AGENTS.md                       # local-AI coding rules (model/memory/tooling)
├── .serena/
│   ├── project.yml                     # Serena LSP/agent project config
│   └── memories/                       # agent memory notes (kebab-case, dated subdirs)
├── .vscode/
│   └── settings.json
├── AGENTS.md                           # root agent rules (authoritative)
├── CLAUDE.md                           # Claude Code guidance (notes AGENTS.md drift)
├── Makefile                            # root dev entry point (docker-compose fan-out)
├── SECURITY.md                         # vuln reporting policy
├── scripts/
│   └── check_docs_links.py             # docs link checker used by CI docs job
├── .dockerignore                       # root-level; excludes **/.env* etc for repo-root build contexts
├── .env.example                        # root env example (repo-wide vars, e.g. compose)
├── .gitleaks.toml                      # secret-scan allowlist (test-only paths)
├── .gitignore                          # polyglot: py/js/ts/docker/env/terraform/... in one file
├── .trivyignore.yaml                   # accepted container misconfigs w/ expiry + reason
├── backend/                            # Django 6 / DRF modular monolith (2,035 files)
│   ├── .ai/                            # ARCHITECTURE/CODING_GUIDELINES/DEVELOPMENT_WORKFLOW/OPENCODE_TOOLS/PROJECT_RULES
│   ├── .env.example
│   ├── .env.test                       # gitleaks-allowlisted (test-only credentials)
│   ├── .python-version                 # "3.13.5"
│   ├── AGENTS.md                       # backend agent rules
│   ├── conftest.py                     # pytest/Django bootstrap + shared fixtures
│   ├── manage.py
│   ├── pyproject.toml                  # uv project: deps + dev group + tool config (ruff/black/basedpyright/pytest/coverage/hatch)
│   ├── ruff.toml
│   ├── uv.lock
│   ├── apps/                           # one Django app per domain
│   │   ├── core/          (shared kernel)
│   │   ├── identity/      (users/auth/RBAC/MFA/security)
│   │   ├── settings/      (system/theme/feature-flag settings)
│   │   ├── audit/         (audit/activity/error logs)
│   │   ├── production/    (projects/shots/sequences/tasks/versions/reviews)
│   │   ├── deliveries/
│   │   ├── publishing/
│   │   ├── scheduling/
│   │   ├── masterdata/    (catalogs/partners/directory)
│   │   └── notifications/
│   ├── config/                         # Django project package
│   │   ├── settings/                   # base.py + env modules + components/ (17 files)
│   │   ├── api_urls.py / v1_urls.py / urls.py
│   │   ├── asgi.py / wsgi.py / celery.py / test_runner.py
│   │   ├── env.py                      # ONLY module reading env vars (pydantic-settings)
│   │   └── secrets.py
│   ├── core/                           # celery app + shared tasks
│   ├── docs/                           # backend-local docs (2-architecture/, superpowers/)
│   └── scripts/
│       └── reset_and_bootstrap.sh
├── frontend/                           # React 19 + Vite + TS + pnpm SPA (819 files)
│   ├── .env.example
│   ├── .github/workflows/ci.yml        # legacy per-stack CI (duplicate of root CI frontend job)
│   ├── AGENTS.md / CLAUDE.md
│   ├── biome.json / tsconfig.json
│   ├── vite.config.ts / vitest.config.ts / vitest.live.config.ts / vitest.setup.ts
│   ├── playwright.config.ts / playwright.live.config.ts
│   ├── package.json / pnpm-lock.yaml / pnpm-workspace.yaml
│   ├── index.html / server.ts / metadata.json
│   ├── e2e/                            # Playwright spec files + live/ (manual smoke)
│   ├── scripts/                        # generate-api-contract.mjs, validate-api-contract.mjs, ...
│   ├── docs/                           # frontend docs (UPPER_SNAKE docs, superpowers/, refactoring/)
│   └── src/
│       ├── api/<domain>/               # typed API client per backend domain
│       ├── components/ (forms,history,system,ui)  core/  features/  layouts/
│       ├── modules/<domain>/           # feature modules per domain
│       ├── pages/  providers/  routes/  shared/  test/  types/
├── docs/                               # single root docs tree (757 files)
│   ├── 01-getting-started/ … 16-data-platform/   # numbered topic dirs
│   ├── adr/                            # ADR-0001 … ADR-0041
│   ├── api/ apps/ architecture/ database/ identity/ masterdata/ production/ …
│   ├── superpowers/                    # plans/ + specs/ (dated design docs)
│   ├── sync/                           # frontend↔backend contract work (samples/, tools/)
│   ├── README.md                       # docs index + architecture summary + stack
│   └── SUMMARY.md                      # full doc index (mkdocs-style)
└── infrastructure/                     # all Docker/IaC/ops (29 files)
    ├── .dockerignore
    ├── compose/
    │   ├── compose.yml                 # base (postgres, redis, networks, volumes)
    │   ├── compose.dev.yml             # dev overlay (django, nginx, celery*, flower, mailpit)
    │   ├── compose.prod.yml            # prod overlay (TLS init, nginx, django, celery*, postgres/redis TLS)
    │   ├── compose.test.yml            # test overlay
    │   └── .env.example
    ├── docker/
    │   ├── django/                     # Dockerfile (+worker target) + entrypoint.sh + start-*.sh
    │   ├── nginx/                      # Dockerfile + default.conf + nginx.conf + security-headers.conf
    │   ├── postgres/                   # Dockerfile + healthcheck.sh + init.sql + pg_hba.prod.conf
    │   ├── redis/                      # redis.conf + redis.prod.conf
    │   └── tls/                        # init.sh (internal CA bootstrap)
    ├── k6/
    │   └── production.js               # load test
    └── scripts/
        ├── backup.sh
        ├── check-encryption-at-rest.sh
        └── github-settings.sh
```

### Notable untracked items (names only; from `git status --porcelain`)
- `spikes/` (untracked dir, contains `grid-bench/` with `node_modules/` — a Playwright benchmark spike)
- `scripts/ATOM API.yaml`
- `docs/sync/phase-34-verification.md`
- `frontend/src/test/live/newScreens.live.test.tsx`
- Several modified tracked files (working tree in-progress work on review-pipeline RP-16), e.g. `backend/apps/production/...`, `docs/review-pipeline/...`, `frontend/src/modules/production/review/...`. (Reported names only; no contents reviewed.)

---

## 2. Per top-level area: purpose & contents

### `backend/` — Django modular monolith (the model for a Django-only repo)
- One Django **app per business domain** under `apps/` (`core`, `identity`, `settings`, `audit`, `production`, `deliveries`, `publishing`, `scheduling`, `masterdata`, `notifications`). `apps/core` is the generic shared kernel (base models, services, selectors, event bus, pagination, permissions, cache) and must never import business domains.
- Per-app package layout is rigid (see §3.7). Reference implementations cited by the repo: `apps/identity`, `apps/settings`, `apps/audit` (CLAUDE.md notes `apps/organization` no longer exists despite being cited in AGENTS.md — a documented doc-drift).
- `config/` is the Django project package: `settings/` split into `base.py` (assembles everything) + per-env modules (`local`, `docker`, `testing`, `production`, `ci`) + `components/<area>.py` (apps, auth, cache, celery, cors, database, drf, email, internationalization, jwt, logging, middleware, notifications, security, static, storage, templates, audit). `env.py` is the only env reader; `DJANGO_ENV` selects both settings module and `.env.*` file.
- `backend/conftest.py` at package root bootstraps pytest (sets `DJANGO_ENV`/settings before imports), plus shared autouse fixtures (cache clearing, seeded-platform fixtures). There is **no top-level `backend/tests/`** — all 519 test files live inside each app's `tests/`.
- `pyproject.toml` is the single Python manifest (deps, `[dependency-groups] dev`, tool configs: black, basedpyright, pytest, coverage, hatch build, `[tool.uv]`). `uv.lock` is committed and authoritative (CI: `uv sync --frozen`; Docker: `uv sync --frozen --no-install-project --no-dev`).
- `backend/.ai/*.md` = architecture/rules docs for AI agents; `backend/docs/` = backend-local working docs; `backend/scripts/reset_and_bootstrap.sh` = db reset helper.

### `frontend/` — React SPA (would be omitted for a Django-only repo)
- Self-contained Node project: `package.json` (name `studiohub-react`, private), `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `biome.json` (linter), `vite.config.ts`, `vitest*.config.ts`, `playwright*.config.ts`, `tsconfig.json`, `index.html`, `server.ts` (dev/prod express server).
- `src/` mirrors the backend domains: `src/api/<domain>/` (typed API client), `src/modules/<domain>/` (feature modules), plus cross-cutting `src/core/`, `src/shared/`, `src/components/`, `src/layouts/`, `src/routes/`, `src/pages/`, `src/providers/`, `src/test/` (mocks/samples/live), `src/types/`.
- `e2e/` = Playwright end-to-end tests (real backend only), with `e2e/live/` for manual browser smoke tests. `src/test/live/*.live.test.tsx` = live-backend vitest files.
- `frontend/docs/` = frontend-specific docs (mostly UPPER_SNAKE doc files + `superpowers/` + `refactoring/`).
- **Quirk:** `frontend/.github/workflows/ci.yml` exists as a per-stack duplicate of the root CI's frontend job (uses un-pinned action versions, `cache: pnpm`). Root `.github/workflows/ci.yml` is the operative one.

### `infrastructure/` — everything Docker/ops (the model for a Django-only repo)
- **Compose files** in `infrastructure/compose/`: base `compose.yml` (postgres, redis, networks/volumes) + overlays `compose.dev.yml`, `compose.prod.yml`, `compose.test.yml`. Compose `name: vfx-platform`. Services defined: postgres, redis, django, nginx, celery, celery-beat, celery-media, flower (monitoring profile), mailpit, tls-init (one-shot). Build contexts point at the **repo root** (`context: ../..`, `dockerfile: infrastructure/docker/<svc>/Dockerfile`).
- **Dockerfiles + runtime config per service** in `infrastructure/docker/<service>/`: `django/` (multi-stage build with `worker` target; entrypoint + `start-*.sh` process scripts), `nginx/` (Dockerfile + `nginx.conf`, `default.conf`, `security-headers.conf`), `postgres/` (healthcheck, init.sql, prod pg_hba), `redis/` (dev + prod confs), `tls/` (internal CA init script).
- `infrastructure/scripts/` = ops helper scripts (backup, encryption check, GitHub settings). `infrastructure/k6/production.js` = load test.
- `infrastructure/.env.example` = compose-level env example. **No Kubernetes manifests, no Terraform/Ansible tracked** (`.gitignore` has placeholders for `.terraform/`, `.kube/` etc. but they are unused — infra is compose-only).

### `docs/` — single root documentation tree
- Numbered topic dirs `NN-<kebab-topic>/`: 01-getting-started, 02-architecture, 02-product, 03-backend, 03-development, 03-domain, 04-architecture, 04-frontend, 05-api, 06-infrastructure, 07-deployment, 07-pipeline, 08-development, 08-review-collaboration, 09-diagrams, 09-testing, 10-security, 11-operations, 12-reference, 13-roadmap, 14-commercial, 15-workforce, 16-data-platform.
- Domain docs also under `docs/{identity,production,masterdata,deliveries,publishing,scheduling,review-pipeline,apps}/`; contract/sync docs under `docs/sync/` (with `samples/` of captured API responses and `tools/` capture scripts); `docs/superpowers/{plans,specs}/` for dated design documents.
- `docs/adr/ADR-NNNN-<kebab-slug>.md` (ADR-0001…ADR-0041) with a standard front-matter template (Status/Date/Decision Makers/Supersedes). `docs/README.md` and `docs/SUMMARY.md` are the indexes. `docs/APP_ARCHITECTURE_TEMPLATE.md` is the mandatory template for any new app.
- `docs/09-diagrams/` keeps Mermaid sources `.mmd` + rendered `.md` pairs.

### `scripts/` — repo-root helper scripts
- Small: a single `scripts/check_docs_links.py` (run by the CI docs job with `uv run`). This is where *repo-level* utility scripts go (as opposed to `infrastructure/scripts/` for ops, `backend/scripts/` for backend-local, `frontend/scripts/` for frontend-local). Note: an untracked `scripts/ATOM API.yaml` exists in the working tree.

### `.github/` — CI/tooling
- `workflows/ci.yml` (main), `workflows/containers.yml`, `workflows/dependency-audit.yml` — see §3.4. `CODEOWNERS`, `dependabot.yml` (5 ecosystems: uv `/backend`, npm `/frontend`, github-actions `/`, docker-compose `/infrastructure/compose`, docker for each of `/infrastructure/docker/{django,nginx,postgres}`).

### Root-level files
- `Makefile` (see §3.9), `AGENTS.md`, `CLAUDE.md`, `SECURITY.md`, `.gitignore`, `.dockerignore`, `.env.example`, `.gitleaks.toml`, `.trivyignore.yaml`, `.vscode/settings.json`, `.opencode/AGENTS.md`, `.serena/` (project.yml + memories).
- **Notably absent:** no root `README.md` (docs/README.md is the entry point), no root `pyproject.toml`/`package.json`, no `CONTRIBUTING.md` (contributing guide lives at `docs/08-development/contributing-guide.md`), no `.editorconfig`, no committed `.pre-commit-config.yaml` (pre-commit is declared in the dev dependency group but not configured), no `justfile`/`Taskfile`, no `LICENSE` (proprietary per pyproject).

---

## 3. Conventions (each with example paths)

### 3.1 Naming
- **Top-level dirs: lowercase singular** — `backend/`, `frontend/`, `docs/`, `scripts/`, `infrastructure/` (not `infra/`).
- **Backend:** Python files `snake_case`; app dirs single lowercase word (`apps/identity`, `apps/audit`); layered packages are **plural** (`services/`, `selectors/`, `managers/`, `querysets/`, `validators/`, `tasks/`, `signals/`, `choices/`, `constants/`, `exceptions/`, `permissions/`, `filters/`, `filtersets/`); test files `test_<area>.py` or `test_<area>_<unit>.py` under mirror dirs (`tests/models/test_user_models.py`, `tests/services/test_rbac_services.py`, `tests/api/viewsets/test_audit_viewsets.py`). Migrations: descriptive snake_case (`0003_project_scope_and_append_only.py`).
- **Frontend dirs: kebab-case** (`frontend/src/api/production`, `src/modules/review-sessions` style); component files PascalCase (`ReviewMoviePanel.tsx`).
- **Docs:** topic dirs `NN-kebab` (`10-security`), ADRs `ADR-NNNN-kebab-slug.md` (`ADR-0040-review-media-processing.md`), superpowers `YYYY-MM-DD-kebab-slug.md` (`docs/superpowers/specs/2026-10-20-identity-phase1-revocation-hardening-design.md`), frontend-local docs UPPER_SNAKE (`frontend/docs/frontend/TESTING.md`), memory notes kebab with date-prefixed subdirs (`.serena/memories/production/project-filter-bug-fix.md`).
- **Doc case conventions:** root-level guidance files are ALL-CAPS names (`AGENTS.md`, `CLAUDE.md`, `README.md`, `SECURITY.md`); topic docs are lowercase-kebab.
- **Branching:** `main` + `develop` (CI triggers on both) + `feature/*`, `fix/*`, `archive/*`, `design/*`, `analysis/*` prefixes seen in `git branch`.

### 3.2 Where infra lives
- **Compose:** `infrastructure/compose/compose.yml` + `.dev`/`.prod`/`.test` overlays (layered merge, never one big file).
- **Dockerfiles:** `infrastructure/docker/<service>/Dockerfile` (django, nginx, postgres, redis). Build context = repo root; `.dockerignore` at root.
- **nginx/proxy configs:** `infrastructure/docker/nginx/` (`nginx.conf`, `default.conf`, `security-headers.conf`).
- **IaC:** none tracked (compose is the deployment mechanism). Placeholders only in `.gitignore` (`.terraform/`, `*.tfstate`, `.kube/`, `*.retry`).
- **Kubernetes/deployment manifests:** none. Deploy = `docker compose -f … -f compose.prod.yml up` + manual `migrate`. One-shot jobs (e.g. `tls-init`) are compose services with `restart: "no"`.

### 3.3 CI workflows & helper scripts
- All workflows in `.github/workflows/` (root). Helper scripts are committed Python/shell under `scripts/`, `docs/sync/tools/`, `infrastructure/scripts/`, `backend/scripts/`, `frontend/scripts/` and invoked by workflows.
- Workflow conventions: pin actions by **commit SHA** with a comment (`uses: actions/checkout@3d3c42e5… # v7.0.1`); least-privilege `permissions: contents: read`; `concurrency: group: ci-${{ github.ref }}` + `cancel-in-progress: true`; jobs named `backend:`, `frontend:`, `secrets:`, `docs:`.
- Real examples of sub-project path handling:

```yaml
# .github/workflows/ci.yml — backend job
jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend        # <-- sub-project working dir at job level
    services:                             # postgres:18 service container
      postgres: { image: postgres:18, ports: [5432:5432], options: --health-cmd "pg_isready -U studiohub" ... }
    env:
      DJANGO_ENV: testing
      DJANGO_SETTINGS_MODULE: config.settings.testing
      DJANGO_SECRET_KEY: ci-only-insecure-secret-key
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { persist-credentials: false }
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3
        with: { python-version: "3.13" }
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run python manage.py check
      - run: uv run python manage.py makemigrations --check
      - run: uv run pytest -q --cov=apps

# .github/workflows/containers.yml — path filters gate infra jobs
on:
  push:
    branches: [main, develop]
    paths:
      - "infrastructure/**"
      - "backend/uv.lock"
      - "backend/pyproject.toml"
      - ".dockerignore"
      - ".trivyignore.yaml"
      - ".github/workflows/containers.yml"
  schedule:
    - cron: "41 5 * * 1"

# .github/workflows/dependency-audit.yml — step-level working-directory
      - name: Export the locked runtime dependencies
        working-directory: backend
        run: >-
          uv export --frozen --no-dev --no-emit-project --format requirements-txt
          --output-file "$RUNNER_TEMP/runtime-requirements.txt"
```
- Cache keys / artifacts: root CI does **not** use cache keys (uv/pnpm resolve from lockfiles; `setup-node` without cache in the root workflow); the legacy `frontend/.github/workflows/ci.yml` uses `cache: pnpm`. Artifacts pass through `RUNNER_TEMP` (e.g. `docker save … -o "$RUNNER_TEMP/studiohub-django.tar"` then `docker run … trivy image --input /image.tar`). Trivy is pinned by digest in `env.TRIVY_IMAGE`.
- Scheduled runs for vulnerability scans (`schedule: cron` weekly) + `workflow_dispatch` on containers.

### 3.4 Env-file conventions
- `.env.example` exists at **multiple levels**: root, `backend/`, `frontend/`, `infrastructure/compose/`. Real env files are git-ignored.
- `.gitignore` pattern: `.env` and `.env.*` ignored, with negations `!.env.example`, `!.env.template`, `!.env.sample` (repo-wide). `.dockerignore` excludes `**/.env` and `**/.env.*` so secrets never enter images.
- Per-environment files are named `.env.<env>` and selected by `DJANGO_ENV`: `backend/config/env.py` maps `local → .env.local`, `docker → .env.docker`, `testing → .env.test`, `production → .env.production`, `ci → .env.ci`. `backend/.env.example` + `backend/.env.test` are the only env files committed. Compose services load env via `env_file:` (`.env` at compose level, or `../../backend/.env.local` for dev celery services).
- `backend/config/env.py` is the single env-var reader (pydantic-settings, `SecretStr` for secrets); settings modules import a `settings` object from it.

### 3.5 Dependency manifests & lockfiles
- **Per sub-project, never root:** `backend/pyproject.toml` + `backend/uv.lock` (Python/uv; `[tool.uv.workspace] members = ["backend"]`), `frontend/package.json` + `frontend/pnpm-lock.yaml` + `frontend/pnpm-workspace.yaml` (Node/pnpm 10, `allowBuilds` for esbuild/msw). No root manifest of either kind.
- Package managers: **uv** (Python 3.13; `uv sync --frozen` in CI/Docker; `uv run` for every command — system Python is banned), **pnpm** for the frontend. Dockerfiles install deps from the lockfile (`COPY backend/pyproject.toml backend/uv.lock ./` then `uv sync --frozen --no-install-project --no-dev`).
- Dev tooling declared in `[dependency-groups] dev` (pytest, factory-boy, ruff, black, basedpyright, django-stubs, pre-commit, ipython).
- Dependabot configured per ecosystem + directory (uv `/backend`, npm `/frontend`, docker-compose `/infrastructure/compose`, docker for each service dir) with grouped minor/patch PRs.

### 3.6 Root-level files and their roles
- `Makefile` — the single dev entry point; `PROJECT_NAME := vfx-platform`; groups targets with banner comments (`Docker`, `Django`, `Database`, `Tests`, `Lint`, `Production`, `Cleanup`); `.DEFAULT_GOAL := help` prints a curated target list.
- `AGENTS.md` — authoritative agent rules (priority: user → AGENTS.md → backend/frontend AGENTS.md → `.ai/` → ADRs → existing code). `CLAUDE.md` documents current repo state and explicitly flags where `AGENTS.md` is stale.
- `SECURITY.md` — private vuln reporting, supported branch (`main`), what to expect.
- `.github/CODEOWNERS` — last-match-wins patterns; owners for security/infra/CI paths (e.g. `/backend/apps/identity/`, `/infrastructure/`, `/docs/adr/`, `/backend/uv.lock`).
- `.gitleaks.toml` — secret-scan allowlist (only test-only paths: `backend/.env.test`, `backend/config/settings/testing.py`). `.trivyignore.yaml` — accepted container misconfigs with statement + `expired_at` date.
- `.vscode/settings.json` — black/flake8 (line-length 92 — note: differs from ruff's 100), pytest args pointing at `backend`.
- No pre-commit config, no `.editorconfig`, no CODEOWNERS outside `.github/`, no root README.

### 3.7 Docs layout
- **Single root `docs/` tree** (primary) + **per-package docs** for stack-local material: `backend/docs/` (architecture audits, backend superpowers plans/specs), `frontend/docs/` (frontend standards + refactoring records). The root tree is the canonical home for cross-cutting topics (getting-started, architecture, API, infra, deployment, development standards, testing, security, operations, reference, roadmap, ADRs).
- **ADR/decision records:** `docs/adr/ADR-NNNN-<kebab-slug>.md` with template front-matter (Status, Date, Decision Makers, Supersedes, Superseded By), Context/Decision/Rationale/Alternatives/Consequences/Implementation Notes/Related ADRs/References. Superseded ADRs are kept and cross-linked (`ADR-0005` → superseded by `ADR-0030`).
- **Runbooks:** `docs/11-operations/runbooks.md` (+ operational-checklists, backup-recovery, disaster-recovery, incident-response in `10-security`).
- **Architecture diagrams:** `docs/09-diagrams/` Mermaid `.mmd` + `.md` pairs (`system-overview.mmd` / `system-overview.md`), `mermaid-instructions.md` explains diagram conventions.
- **Design docs (dated):** `docs/superpowers/plans/YYYY-MM-DD-<slug>.md` (plan) + `docs/superpowers/specs/YYYY-MM-DD-<slug>-design.md` (design) — also present under `backend/docs/superpowers/` and `frontend/docs/superpowers/`.
- **Indexes:** `docs/README.md` (structure + overview + stack + standards) and `docs/SUMMARY.md` (complete file-by-file index). `docs/APP_ARCHITECTURE_TEMPLATE.md` is the mandatory template for new apps; new domain docs are expected there too.

### 3.8 Test layout
- **Backend: colocated per app** — `backend/apps/<domain>/tests/` mirroring the package layout (`tests/models/`, `tests/services/`, `tests/selectors/`, `tests/managers/`, `tests/querysets/`, `tests/api/viewsets/`, `tests/api/serializers/`, `tests/api/filtersets/`, `tests/security/`, `tests/performance/`, `tests/integration/`, `tests/validators/`, `tests/resolvers/`). Each `tests/` has its own `conftest.py`, `factories.py` (factory-boy), `fixtures.py`, and `*_helpers.py`. Root-level `backend/conftest.py` holds global fixtures. pytest configured in `pyproject.toml` (`testpaths = ["apps"]`, `DJANGO_SETTINGS_MODULE=config.settings.testing`, `--reuse-db --strict-markers --import-mode=importlib`).
- **Frontend:** colocated `*.test.{ts,tsx}` under `src/` (next to code), plus `src/test/` for shared test utils/mocks/samples, `src/test/live/*.live.test.tsx` for live-backend tests (excluded from default vitest run), and `frontend/e2e/` for Playwright end-to-end (with `e2e/live/` for manual role smoke tests that need a seeded backend).
- **No backend e2e suite**; end-to-end coverage is the frontend Playwright suite against the real Django API.

### 3.9 Polyglot tooling
- Single root `Makefile` fans out to sub-projects **through docker compose** (not per-subproject Makefiles — there are none):
  - `COMPOSE := docker compose -f infrastructure/compose/compose.yml -f infrastructure/compose/compose.dev.yml`
  - `DJANGO := $(COMPOSE) exec django`, `PYTHON := $(DJANGO) python`, `MANAGE := $(PYTHON) manage.py`
  - Targets: `build/up/down/restart/logs/ps/shell` (Docker), `migrate/makemigrations/createsuperuser/collectstatic/shellplus` (Django), `dbshell` (DB), `test/test-full/pytest/coverage` (pytest via `$(COMPOSE) exec django pytest`), `lint/format/typecheck/check` (ruff + basedpyright in-container), `prod-build/prod-up/prod-down` (prod overlay), `clean/clean-all/reset-db`.
  - Naming: short verb nouns (`build`, `up`, `down`, `migrate`, `lint`, `test`, `check`); grouped by banner sections; `prod-*` prefix for production variants; `clean`/`clean-all`/`reset-db` for teardown.
- The frontend is *not* wired into the Makefile or compose (by design; CLAUDE.md documents this) — it uses its own `pnpm` scripts.
- CI is the other fan-out mechanism: one workflow with one job per stack (`backend`, `frontend`, `secrets`, `docs`).

---

## 4. Things that do NOT apply to a single-language (Python/Django) backend

| StudioHub area | Apply to Django-only repo? | What a Django-only repo should do instead |
|---|---|---|
| `frontend/` (React SPA, pnpm, Vite, Biome, vitest, Playwright) | Omit | Drop entirely. Keep the *slot* in mind if an API client is ever added; don't create placeholders. |
| `frontend/.github/workflows/ci.yml` (per-stack CI duplicate) | Omit | One root `.github/workflows/`; single `ci.yml` with per-job `working-directory:` for sub-projects. |
| `frontend/docs/`, `docs/04-frontend/`, `docs/sync/` (frontend↔backend contract) | Omit | API docs belong in `docs/05-api/` + `docs/adr/`. |
| `infrastructure/docker/nginx/`, `infrastructure/k6/`, `infrastructure/docker/tls/` | Omit unless needed | Keep `infrastructure/docker/<svc>/` pattern only for services that exist (e.g. `django/`, `postgres/`). |
| Celery/flower/celery-media worker targets, mailpit, redis dev overlay | Conditional | Only include compose services the Django app actually uses. Compose stays layered (`compose.yml` + `.dev`/`.prod`/`.test` overlays). |
| `.serena/` (agent memories), `.opencode/AGENTS.md` | Optional | Agent-rule files (`AGENTS.md`, `CLAUDE.md`) are worth keeping; `.serena`/`.opencode` are tool-specific and can be omitted. |
| `backend/.ai/` docs, `docs/superpowers/`, `backend/docs/superpowers/` | Optional | Keep the *convention* if the team uses AI-assisted planning: `docs/superpowers/{plans,specs}/YYYY-MM-DD-*.md`. |
| `frontend/src/*` structure, `src/test/live`, `e2e/` | Omit | If browser E2E is ever needed, colocate under a per-stack dir (e.g. `<stack>/e2e/`) rather than a root `e2e/`. |
| `docs/02-product/`, `docs/03-domain/`, `docs/14-commercial/`, `docs/15-workforce/`, `docs/16-data-platform/`, `docs/07-pipeline/`, `docs/08-review-collaboration/` | Omit | Domain/product docs: use `docs/03-domain/` + `docs/03-backend/` slots only if the domain warrants them; otherwise keep numbered topics minimal (01–12). |
| `frontend/metadata.json`, `frontend/biome.json`, `pnpm-workspace.yaml` | Omit | N/A. |
| Root `Makefile` using `docker compose exec django` | **Keep — core convention** | The root Makefile fan-out pattern is the right model: define `COMPOSE`, `DJANGO`, `MANAGE` variables and short verb targets. |

**Core set a Django-only repo should adopt in full:** `backend/` (apps/ + config/ + pyproject/uv.lock/ruff.toml/conftest.py/manage.py), `infrastructure/compose` + `infrastructure/docker/<svc>/`, `docs/` (numbered topics + `adr/` + `README.md`/`SUMMARY.md`), `scripts/`, `.github/workflows/` + `CODEOWNERS` + `dependabot.yml`, root `Makefile`, root `AGENTS.md`/`CLAUDE.md`/`SECURITY.md`, root `.gitignore`/`.dockerignore`/`.env.example`/`.gitleaks.toml`/`.trivyignore.yaml`.

---

## 5. Ambiguities ("unclear") with best inference

1. **`apps/organization` referenced in AGENTS.md/ADR but absent** — AGENTS.md and ADR-0001 name `organization` as the reference app; the current tree has `masterdata`, `settings`, `audit` instead, and CLAUDE.md explicitly documents this drift. Inference (high confidence): AGENTS.md/ADR lag the code; a Django repo should treat the **layered per-app package convention** (identity/settings/audit layout) as normative, not the app names.
2. **`frontend/.github/workflows/ci.yml` duplication** — a second CI file inside `frontend/` duplicates the root frontend job, using un-pinned actions and `cache: pnpm`. Inference (high confidence): legacy artifact from the merged sibling repo; root CI is authoritative. A Django-only repo should keep exactly one `.github/workflows/`.
3. **Root `.env.example` vs `infrastructure/compose/.env.example` vs per-stack `.env.example`** — three committed env-example locations. Inference (medium-high): the rule is "example env next to where the env file is consumed" (compose reads `.env` from `infrastructure/compose/`; backend reads `.env.local`/`.env.test` from `backend/`). A Django-only repo should commit `backend/.env.example` (+ `.env.test` for CI) and, if compose is used, `infrastructure/compose/.env.example`.
4. **`pre-commit` listed as a dev dependency but no `.pre-commit-config.yaml` committed** — Inference (medium): either run elsewhere or unused; do not copy. A Django repo should rely on CI (ruff/pytest gates) like StudioHub does.
5. **`docs/` has overlapping/duplicate topic dirs** (e.g. `02-architecture` vs `04-architecture`, `03-backend` vs `03-development`, `docs/api/` vs `docs/05-api/`) — the numbered tree is the canonical one (per SUMMARY.md modernization checkpoints), older unnumbered dirs are legacy. Inference (medium): adopt the **numbered** convention and avoid duplicate topic dirs from day one.
6. **Line-length discrepancy** — `.vscode/settings.json` says black line-length 92; `pyproject.toml`/`ruff.toml` say 100. Inference (high): ruff's 100 is the operative lint gate (CI runs ruff). Use 100.
7. **Backend `core/` package at `backend/core/` (celery) vs `backend/apps/core/` (shared kernel)** — two distinct "core" locations. Inference (high): `apps/core` is the Django app/shared kernel; `backend/core/` is the project-level celery app. A Django repo should keep celery app at `<project>/core/` and shared kernel at `apps/core/`.
8. **`docs/sync/samples/` (captured API responses) is a build input for frontend tests** — Inference (medium): this is contract-test tooling specific to the frontend migration; a Django-only repo does not need it (its API docs/contracts live in `docs/05-api/`).
9. **Untracked `spikes/` and `scripts/ATOM API.yaml`** — working-tree-only, not committed; Inference (low-medium): transient experiments; the repo's convention for experiments is a `spikes/` dir that is **not** tracked/ignored — unclear whether it should be gitignored.
10. **`Makefile` `PROJECT_NAME := vfx-platform` vs repo name studiohub** — Inference (high): compose project/name labels predate the current product name; harmless. Use one consistent `PROJECT_NAME` in a Django repo.
11. **Python version floor** — Dockerfile builds with `python:3.14-slim` while `.python-version` is `3.13.5` and CI `setup-uv` uses `python-version: "3.13"`. Inference (medium): 3.13 is the floor; base image drifted ahead. Pin one version consistently.
12. **No root `README.md`** — the docs/README.md doubles as the repo front page. Inference (high): intentional. A Django-only repo can mirror this (docs/README.md) or add a short root README pointing at docs/ — mirroring StudioHub means no root README.

---

## 6. Quick reference: Django-repo target skeleton (derived, not StudioHub content)

```
<repo>/
├── .github/
│   ├── CODEOWNERS
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml                 # backend job (working-directory: backend) + secrets + docs
│       ├── containers.yml         # path-filtered on infrastructure/** + backend/uv.lock
│       └── dependency-audit.yml   # uv export | pip-audit
├── AGENTS.md / CLAUDE.md / SECURITY.md
├── Makefile                       # COMPOSE var + django exec fan-out targets
├── scripts/                       # repo-level helpers (docs link check, etc.)
├── .dockerignore  .env.example  .gitleaks.toml  .gitignore  .trivyignore.yaml
├── backend/
│   ├── apps/
│   │   ├── core/                  # shared kernel
│   │   ├── <domain>/              # api/{viewsets,views,serializers,filtersets,routers,urls.py}
│   │   │   ├── services/ selectors/ managers/ querysets/ models/ validators/
│   │   │   ├── events/ signals/ permissions/ tasks/ choices/ constants/ exceptions/
│   │   │   └── tests/             # mirrored tree + conftest.py + factories.py + fixtures.py
│   │   └── ...
│   ├── config/
│   │   ├── settings/{base,local,docker,testing,production,ci}.py
│   │   ├── settings/components/*.py
│   │   ├── env.py (only env reader)  urls.py  asgi.py  wsgi.py  celery.py
│   ├── conftest.py  manage.py  pyproject.toml  uv.lock  ruff.toml  .python-version
│   ├── .env.example  .env.test
│   └── scripts/
├── docs/
│   ├── 01-getting-started/ … NN-topic/
│   ├── adr/ADR-NNNN-<slug>.md
│   ├── 09-diagrams/ (mermaid .mmd + .md)
│   ├── superpowers/{plans,specs}/YYYY-MM-DD-*.md   (optional)
│   └── README.md  SUMMARY.md
└── infrastructure/
    ├── compose/compose.yml + compose.{dev,prod,test}.yml
    ├── docker/<service>/Dockerfile + configs + start scripts
    └── scripts/
```
