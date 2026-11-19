# Restructure Mapping: Cricket OS → StudioHub layout

**Source of truth for every move.** Each target cites the StudioHub convention it
follows (`STUDIOHUB_STRUCTURE_ANALYSIS.md`). Where StudioHub has no precedent, the
task's "expected shape" rule applies and is flagged **DECISION** with the default
used. Counts are from `git ls-files` (714 tracked files).

---

## 1. Target shape (what we are building)

```
cricket_os/
├── .editorconfig  .gitattributes  .gitignore  .dockerignore     (new: 2 generic)
├── .env.example                                                  (kept, root-level compose/doc)
├── .github/
│   ├── CODEOWNERS                                                (new, TODO-marked)
│   └── workflows/{ci,migration-drift,schema-ddl}.yml
├── Makefile  README.md
├── backend/                          ← all Python + its tooling
│   ├── apps/  config/  tests/  scripts/  loadtests/
│   ├── scripts/ci/                   ← was ci/
│   ├── manage.py  pyproject.toml  uv.lock  .python-version
│   ├── .env.example  .coveragerc-* (×5)  README.md (new)
├── clients/
│   └── reference_client/             ← was tools/reference_client/
├── docs/                             ← unchanged location (45 .md + superpowers/plans/)
├── infrastructure/
│   ├── compose/compose.yml           ← was docker-compose.yml
│   ├── docker/django/Dockerfile      ← was Dockerfile
│   ├── docker/nginx/nginx.conf       ← was ops/proxy/nginx.conf
│   ├── ops/alerts/                   ← was ops/alerts/ (Prometheus rules + local_sink.py)
│   └── README.md (new)
└── reports/                          ← unchanged location (evidence) + restructure/
```

All Python commands run from `backend/` (uv project root); Makefile fan-out and CI
use `working-directory: backend`. Compose runs from repo root via
`docker compose -f infrastructure/compose/compose.yml` with build context `../..`.

---

## 2. Move table: current → target → reason (StudioHub precedent)

### 2.1 Backend code + Python packaging

| Current | Target | Reason / StudioHub precedent |
|---|---|---|
| `apps/` (21 apps) | `backend/apps/` | `backend/apps/` — Django modular monolith apps |
| `config/` | `backend/config/` | `backend/config/` — Django project package |
| `tests/` | `backend/tests/` | `backend/apps/<app>/tests/` colocation is NOT adopted (behavior risk); tests move as one package under the backend so `tests.*` / `tests.dummy_app` imports and pytest `testpaths` resolve from `backend/` |
| `loadtests/` | `backend/loadtests/` | **DECISION** — StudioHub puts load tests in `infrastructure/k6/`; Cricket's harnesses are Python that import `config.settings.test` + `loadtests.safety`, so they must live on the Python path under `backend/` |
| `scripts/` | `backend/scripts/` | `backend/scripts/` — backend-local helper scripts |
| `ci/` | `backend/scripts/ci/` | `backend/scripts/` — schema-drift tooling are backend-local scripts consumed by workflows with `working-directory: backend` |
| `manage.py` | `backend/manage.py` | `backend/manage.py` |
| `pyproject.toml` | `backend/pyproject.toml` | `backend/pyproject.toml` — manifest next to its code, never root |
| `uv.lock` | `backend/uv.lock` | `backend/uv.lock` — lockfile next to manifest |
| `.python-version` | `backend/.python-version` | `backend/.python-version` |
| `.env.example` | `backend/.env.example` | `backend/.env.example` — env example next to where `.env` is consumed (`BASE_DIR/.env`, which becomes `backend/`) |
| `.coveragerc-identity` | `backend/.coveragerc-identity` | `backend/` tool config (pyproject/ruff.toml precedent) — coverage gates run from `backend/` |
| `.coveragerc-phasec` | `backend/.coveragerc-phasec` | same |
| `.coveragerc-phasef` | `backend/.coveragerc-phasef` | same |
| `.coveragerc-phaseh` | `backend/.coveragerc-phaseh` | same |
| `.coveragerc-phasei` | `backend/.coveragerc-phasei` | same |

### 2.2 Infrastructure (Docker, compose, proxy, alerts)

| Current | Target | Reason / StudioHub precedent |
|---|---|---|
| `Dockerfile` | `infrastructure/docker/django/Dockerfile` | `infrastructure/docker/django/Dockerfile` |
| `docker-compose.yml` | `infrastructure/compose/compose.yml` | `infrastructure/compose/compose.yml` (layered compose lives here) |
| `ops/proxy/nginx.conf` | `infrastructure/docker/nginx/nginx.conf` | `infrastructure/docker/nginx/nginx.conf` |
| `ops/alerts/*.yml` (5) + `ops/alerts/local_sink.py` | `infrastructure/ops/alerts/` | **DECISION** — StudioHub tracks no alert rules; task's expected shape puts "alert rules and dashboards under the infrastructure area". `local_sink.py` stays with its rules (it is the sink for them). Test import mechanism becomes load-by-path (see plan §2.6) |

### 2.3 Clients

| Current | Target | Reason / StudioHub precedent |
|---|---|---|
| `tools/reference_client/` | `clients/reference_client/` | **DECISION** — StudioHub's client material lives in its `frontend/`; Cricket has no frontend. Task's expected shape names a "clients area" for the reference client. `clients/` is the minimal slot; no frontend is built |

### 2.4 Items that stay (with reason)

| Path | Why it stays |
|---|---|
| `docs/` | Single root docs tree = StudioHub's primary convention (`docs/`). No move → relative links and Mermaid sources are untouched. Numbered-topic renumbering is NOT adopted (recommendation only) |
| `reports/` | No StudioHub precedent; committed generated evidence. Keep current location per "no precedent → keep" rule |
| `Makefile` | Root orchestration = StudioHub core convention. Targets repointed to `backend/` (Stage 3) |
| `README.md` | Root README (StudioHub has none, but the task's Stage 4 explicitly wants a root README explaining layout); Cricket already has one — keep and update |
| `.github/workflows/` | All CI stays in root `.github/workflows/` (StudioHub convention). YAMLs updated with `working-directory: backend` + path filters (Stage 3) |
| `.gitignore` | Root polyglot ignore = StudioHub convention. Only path-driven additions if any |
| `.dockerignore` | Root `.dockerignore` applies to repo-root build contexts (StudioHub keeps root `.dockerignore` too) |

### 2.5 New files (Stage 4)

| Path | Reason |
|---|---|
| `.editorconfig` | Task Stage 4; generic scaffolding (StudioHub has none — task overrides) |
| `.gitattributes` | Task Stage 4; generic scaffolding (StudioHub has none — task overrides) |
| `.github/CODEOWNERS` | Task Stage 4; **TODO-marked**, no invented owners (no owner facts in repo) |
| `docs/REPO_LAYOUT.md` | Task Stage 4 deliverable: describes final structure and how to add a new service/area |
| `backend/README.md`, `infrastructure/README.md`, `clients/README.md` | Task Stage 1: short README per top-level area describing its contents |

### 2.6 Import/namespace mechanics after the moves (unchanged behavior)

- `apps.*`, `config.*`, `tests.*` resolve from `backend/` (regular packages).
- `loadtests`, `scripts`, `scripts.game_day`, `scripts.security` remain namespace
  packages under `backend/` → `from loadtests.safety import ...` and
  `from scripts.game_day import common` keep working from `backend/`.
- `ops.alerts.local_sink` moves to `infrastructure/ops/alerts/` and is no longer on
  the Python path → `tests/phase_i/test_game_day.py` switches to a load-by-path
  import (`importlib.util.spec_from_file_location`) in Stage 3.
- Import-linter `root_packages = ["apps"]`, mypy `apps config`, ruff `check .`,
  pytest `testpaths = ["tests"]` are all evaluated from `backend/` and keep
  targeting exactly the same packages.

---

## 3. Final tree (depth 3) — target

```
cricket_os/
├── .editorconfig  .gitattributes  .gitignore  .dockerignore
├── .github/
│   ├── CODEOWNERS
│   └── workflows/
│       ├── ci.yml
│       ├── migration-drift.yml
│       └── schema-ddl.yml
├── Makefile  README.md
├── backend/
│   ├── apps/            # 21 Django apps
│   ├── config/          # settings/, urls, asgi, wsgi, celery
│   ├── tests/           # phase_c..phase_i + per-app + fixtures/factories/dummy_app
│   ├── scripts/         # extract_ddl, benchmark_*, game_day/, security/, ci/
│   ├── loadtests/       # harnesses + safety.py + smoke_gate.py + k6/
│   ├── manage.py  pyproject.toml  uv.lock  .python-version
│   ├── .env.example  .coveragerc-*  README.md
├── clients/
│   └── reference_client/  (README.md, realtime_client.py)
├── docs/                # unchanged
├── infrastructure/
│   ├── compose/
│   │   └── compose.yml
│   ├── docker/
│   │   ├── django/Dockerfile
│   │   └── nginx/nginx.conf
│   ├── ops/alerts/      # *.yml rules + local_sink.py
│   └── README.md
└── reports/             # unchanged (evidence) + restructure/
```

---

## 4. Decisions where StudioHub is silent or ambiguous (defaults applied)

| # | Decision | Default applied | Rationale |
|---|---|---|---|
| D1 | Test layout | `backend/tests/` single package (NOT per-app colocation) | Colocating 198 test files into 21 app dirs would change imports, fixtures and coverage gates — behavior risk. Recorded as recommendation |
| D2 | Load tests location | `backend/loadtests/` | Python harnesses import app code; must stay on the Python path. StudioHub's `infrastructure/k6/` precedent doesn't fit Python imports |
| D3 | Alert rules | `infrastructure/ops/alerts/` | Task: "alert rules and dashboards under the infrastructure area". No StudioHub precedent |
| D4 | Reference client | `clients/reference_client/` | Task: "reference client ... under the frontend or clients area". No frontend; `clients/` is the slot |
| D5 | `.env.example` | `backend/.env.example` | Env example next to consumer (`BASE_DIR`). StudioHub keeps both root + backend; Cricket's is app-env → backend |
| D6 | Docs numbering (01-…, adr/) | **Not adopted** | Renumbering 45 docs + rewriting every cross-link is a large no-behavior-change risk for zero functional gain; recommendation |
| D7 | Makefile mechanism | Keep uv-direct (no `docker compose exec django`) | Cricket's uv mechanism works; StudioHub's compose-exec is a different dev loop. Only paths repointed |
| D8 | `.editorconfig` / `.gitattributes` | Add (generic) | Task Stage 4 explicitly requests them even though StudioHub has none |
| D9 | Root `.env.example` removal | Keep only `backend/.env.example` | Compose reads vars via shell `env`/`.env` at repo root; if compose needs its own example in Stage 3, add `infrastructure/compose/.env.example` (StudioHub precedent) |
