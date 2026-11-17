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

---
## Stage 0 verdict: **PASS** — baseline recorded; no red items attributable to the restructure.

### 0.1a docs link check + runbook anchors (baseline)
```
$ python3 /tmp/opencode/restructure/check_docs_links.py .   -> OK: 74 markdown files, all relative links resolve
$ python3 /tmp/opencode/restructure/check_runbook_anchors.py . -> OK: 25 runbook anchors; all alert runbook labels resolve
```
Mermaid: 4 docs files use ```mermaid blocks (LIVE_INGESTION, CRICKET_DATA_INGESTION,
CRICKET_BACKEND_ARCHITECTURE, CRICKET_DATABASE_SCHEMA). No local mermaid-cli tooling
(only `npx`, which needs network) -> fence balance verified (2/4,1/6,1/4,1/20), render
check deferred to GitHub's native Mermaid rendering. Baseline = fence-balanced.

### 0.2 makemigrations --check
<!-- filled below -->

### 0.3 migrate forward/backward
<!-- filled below -->

### 0.4 docs link check + Mermaid
See 0.1a above (recorded with make check section).

### 0.5 schema-ddl + migration-drift workflow steps (local)
<!-- filled below -->

### 0.6 full file list
<!-- filled below -->
