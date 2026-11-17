# backend

The Django modular monolith — all Python code and its tooling live here.

| Path | Contents |
|---|---|
| `apps/` | 21 first-party Django apps (core, scoring, matches, players, teams, venues, competitions, deliveries, innings, scorecards, live, realtime, ingestion, synchronization, providers, administration, statistics, search, rankings, shadow, fantasy) |
| `config/` | Django project package: `settings/{base,dev,prod,test}.py`, `urls.py`, `asgi.py`, `wsgi.py`, `celery.py` |
| `tests/` | The test suite (phase suites + per-app areas + fixtures/factories) |
| `scripts/` | Backend utility scripts: `extract_ddl.py`, benchmarks, `game_day/` drills, `security/` scans, `ci/` schema-drift tooling |
| `loadtests/` | Load-test harnesses + `safety.py` (G3 allow-list gate) + `smoke_gate.py` |
| `manage.py`, `pyproject.toml`, `uv.lock`, `.python-version` | uv project + Django entrypoint |
| `.env.example`, `.coveragerc-*` | Environment template + per-gate coverage configs |

## Run

All Python commands run from this directory (uv project root):

```sh
cd backend
uv sync --frozen        # install locked deps
uv run python manage.py migrate
uv run pytest           # or: make test* from the repo root
```

From the repo root, `make` targets fan into this directory.
