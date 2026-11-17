# Restructure Plan: Cricket OS → StudioHub layout

Ordered, staged execution with a verification gate and rollback after every stage.
Every commit is `chore(restructure): <what>`. Rename-only commits are pure `git mv`
(no content edits) so rename detection works; reference fixes are separate commits.

---

## 1. Reference-impact checklist (every place paths appear + how updated)

Each row is a place that references paths and what Stage 3 does to it. **Package/module
names are never changed** (`apps.*`, `config.*`, `tests.*`, `loadtests.*`, `scripts.*`
stay identical from `backend/`); only *filesystem* paths change.

### 1.1 Python packaging and tool config
| Item | Today (repo root) | After (run from `backend/`) | Fix |
|---|---|---|---|
| `pyproject.toml` `testpaths = ["tests"]` | root | `backend/tests/` | auto (relative to rootdir) |
| `[tool.importlinter] root_packages = ["apps"]` | root | `backend/apps` | auto — same package from `backend/` |
| mypy `apps config` | root | `backend/apps backend/config` | auto |
| ruff `check .` / `format --check .` | root | `backend/` tree only | auto (CI `working-directory: backend`) |
| `[tool.django-stubs] django_settings_module = "config.settings.test"` | — | — | unchanged (module path) |
| `--cov-config=.coveragerc-*` (×5) | root | `backend/.coveragerc-*` | Makefile runs from `backend/`; coveragerc `include` globs re-checked for CWD-relative patterns |
| `coverage.xml` artifact | root | `backend/coverage.xml` | CI upload path → `backend/coverage.xml` |

### 1.2 `config/settings/base.py` path assumptions
- `BASE_DIR = parents[2]` → **automatically becomes `backend/`** after the move
  (`backend/config/settings/base.py`). Derived paths then resolve correctly:
  `BASE_DIR/.env`, `BASE_DIR/tests/fixtures/rankings`, `BASE_DIR/templates`,
  `BASE_DIR/staticfiles`, `BASE_DIR/media` — all inside `backend/`, matching where
  those trees move. No code change needed; verify.
- Local (untracked) `.env` currently at repo root — after the move Django reads
  `backend/.env`. This is an untracked local file; **note for the operator** (copy
  `.env.example` → `backend/.env`). Never read/copy the `.env` contents.

### 1.3 Makefile (root orchestration)
- All Python targets get `cd backend &&` (uv project + CWD = `backend/`), so
  relative args (`--cov-config`, test dirs, `coverage.xml`) resolve inside `backend/`.
- Compose targets (`up/down/logs`) get `-f infrastructure/compose/compose.yml`
  (StudioHub `COMPOSE` variable pattern).
- `MANAGE := $(UV) run python manage.py` reused inside backend-cd'd targets.
- `test-loadtest-smoke`: `cd backend && DJANGO_SETTINGS_MODULE=config.settings.test uv run python loadtests/smoke_gate.py`.
- `test-secrets` / `test-security`: `cd backend && uv run python scripts/security/scan_secrets.py`.
- Target names kept (already short verb nouns matching StudioHub style: `test`,
  `lint`, `check`, `migrate`, `up/down/logs`, `shell`).

### 1.4 Docker and compose
- `Dockerfile` → `infrastructure/docker/django/Dockerfile`; build context stays repo
  root. `COPY pyproject.toml uv.lock ./` → `COPY backend/pyproject.toml backend/uv.lock ./`;
  `COPY . .` → `COPY backend/ ./` (StudioHub Dockerfile pattern). CMD unchanged
  (`config.asgi:application` resolves with WORKDIR `/app` = backend contents).
- `docker-compose.yml` → `infrastructure/compose/compose.yml`; `build: .` →
  `context: ../..` + `dockerfile: docker/django/Dockerfile` (StudioHub precedent).
  Named volumes (`pgdata`, `redisdata`) unchanged. No bind mounts of static/media
  today — confirm and note.

### 1.5 GitHub workflows
| Workflow | Fix |
|---|---|
| `ci.yml` | `defaults.run.working-directory: backend` on lint/typecheck/imports/migrations/test jobs (StudioHub precedent); upload artifact path `backend/coverage.xml`; `on.push.branches: [main]` unchanged |
| `migration-drift.yml` | `defaults.run.working-directory: backend`; `paths:` → `backend/apps/**`, `backend/config/**`, `docs/CRICKET_DATABASE_SCHEMA.md`, `backend/scripts/ci/**`, `backend/scripts/extract_ddl.py`, `backend/pyproject.toml`, `backend/uv.lock`; step paths `ci/schema_drift_scope.txt|schema_manifest.sql|schema_drift_allowlist.txt` → `scripts/ci/...` |
| `schema-ddl.yml` | `defaults.run.working-directory: backend`; `paths:` → `backend/scripts/extract_ddl.py`, `backend/scripts/ci/ddl_checks.sql`, docs + workflow file; `psql -f ci/ddl_checks.sql` → `scripts/ci/ddl_checks.sql`; `python scripts/extract_ddl.py` unchanged (script default doc path fixed in 1.6) |

### 1.6 Script defaults
- `scripts/extract_ddl.py` default doc path `docs/CRICKET_DATABASE_SCHEMA.md` →
  compute from `__file__` (`parents[2] / "docs/CRICKET_DATABASE_SCHEMA.md"` = repo
  root docs) so it works from any CWD; keep the CLI-arg override.
- `scripts/consumer_ladder.py`: `ROOT = parents[1]` now = `backend/` (correct);
  `REF_CLIENT_DIR = ROOT / "tools" / "reference_client"` → `ROOT.parent / "clients" / "reference_client"`.
- `scripts/game_day/common.py`: `ROOT = parents[2]` now = `backend/`; `REPORTS_DIR`
  must stay repo-root `reports/` → `parents[3] / "reports" / "game_day"` (or `.git`
  marker).

### 1.7 Tests (parents-based ROOT + package imports)
| File | Fix |
|---|---|
| `tests/phase_i/test_security.py` | `ROOT = parents[2]` → `backend/` (auto-correct); `SECRET_SCAN/DAST_SCAN` = `backend/scripts/security/*.py` (auto-correct); `cwd=ROOT` = `backend/` (auto-correct) — verify only |
| `tests/phase_i/test_harness_safety.py` | `from loadtests.safety import ...` resolves from `backend/` — verify only |
| `tests/phase_i/test_game_day.py` | `ROOT = parents[2]` → `backend/`; `from ops.alerts.local_sink import route, runbook_link` → load-by-path (`importlib.util.spec_from_file_location`) into `infrastructure/ops/alerts/local_sink.py`; any `reports/` path → parents[3] |
| `tests/phase_e/test_reference_client.py` | subprocess path `parents[2]/"tools"/"reference_client"` → `ROOT.parent/"clients"/"reference_client"` |
| `tests/phase_d/test_observability.py`, `tests/phase_i/test_observability.py` | alert YAML path strings `ops/alerts/*.yml` → `infrastructure/ops/alerts/*.yml` (relative to repo root) |

### 1.8 Ops artifacts
- `ops/alerts/local_sink.py`: `RUNBOOKS = ROOT / "docs" / "RUNBOOKS.md"` — ROOT
  computation (parents-based) must yield repo root from its new
  `infrastructure/ops/alerts/` location → parents[3] or `.git` marker. `docs/` is
  unchanged so anchors still resolve.

### 1.9 Docs, README, diagrams
- `docs/` stays put → relative links and Mermaid sources unchanged. Grep docs for
  *filesystem* path references that changed (`scripts/`, `ci/`, `ops/`, `loadtests/`,
  `tools/`, `docker-compose.yml`, `Dockerfile`, `manage.py`, `pyproject.toml`) and
  update prose; do **not** touch `apps.x.y` module references (unchanged).
- `README.md` (root) quick-start: commands still `make check` / `uv run pytest`
  (now via `cd backend` or `make`); update any file paths shown.
- Runbook anchors: `docs/RUNBOOKS.md` unchanged → alert `runbook:` labels still resolve.

### 1.10 Ignore/env examples
- `.gitignore`: `.env`/`.env.*` + `!.env.example` patterns already location-agnostic.
  Add `backend/coverage.xml` coverage artifact if not covered (`.gitignore` already
  has `coverage.xml`). `.dockerignore` unchanged (repo-root build context).
- `.env.example`: moved to `backend/`; ensure `scan_secrets.py` still treats it as
  example (verify its ignore list).

### 1.11 Hard-coded path greps (Stage 3 gate)
Grep patterns that must return **zero stale references** at the end:
`\bci/`, `\bscripts/`, `\bops/`, `\bloadtests/`, `\btools/`, `docker-compose\.yml`,
`Dockerfile` (root-only refs), `\bmanage\.py` (root-only refs), `pyproject\.toml` +
`uv\.lock` (root-only refs) — excluding module-path false positives (`apps.`, `config.`
as dotted imports) and `backend/`-prefixed new paths.

---

## 2. Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Django app labels / migration paths / Celery task names** change if `apps/<app>` module path changes | `apps` package moves whole under `backend/`; module path stays `apps.<app>...` from `backend/`. Verify `showmigrations` graph identical to baseline |
| **BASE_DIR depth** flips to `backend/` | Intended and correct; every derived path (`tests/fixtures/rankings`, `templates`, `staticfiles`, `media`, `.env`) lands inside `backend/` where the trees move. Verify settings tests + fixtures |
| **pytest rootdir** moves | `pyproject.toml` moves with the code; all pytest/coverage commands run from `backend/`. Verify `make check` |
| **Import-linter root_packages** | `apps` still resolvable from `backend/`; config diff shows same `root_packages=["apps"]` + same contracts. "Gates have teeth" re-proven with seeded violations in a throwaway worktree |
| **`ops.alerts.local_sink` import** breaks (ops → infrastructure) | Load-by-path in the two test call sites (reference fix, no behavior change — same functions, same file) |
| **Namespace packages** (`loadtests`, `scripts`) resolve from root today | They move under `backend/` together; all commands run from `backend/`, so resolution is unchanged. Verify with the harness-safety + game-day tests |
| **Docker layer cache** invalidates (`COPY pyproject.toml` → `COPY backend/pyproject.toml`) | Accepted (one-time); lockfile still copied before app code so dep layer caching is preserved |
| **Workflows reference root paths** | All three YAMLs get `working-directory: backend` + updated path filters (StudioHub pattern). Parse-check + local step-by-step run where feasible |
| **`extract_ddl.py` default doc path** breaks if CWD ≠ repo root | Default computed from `__file__` → repo-root `docs/`; CLI arg kept |
| **Reports dir** moves under backend if parents[2] assumed | `scripts/game_day/common.py` + `test_game_day` switch to parents[3]/.git marker for `reports/` |
| **Static/media compose volumes** (if any) | Compose has no static/media bind mounts today (named volumes only) — confirm in Stage 3 |
| **Docs prose references old filesystem paths** | Targeted greps (§1.11) + link check in Stage 5 |
| **`coverage.xml` artifact path in CI** | Upload path → `backend/coverage.xml` |
| **Untracked local `.env` at root** | Operator note: copy `backend/.env.example` → `backend/.env`. Never read/copy contents |

---

## 3. Staged execution plan (gate + rollback per stage)

Branch: `chore/restructure-like-studiohub` (from clean tree at `47f9002`).
Safety tag: `pre-restructure-2026-11-17` (created). Rollback for any stage:
`git reset --hard <commit-before-stage>` (final rollback = `git reset --hard pre-restructure-2026-11-17` — NOT run by me).

### Stage 0 — Baseline (on the safety tag; no changes)
Record in `EXECUTION_LOG.md`: `make check` output + coverage numbers, full test run,
`makemigrations --check`, migrate forward/backward, docs-link + Mermaid check
(if tooling exists), schema-ddl + migration-drift workflow steps run locally,
full file list (`git ls-files`). Gate: baseline recorded, red items (if any) logged
and NOT attributed to the restructure. Commit: none (or log-only).
Rollback: n/a.

### Stage 1 — Skeleton
Create target dirs + a short README per top-level area (`backend/README.md`,
`infrastructure/README.md`, `clients/README.md`), StudioHub README style. **No moves.**
Gate: dirs + READMEs exist; `git status` shows only new files; `make check` still
green (nothing moved). Commit `chore(restructure): scaffold target directories`.
Rollback: `git reset --hard HEAD^`.

### Stage 2 — Rename-only moves (one commit per area, pure `git mv`)
Order per task: docs (no-op) → infrastructure → ops and loadtests → tools and
clients → scripts and CI helpers → backend code and packaging.
1. docs: no-op (stays). Record reason.
2. infrastructure: `git mv Dockerfile infrastructure/docker/django/Dockerfile`;
   `git mv docker-compose.yml infrastructure/compose/compose.yml`;
   `git mv ops/proxy/nginx.conf infrastructure/docker/nginx/nginx.conf`;
   `git mv ops/alerts infrastructure/ops/alerts`.
3. ops+loadtests: `git mv loadtests backend/loadtests` (ops/alerts done in 2).
4. tools+clients: `git mv tools/reference_client clients/reference_client`; remove
   empty `tools/` dir.
5. scripts+ci: `git mv scripts backend/scripts`; `git mv ci backend/scripts/ci`.
6. backend: `git mv apps backend/apps; git mv config backend/config; git mv tests
   backend/tests; git mv manage.py pyproject.toml uv.lock .python-version backend/;
   git mv .coveragerc-* backend/; git mv .env.example backend/.env.example`.
Gate after EACH area: `git status` shows only renames; `git log --follow` on three
sample files per area; `git diff -M --stat` shows high rename similarity; the tree
is **known-broken** (paths moved, refs not yet fixed) — the gate is that it is
broken *only* in the expected reference-fix ways, verified by grep inventory.
Rollback per area: `git reset --hard <commit-before-area>`.

### Stage 3 — Reference fixes (one commit per category)
1. **Python packaging/tool config**: verify import-linter/mypy/ruff/pytest/coverage
   effective configs from `backend/`; fix `.coveragerc-*` include patterns if
   CWD-relative; `tests/phase_i/test_security.py` + `test_harness_safety` + settings
   paths verified.
2. **Makefile + root orchestration**: repoint all targets to `cd backend` / compose
   `-f infrastructure/compose/compose.yml`; keep target names.
3. **Docker + compose**: Dockerfile COPY paths; compose build context/dockerfile.
4. **GitHub workflows**: `working-directory: backend`, path filters, artifact paths,
   `scripts/ci/...` step paths; YAML parse check.
5. **Docs links + diagrams + script defaults**: `extract_ddl.py` default;
   `consumer_ladder.py` + `game_day/common.py` ROOT/REPORTS_DIR;
   `test_game_day.py` load-by-path; observability alert-YAML path strings;
   `test_reference_client` client path; docs prose path updates.
6. **Ignore files + env examples**: `.gitignore`/`.dockerignore` as needed;
   `backend/.env.example`; `scan_secrets.py` example handling.
Gate: `make check` green from repo root with test counts/coverage ≥ baseline;
`makemigrations --check` clean; `git log --follow` on samples; zero stale-path greps.
Rollback: `git reset --hard <commit-before-category>`.

### Stage 4 — Root hygiene
Add `.editorconfig`, `.gitattributes` (generic), `.github/CODEOWNERS` (TODO-marked,
no invented owners), `docs/REPO_LAYOUT.md`; update root `README.md` layout section.
Gate: files present, valid; `make check` green; `git status` clean.
Commit `chore(restructure): root hygiene (editorconfig, gitattributes, CODEOWNERS todo, repo layout doc)`.
Rollback: `git reset --hard HEAD^`.

### Stage 5 — Final verification (full checklist)
Run and record in `VERIFICATION_REPORT.md` every verification item (see §4).
Gate: all PASS vs baseline. Commit any log/report updates.

---

## 4. Verification matrix (baseline vs final)

| # | Check | Baseline (Stage 0) | Final (Stage 5) |
|---|---|---|---|
| V1 | `make check` from repo root | exit 0, 182 phase_i tests, 96.12% | equal or better, per-app |
| V2 | `makemigrations --check` clean | yes | yes |
| V3 | migrate forward + to zero | ok | ok |
| V4 | `showmigrations` graph | recorded | identical |
| V5 | compose stacks start + health endpoints respond (dev; prod-topology if present) | recorded | ok from new paths |
| V6 | Celery worker/beat + ASGI start from new paths | recorded | ok |
| V7 | workflow YAMLs parse + run locally where feasible (schema-ddl, migration-drift, ci, security gates) | recorded | ok; list unrunnable steps |
| V8 | import-linter/mypy/ruff target same packages (effective-config diff) | recorded | identical |
| V9 | "gates have teeth": seeded purity + forbidden-import violation in throwaway worktree → import-linter fails | — | proven |
| V10 | docs relative links resolve; Mermaid renders; runbook anchors exist | recorded | ok |
| V11 | `git log --follow` on sample files per moved area; `git diff -M --stat` high similarity | — | ok |
| V12 | grep for old paths → zero stale references (patterns in §1.11) | — | ok |
| V13 | `git status` clean; no new untracked artifacts; no secrets/.env committed | clean | clean |

---

## 5. Deliverables on the branch

`reports/restructure/`: STUDIOHUB_STRUCTURE_ANALYSIS.md ✅, CRICKET_INVENTORY.md ✅,
MAPPING.md ✅, RESTRUCTURE_PLAN.md (this file), EXECUTION_LOG.md, VERIFICATION_REPORT.md,
RECOMMENDATIONS.md — plus `docs/REPO_LAYOUT.md` and the staged commits.
