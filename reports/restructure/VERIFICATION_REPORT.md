# Verification Report: Cricket OS restructure → StudioHub layout

Branch `chore/restructure-like-studiohub` · Safety tag `pre-restructure-2026-11-17`
Baseline recorded at Stage 0 (commit `d79ed67`) on code identical to the safety tag.

Every gate below was run at the **final** state (HEAD `e5f2569`). Where a gate
needed a fix, the fix is listed with the commit. Local env for Django commands
(exported explicitly, per plan): `DATABASE_URL=postgres://postgres:postgres@localhost:55435/cricket
REDIS_URL=redis://localhost:6379/0 CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2 CELERY_TASK_ALWAYS_EAGER=true`.
The operator's untracked root `.env` was never read or copied.

| # | Check | Baseline (Stage 0) | Final (Stage 5) | Verdict |
|---|---|---|---|---|
| V1 | `make check` from repo root | exit 0 — 1873 passed/94.84%, scoring 172/99.07%, identity 40/100%, phasec 184/98.85%, phasef 137×2 (93.96%/97.07%), phaseh 318×2 (100%), phasei 182/96.12% | **exit 0 — identical numbers** (1873/94.84%, 172/99.07%, 40/100%, 184/98.85%, 137×2 93.96%/97.07%, 318×2 100%, 182/96.12%) | ✅ PASS |
| V2 | `makemigrations --check` clean | `No changes detected`, exit 0 | `No changes detected`, exit 0 (from `backend/`) | ✅ PASS |
| V3 | migrate forward + to zero | 58 → 0 → 58, no errors | 58 → 0 → 58, no errors (from `backend/`) | ✅ PASS |
| V4 | `showmigrations` graph | recorded (58, incl. `dummy_app` under test settings) | **identical** (diff empty vs baseline dev graph) | ✅ PASS |
| V5 | compose stacks + health endpoints | data services healthy (55435/6379) | images build, app container starts, `GET /api/v1/health` → `{"data":{"status":"ok","checks":{"database":"ok","redis":"ok"}}}` | ✅ PASS |
| V6 | uvicorn + Celery start from new paths | app runs on host via uv | host uvicorn from `backend/` → health OK; Celery worker → `ready.` (connected to redis) | ✅ PASS |
| V7 | workflow YAMLs parse + run locally | schema-ddl (1157 lines, apply, 59 tables), migration-drift (NO DRIFT, 59 scoped) | 3 YAMLs parse; schema-ddl 1157 lines/apply/`scripts/ci/ddl_checks.sql` 59 tables; migration-drift NO DRIFT 59 scoped; CI steps = `make check`; security gates in `make check`. `actionlint` not installed (parsed via `yaml.safe_load` instead) | ✅ PASS |
| V8 | import-linter/mypy/ruff target same packages | 10 contracts, mypy 316 files, ruff clean | **identical** — 10 contracts kept, mypy 316 files, ruff clean, all from `backend/` | ✅ PASS |
| V9 | "gates have teeth" (throwaway worktree) | — | seeded import-linter violation (core→administration) → `lint-imports` exit 1; seeded `AKIA…` AWS key → `scan_secrets` exit 1 | ✅ PASS |
| V10 | docs links / Mermaid / runbook anchors | 74 md, 0 broken; 25 anchors OK; fences balanced | 79 md, 0 broken; 25 anchors OK; fences balanced | ✅ PASS |
| V11 | `git log --follow` per moved area | — | Dockerfile, local_sink, compose, manage.py, core/models, realtime_client, schema_drift_scope all show pre-restructure history through the rename commits | ✅ PASS |
| V12 | stale old-path greps (root-scoped) | — | zero stale references (`^scripts/`, `^ops/`, `^ci/`, `^tools/`, `^loadtests/`, `docker-compose.yml`, root `manage.py`/`pyproject.toml`/`uv.lock`/`Dockerfile`); only `.gitattributes` generic pattern + execution-log command records | ✅ PASS |
| V13 | `git status` clean; no secrets | clean | `git status --porcelain` empty; only `backend/.env.example` tracked; `git log --all -- .env` empty; secret scan clean | ✅ PASS |

## Fixes found and applied during Stage 5 (all reference-only, no behavior change)

| # | Issue | Fix (commit) |
|---|---|---|
| F1 | Compose `dockerfile:` path resolved against build context → build failed | `context: ../..` + `dockerfile: infrastructure/docker/django/Dockerfile` (`85bee96`) |
| F2 | Compose `command:` overrode the Dockerfile `uv run` CMD → `uvicorn` not on PATH in the image (latent pre-restructure; app container had never been started) | `command: uv run uvicorn …` / `uv run celery …` (`85bee96`) |
| F3 | Compose project dir became `infrastructure/compose/` → root `.env` no longer read for `POSTGRES_PORT`/`REDIS_PORT` interpolation | `COMPOSE_ARGS := --project-directory . -f infrastructure/compose/compose.yml` in Makefile (`e5f2569`) |
| F4 | `make check` residual path bugs surfaced by the full suite: `test_enums` schema-doc path (parents[1]→[2]), `import_cricsheet_register` curated-venues CSV (parents[4]→[3]), purity subprocess roots (scoring/fantasy engine) pointed at repo root instead of `backend/` | `bc4b135` |

## Notes / environment caveats (not restructure regressions)

- `make check` requires `DATABASE_URL`/`REDIS_URL` (or a `backend/.env`): BASE_DIR is
  now `backend/`, so Django reads `backend/.env`. The operator must copy
  `backend/.env.example` → `backend/.env` (README updated; local env vars were
  exported for this verification, never the `.env` contents).
- This machine has another service on host port 5432; the local stack uses
  55435 via the repo-root `.env` (`POSTGRES_PORT`). `make up` now reads that
  `.env` again via `--project-directory .` (F3).
- Mermaid render is verified by fence balance only (no local mermaid-cli);
  GitHub renders the diagrams natively.
