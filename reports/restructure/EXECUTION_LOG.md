# Restructure Execution Log

Branch: `chore/restructure-like-studiohub` · Safety tag: `pre-restructure-2026-11-17`
Start commit: `47f9002` (develop/main) · Repo: `/home/prafull.sakharkar/Repository/github/cricket_os`

> Format per stage: **commands run** → **exact output/result** → **gate verdict (PASS/FAIL)**.
> Rollback for any stage: `git reset --hard <commit-before-stage>` (final: `git reset --hard pre-restructure-2026-11-17`).

---

## Stage 0 — Baseline (code state = safety tag; only reports/restructure planning docs differ, which no gate reads)

Baseline taken on the branch at `47f9002` + 3 planning-doc commits (`23ad73f`, `9fc5169`).
The planning docs touch only `reports/restructure/*.md` — no code, no config, no
paths that any gate consumes. Code state is identical to the safety tag.

### 0.0 Environment
```
$ docker compose ps            -> cricket_os-postgres-1 (postgres:17) Up 2 weeks (healthy), port 55435->5432
                                 cricket_os-redis-1    (redis:8)     Up 4 days  (healthy), port 6379
$ uv --version                 -> uv 0.11.25
$ python3 --version            -> (host python; scripts use uv-managed 3.14)
$ git ls-files | wc -l         -> 718 (714 pre-restructure + 3 planning docs + EXECUTION_LOG)
```
Local `.env` exists (untracked, gitignored) and drives `DATABASE_URL` at runtime; contents never read.

### 0.1 make check
RUN: `make check` (from repo root) — `lint typecheck imports test-secrets test test-scoring test-identity test-phasec test-phasef test-phaseh test-phasei`.
Full output: `/tmp/opencode/restructure/baseline-make-check.log` (not committed).
RESULT: **exit 0**. Per-gate:
| Gate | Tests | Coverage |
|---|---|---|
| ruff lint + format | — | `All checks passed!` |
| mypy (`apps config`) | — | passed |
| import-linter | — | passed |
| test-secrets | — | passed |
| `test` (app-wide ≥90%) | 1873 passed, 2 deselected | 94.84% |
| `test-scoring` (≥95%) | 172 passed, 2 deselected | 99.07% |
| `test-identity` (100%) | 40 passed | 100.00% |
| `test-phasec` (≥95%) | 184 passed | 98.85% |
| `test-phasef` (≥90% / ≥95%) | 137 passed (×2 runs) | 93.96% / 97.07% |
| `test-phaseh` (≥90% / ≥95%) | 318 passed (×2 runs) | 100.00% / 100.00% |
| `test-phasei` (≥95%) | 182 passed | 96.12% |
Gate: **PASS** (matches known baseline: 182 phase_i, 96.12%).

### 0.2 makemigrations --check
RUN: `DJANGO_SETTINGS_MODULE=config.settings.test DATABASE_URL=postgres://postgres:postgres@localhost:55435/django_db uv run python manage.py makemigrations --check --dry-run`
RESULT: `No changes detected`, exit 0 → **PASS**.

### 0.3 migrate forward/backward
Scratch DB `baseline_mig` (dev DB untouched; schema `cricket`, search_path `cricket,public`):
```
createdb baseline_mig
migrate --noinput (config.settings.test)        -> 58 applied, exit 0
showmigrations graph == dev cricket DB graph    -> IDENTICAL (58)
migrate <app> zero for every app (reverse order)-> 0 applied (backward to zero, no errors)
migrate --noinput again                          -> 58 applied
graph diff vs dev DB                             -> IDENTICAL
dropdb baseline_mig
```
Note: `dummy_app.0001_initial` is in the graph only under test settings (it is in
`INSTALLED_APPS` for tests); dev settings fresh-migrate = 57. Both are pre-existing.
Gate: **PASS** (forward, backward-to-zero, graph identical).

### 0.4 docs link check + Mermaid
See 0.1a above (recorded with make check section).

### 0.5 schema-ddl + migration-drift workflow steps (local)
schema-ddl (`ddl_baseline` scratch DB):
```
python3 scripts/extract_ddl.py > reference.sql   -> 1157 lines, exit 0
psql -v ON_ERROR_STOP=1 -f reference.sql         -> exit 0 (DDL applies clean)
psql -v ON_ERROR_STOP=1 -f ci/ddl_checks.sql     -> exit 0 (59 tables in cricket schema)
dropdb ddl_baseline
```
migration-drift (`ref_db`, `django_db` scratch DBs):
```
python3 scripts/extract_ddl.py | psql -d ref_db ...     -> exit 0
manage.py migrate --noinput (django_db)                  -> exit 0 (58 applied)
manage.py makemigrations --check --dry-run               -> "No changes detected", exit 0
grep -vE '^\s*(#|$)' ci/schema_drift_scope.txt           -> 59 scoped tables
manifest ref_db vs manifest django_db (allowlist applied)-> diff empty: NO DRIFT
dropdb ref_db django_db
```
Gate: **PASS**.

### 0.6 full file list
`git ls-files` → 718 entries (714 pre-restructure + 4 planning/log docs), saved to
`/tmp/opencode/restructure/baseline-files.txt`. Baseline tree = depth-2 listing in
CRICKET_INVENTORY.md §1.

### 0.1a docs link check + runbook anchors (baseline)
```
$ python3 /tmp/opencode/restructure/check_docs_links.py .   -> OK: 74 markdown files, all relative links resolve
$ python3 /tmp/opencode/restructure/check_runbook_anchors.py . -> OK: 25 runbook anchors; all alert runbook labels resolve
```
Mermaid: 4 docs files use ```mermaid blocks (LIVE_INGESTION, CRICKET_DATA_INGESTION,
CRICKET_BACKEND_ARCHITECTURE, CRICKET_DATABASE_SCHEMA). No local mermaid-cli tooling
(only `npx`, which needs network) -> fence balance verified (2/4,1/6,1/4,1/20), render
check deferred to GitHub's native Mermaid rendering. Baseline = fence-balanced.

---
## Stage 0 verdict: **PASS** — baseline recorded; no red items attributable to the restructure.

---

## Stage 1 — Skeleton (commit `855ca5b`)

Created target directories + a short README per top-level area:
`backend/README.md`, `infrastructure/README.md`, `clients/README.md`
(StudioHub README style: one table of contents, run section). No moves.
A stray `infrastructure/clients/` dir created by the mkdir was removed before commit.

Gate: dirs + READMEs exist, `make check` untouched (nothing moved). **PASS**.
Rollback: `git reset --hard 23ad73f` (unused).

## Stage 2 — Rename-only moves (commits `4fa70df` `09cea87` `e1b4f52` `314a98e` `090e802` `765c061`)

All `git mv`, 635 renames, 0 content edits (`git diff 855ca5b..HEAD -M --summary`
shows only `rename` lines; `git diff -M --stat` shows `| 0` for every moved file).

| Area | Commit | Moves |
|---|---|---|
| infrastructure | `4fa70df` | `Dockerfile`→`infrastructure/docker/django/Dockerfile`, `docker-compose.yml`→`infrastructure/compose/compose.yml`, `ops/proxy/nginx.conf`→`infrastructure/docker/nginx/nginx.conf`, `ops/alerts`→`infrastructure/ops/alerts` |
| loadtests | `09cea87` | `loadtests`→`backend/loadtests` |
| reference client | `e1b4f52`, `314a98e` | `tools/reference_client`→`clients/reference_client` (flattened) |
| scripts + ci | `090e802` | `scripts`→`backend/scripts`, `ci`→`backend/scripts/ci` |
| backend code + packaging | `765c061` | `apps config tests manage.py pyproject.toml uv.lock .python-version .coveragerc-* .env.example`→`backend/` |

`git log --follow` verified on samples per area (Dockerfile → `750c334`,
local_sink → `3e4d9d9`, compose → `750c334`, smoke_gate → `5a29d3e`, safety →
`e17a6ad`, realtime_client → `f37e9d5`, extract_ddl → `9d595fb`, manage.py →
`750c334`, pyproject → `127da59`, core/models → `837eed9`). Untracked
`__pycache__` dirs from the moves were removed (never committed).

Gate: renames only, history intact, tree broken *only* in expected reference-fix
ways (stale-path inventory captured and used as the Stage 3 fix list). **PASS**.

## Stage 3 — Reference fixes (commits `1b6c09e` `95b6a95` `1d5ed5f` `eeeb4a2` `983dc06`)

### 3.1 Python path computations + script defaults + tests (`1b6c09e`, 22 files)
- `infrastructure/ops/alerts/local_sink.py`: `ROOT = parents[2]` → `parents[3]` (repo root).
- `backend/scripts/security/scan_secrets.py`: `ROOT = parents[2]` → `parents[3]` (repo root).
- `backend/scripts/security/dast_scan.py`: `ROOT` (sys.path) = `parents[2]` (backend);
  new `REPO_ROOT = parents[3]` for `REPORT_DIR`.
- `backend/scripts/consumer_ladder.py`: `REF_CLIENT_DIR = ROOT.parent/"clients"/"reference_client"`;
  `REPORT_PATH = ROOT.parent/"reports"/"phase_i"/...`.
- `backend/scripts/game_day/common.py`: `ROOT` (sys.path) = `parents[2]` (backend);
  `REPORTS_DIR = parents[3]/"reports"/"game_day"` (repo root).
- `backend/loadtests/harness_common.py`: `REPORTS_DIR = parents[2]/"reports"/"phase_i"/"load"`.
- `backend/scripts/extract_ddl.py`: default doc path from `__file__` (`parents[2]/docs/...`).
- `backend/apps/players/management/commands/import_cricsheet_register.py`: `parents[3]`→`parents[4]`.
- Tests: `test_game_day` (load-by-path `local_sink` via `spec_from_file_location`,
  `REPO_ROOT` for reports/docs), `test_consumer_ladder` (nginx + report paths),
  `test_docs` (dual roots), `test_gate_docs` (parents[2]), `test_reference_client`
  (client path), `phase_d/h/i/test_observability` (alert-YAML + RUNBOOKS paths).
- Docstring/comment path fixes: `fantasy/metrics.py`, `soak.py`,
  `scenario_replica_loss.py`, `test_migrations.py` (+phase_c), `test_reference_client`,
  `phase_f/test_observability`. `cd backend &&` docstring prefixes reverted (E501 churn).
- Verified: `ruff check .` + `ruff format --check .` clean, `mypy apps config`
  clean (316 files), import-linter 10 contracts kept, targeted tests 27+43+12 passed,
  `.coveragerc-phasec` gate 98.85% from `backend/` (no coveragerc change needed).

### 3.2 Makefile (`95b6a95`)
`PY := cd backend && $(UV) run`, `COMPOSE_ARGS := -f infrastructure/compose/compose.yml`,
all Python targets run from `backend/`, compose targets use the new file. Target names
kept. Verified: `make help`, `make lint`, `make imports`, `make migrate`, `make test-secrets`.

### 3.3 Docker + compose (`1d5ed5f`)
`infrastructure/docker/django/Dockerfile`: `COPY backend/pyproject.toml backend/uv.lock ./`
+ `COPY backend/ ./` (build context stays repo root). Compose `app`/`worker`:
`build.context: ../..` + `dockerfile: docker/django/Dockerfile`. `docker compose config -q` valid.

### 3.4 GitHub workflows (`eeeb4a2`)
`ci.yml`: per-job `defaults.run.working-directory: backend` (lint/typecheck/imports/migrations/test),
upload artifact `backend/coverage.xml`, comment `docker-compose.yml`→`infrastructure/compose/compose.yml`.
`migration-drift.yml`: job-level `working-directory: backend`, `paths:` → `backend/apps/**`,
`backend/config/**`, `backend/scripts/ci/**`, `backend/scripts/extract_ddl.py`,
`backend/pyproject.toml`, `backend/uv.lock`; step paths `ci/*`→`scripts/ci/*`; artifact
`backend/ref.txt`, `backend/dj.txt`. `schema-ddl.yml`: `working-directory: backend`,
`paths:` → `backend/scripts/extract_ddl.py`, `backend/scripts/ci/ddl_checks.sql`;
`psql -f scripts/ci/ddl_checks.sql`; artifact `backend/reference.sql`.
YAML parse OK (3 files). Local simulation from `backend/`: `python3 scripts/extract_ddl.py`
(no-arg) → 1157 lines (same as baseline), DDL applies clean, `scripts/ci/ddl_checks.sql`
→ 59 tables. actionlint not installed (noted).

### 3.5 Active docs path refs (`983dc06`, 21 files)
Script (`/tmp/opencode/restructure/fix_docs_paths.py`, not committed) rewrote stale
filesystem paths in 20 active docs + README.md:
`ops/alerts/*`→`infrastructure/ops/alerts/*`, `ops/proxy/nginx.conf`→
`infrastructure/docker/nginx/nginx.conf`, `tools/reference_client`→`clients/reference_client`,
`ci/{schema_*,ddl_checks}`→`backend/scripts/ci/*`, `docker-compose.yml`→
`infrastructure/compose/compose.yml`, `.coveragerc-*`→`backend/.coveragerc-*`,
`loadtests/`/`scripts/`→`backend/...`. Command examples made runnable:
`uv run python loadtests|scripts/...` → `cd backend && uv run python ...`;
reference client (stdlib-only) → `python clients/reference_client/...` from repo root.
`docs/superpowers/plans/*.md` excluded (dated archival records — documented exception).
README.md: `cp backend/.env.example backend/.env`, `cd backend && uv sync`, layout section.
Verified: docs links OK (78 files), runbook anchors OK (25), Mermaid fences balanced.

### 3.6 Ignore files + env examples (no commit — no changes needed)
`.gitignore`/`.dockerignore` patterns are location-agnostic (`coverage.xml`, `.venv/`,
`.env`, `!.env.example` match at any depth): `git check-ignore backend/.env`
`backend/coverage.xml` `backend/.venv` all ignored; `backend/.env.example` tracked.
`scan_secrets.py` excludes by name (`name != ".env.example"`) → still scans the example.

Stage 3 gate: targeted gates green during the category commits (ruff/mypy/imports,
test-secrets, compose config, workflow YAML, docs links). Full `make check` = Stage 5 V1.

## Stage 4 — Root hygiene (commit `19430d1`)

Added `.editorconfig` + `.gitattributes` (generic), `.github/CODEOWNERS` (TODO-marked,
no invented owners), `docs/REPO_LAYOUT.md` (full map + how-to-add sections), root
README.md layout section. Docs link check still OK (79 files).
Gate: files present/valid, links OK. **PASS**.

## Stage 5 — Final verification (commits `9cdec36` `bc4b135` `85bee96` `e5f2569`)

Full results in `VERIFICATION_REPORT.md`. Summary:

- **V1 `make check`** — first full run FAILED for an environmental reason (no
  `backend/.env`, so Django used the default `DATABASE_URL` on port 5432, which
  another service owns). Re-run with the verification env exported: **exit 0,
  numbers identical to baseline** (1873/94.84%, 172/99.07%, 40/100%,
  184/98.85%, 137×2 93.96%/97.07%, 318×2 100%, 182/96.12%).
- The first env-correct run surfaced **5 residual path bugs** fixed in `bc4b135`:
  `test_enums` schema-doc (`parents[1]`→`[2]`), `import_cricsheet_register`
  curated-venues CSV (`parents[4]`→`[3]`), and the three purity/determinism
  subprocess roots (scoring + fantasy engine) that pointed at the repo root via
  the `.git` marker instead of `backend/`. After the fix: 44/44 previously
  failing tests pass; full `make check` green.
- **V2–V4 migrations** — `makemigrations --check` clean; 58→0→58 round-trip;
  graph diff empty vs baseline (from `backend/`).
- **V5 compose** — image build failed first: `dockerfile:` is resolved against
  the build context, so `docker/django/Dockerfile` → `infrastructure/docker/django/Dockerfile`
  (`85bee96`). Then the app container failed at start: compose `command:`
  overrode the Dockerfile `uv run` CMD and `uvicorn` is not on PATH — fixed with
  `uv run uvicorn/celery` (`85bee96`; latent pre-restructure, containers had
  never been started). Then `--profile full up` tried to recreate postgres on
  port 5432 because compose's project dir is now `infrastructure/compose/` and no
  longer read the root `.env` (`POSTGRES_PORT=55435`); the old container was
  recreated/removed and restored from the `pgdata` volume with data intact.
  Fix: Makefile `COMPOSE_ARGS := --project-directory . -f …` (`e5f2569`) so
  `make up` reads the repo-root `.env` as before. Final: images built, app
  container starts, `GET /api/v1/health` → 200 `{"status":"ok","database":"ok","redis":"ok"}`.
- **V6 uvicorn/celery** — host uvicorn from `backend/` → health OK; celery
  worker → `ready.` on redis.
- **V7 workflows** — 3 YAMLs parse; schema-ddl steps from `backend/` (extract
  1157 lines, DDL applies, `scripts/ci/ddl_checks.sql` → 59 tables);
  migration-drift steps from `backend/` (migrate, makemigrations clean,
  NO DRIFT across 59 scoped tables).
- **V9 gates have teeth** — throwaway worktree seeded `apps.core →
  apps.administration` import → `lint-imports` exit 1; seeded a synthetic
  AWS-access-key-shaped token (matching the scan's exact regex) →
  `scan_secrets` exit 1 (a first seed with a `_SEEDED` suffix did not match the
  `\b` pattern — reseeded to match exactly). Worktree removed.
- **V10–V13** — docs links 79/0 broken, 25 runbook anchors, Mermaid fences
  balanced; `git log --follow` samples per moved area show pre-restructure
  history; root-scoped stale-path grep empty; `git status` clean; only
  `backend/.env.example` tracked; `git log --all -- .env` empty; secret scan clean.

Gate: **PASS** — every item in the verification matrix green vs baseline.

---

## Final commit log (this branch, oldest → newest)

```
23ad73f chore(restructure): add StudioHub structure analysis and Cricket OS inventory
9fc5169 chore(restructure): mapping and staged execution plan
d79ed67 chore(restructure): record Stage 0 baseline (make check, migrations, drift, docs)
855ca5b chore(restructure): scaffold target directories with per-area READMEs
4fa70df chore(restructure): move infrastructure files (Dockerfile, compose, nginx, alert rules)
09cea87 chore(restructure): move loadtests under backend
e1b4f52 chore(restructure): move reference client under clients
314a98e chore(restructure): flatten reference_client into clients root
090e802 chore(restructure): move scripts and ci helpers under backend/scripts
765c061 chore(restructure): move backend code and packaging under backend/
1b6c09e chore(restructure): fix Python path computations, script defaults and test path references
95b6a95 chore(restructure): repoint Makefile targets to backend and compose path
1d5ed5f chore(restructure): repoint Dockerfile and compose to backend layout
eeeb4a2 chore(restructure): repoint GitHub workflows to backend layout
983dc06 chore(restructure): update active docs path references to new layout
19430d1 chore(restructure): root hygiene (editorconfig, gitattributes, CODEOWNERS todo, repo layout doc)
9cdec36 chore(restructure): record Stages 1-4 in execution log
bc4b135 chore(restructure): fix residual path computations (enums doc, importer venues CSV, purity subprocess roots)
85bee96 chore(restructure): fix compose dockerfile path and container commands (uv run)
e5f2569 chore(restructure): keep compose reading repo-root .env for port interpolation
```

Rollback (if ever needed, NOT run): `git reset --hard pre-restructure-2026-11-17`.
Nothing has been force-pushed or rewritten; the branch is local-only.
