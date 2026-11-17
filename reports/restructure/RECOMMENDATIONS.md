# Recommendations & non-adopted conventions

Cricket OS now follows the StudioHub layout (see `MAPPING.md` for what was
adopted and why). This file records the StudioHub conventions that were
**deliberately not adopted** (with rationale), the decisions taken where the
reference was silent, and operator notes that fall out of the restructure.

## Non-adopted StudioHub conventions

| # | StudioHub convention | Why not adopted | Cost / how to revisit |
|---|---|---|---|
| R1 | Numbered docs tree (`docs/01-intro/…`) + `docs/adr/` | Renumbering 45 root docs and rewriting every cross-link is a large no-behavior-change risk for zero functional gain. Docs stay a single flat root tree. | Do it as a docs-only follow-up if a numbering scheme is wanted; the link checker (`/tmp/opencode/restructure/check_docs_links.py`) guards regressions. |
| R2 | Per-app colocated tests (`apps/<app>/tests/`) | 198 test files would move into 21 apps; `tests.*` imports, `tests.dummy_app`, pytest `testpaths` and every coverage gate would need rework — behavior risk. Tests stay one package under `backend/tests/`. | Adopt incrementally when an app's suite is rewritten; keep `tests.dummy_app` importable. |
| R3 | Load tests in `infrastructure/k6/` | Cricket's harnesses are **Python** that import `config.settings.test` + `loadtests.safety`; they must stay on the Python path (`backend/loadtests/`). The k6 JS scripts moved with them. | If a pure-k6 capacity suite is added later, `infrastructure/k6/` is the natural home. |
| R4 | Compose-exec Makefile fan-out (`docker compose exec django …`) | Cricket's uv-direct mechanism (`cd backend && uv run …`) is faster and works today; only paths were repointed. | Adopt only if containerized dev becomes the norm. |
| R5 | Per-area `.env.example` at root + `infrastructure/compose/` | Only the app consumes a `.env` today → one `backend/.env.example`. Compose interpolation vars are documented to come from the shell or a repo-root `.env` (README). | Add `infrastructure/compose/.env.example` if compose-specific vars accumulate. |
| R6 | Root README absence | StudioHub has no root README, but the task's Stage 4 explicitly wants a root README explaining layout; Cricket already had one. Kept and updated. | — |
| R7 | Alert-rule location | StudioHub tracks no Prometheus rules. Task's expected shape put them "under the infrastructure area" → `infrastructure/ops/alerts/`. The Python sink (`local_sink.py`) moved with its rules; tests load it by path. | If a monitoring repo appears, rules could move there; the runbook-anchor contract tests would move with them. |

## Decisions taken where the reference was silent (see MAPPING.md §4)

- **Test layout**: `backend/tests/` single package (R2 above).
- **Load tests**: `backend/loadtests/` (R3 above).
- **Alert rules**: `infrastructure/ops/alerts/` (R7 above).
- **Reference client**: `clients/reference_client/` (task's "clients area"; no frontend exists).
- **`.env.example`**: `backend/.env.example` (next to its consumer).
- **`docs/superpowers/plans/*.md`**: dated SDD archival records — **not edited**
  (documented exception). Their stale path mentions (`ci/`, `scripts/`, `ops/`)
  are historical records of the pre-restructure layout.

## Operator notes (fall out of the restructure)

1. **`.env` location changed.** Django now reads `backend/.env` (BASE_DIR is
   `backend/`). Copy your existing env: `cp backend/.env.example backend/.env`
   and re-export the values you had. The old repo-root `.env` is only read by
   compose for `POSTGRES_PORT`/`REDIS_PORT` interpolation (`make up` passes
   `--project-directory .`).
2. **Python commands run from `backend/`.** `make` targets and CI already do
   this (`working-directory: backend`). For ad-hoc commands: `cd backend && uv run …`.
3. **Compose moved.** `docker compose -f infrastructure/compose/compose.yml …`
   (or `make up/down/logs`). Build context stays the repo root.
4. **`.venv` location.** `uv sync` inside `backend/` creates `backend/.venv`;
   the old root `.venv` can be deleted (gitignored either way).
5. **Docker image startup** runs `uv run` (re-syncs the lockfile at container
   start, a pre-existing Dockerfile behavior). If startup latency matters,
   switch the image CMD to the synced interpreter or add `--no-sync`.
6. **`CODEOWNERS` is a TODO scaffold** — no owner facts exist in the repo; fill
   it in (`.github/CODEOWNERS`) before relying on ownership review.

## Follow-up ideas (out of scope for this restructure)

- Docs numbering (R1) and per-app test colocation (R2) if desired.
- A `docs/superpowers/plans/` migration/archive decision (keep as historical,
  or move under `docs/archive/` with links updated).
- `infrastructure/compose/.env.example` + a compose-vars section in the README
  if the stack grows beyond postgres/redis/app/worker.
