SHELL := /bin/bash
UV ?= uv
COMPOSE ?= docker compose
MANAGE := $(UV) run python manage.py

.DEFAULT_GOAL := help

.PHONY: help up down logs migrate makemigrations run worker test test-scoring test-phasec test-phasef test-identity test-nocov test-loadtest-smoke lint fmt typecheck imports check shell

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Start PostgreSQL + Redis and wait for health
	$(COMPOSE) up -d postgres redis
	@echo "waiting for services to become healthy..."
	@until [ "$$($(COMPOSE) ps --format json postgres | grep -c '"Health":"healthy"')" -ge 1 ] 2>/dev/null; do sleep 1; done || true
	$(COMPOSE) ps

down: ## Stop the local stack
	$(COMPOSE) down

logs: ## Tail the local stack logs
	$(COMPOSE) logs -f

migrate: ## Apply database migrations
	$(MANAGE) migrate

makemigrations: ## Create new migrations from model changes
	$(MANAGE) makemigrations

run: ## Run the ASGI dev server (uvicorn)
	$(UV) run uvicorn config.asgi:application --reload --host 0.0.0.0 --port 8000

worker: ## Run a Celery worker
	$(UV) run celery -A config.celery worker -l info

test: ## Run the full test suite with coverage (core + Phase B + Phase C apps >= 90% line+branch)
	$(UV) run pytest --cov=apps/core --cov=apps/venues --cov=apps/teams \
		--cov=apps/players --cov=apps/competitions --cov=apps/providers \
		--cov=apps/administration --cov=apps/matches --cov=apps/innings \
		--cov=apps/deliveries --cov=apps/scorecards --cov=apps/statistics \
		--cov=apps/ingestion --cov=apps/synchronization --cov=apps/live --cov-branch \
		--cov-report=term-missing --cov-report=xml:coverage.xml

test-phasec: ## Enforce >=95% line+branch on the write path + rebuild + Cricsheet mapper
	$(UV) run pytest -o addopts="" tests/phase_c -m "not benchmark" \
		--cov-config=.coveragerc-phasec --cov --cov-report=term-missing

test-phasef: ## Enforce >=90% line+branch on the Phase F apps; >=95% branch on the pipeline modules
	$(UV) run pytest -o addopts="" tests/phase_f \
		--cov=apps/statistics --cov=apps/search --cov=apps/rankings \
		--cov-branch --cov-report=term-missing --cov-fail-under=90
	$(UV) run pytest -o addopts="" tests/phase_f \
		--cov-config=.coveragerc-phasef --cov --cov-report=term-missing

test-scoring: ## Enforce >=95% line+branch coverage on the pure scoring engine
	$(UV) run pytest -o addopts="" tests/scoring -m "not benchmark" \
		--cov=apps/scoring --cov-branch --cov-report=term-missing --cov-fail-under=95

test-identity: ## Enforce 100% line+branch on the identity engine (scoring + merge)
	$(UV) run pytest tests/players/test_identity_scoring.py \
		tests/players/test_identity_scoring_properties.py \
		tests/players/test_merge.py \
		--cov-config=.coveragerc-identity --cov --cov-report=term-missing

test-nocov: ## Run tests without the coverage gate (fast inner loop)
	$(UV) run pytest

test-loadtest-smoke: ## Phase E load smoke: cached-read p95 <= 200ms, fan-out <= 1s
	DJANGO_SETTINGS_MODULE=config.settings.test $(UV) run python loadtests/smoke_gate.py

lint: ## Lint + format check (ruff)
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## Auto-fix lint issues and format
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Static type check (mypy)
	$(UV) run mypy apps config

imports: ## Enforce import boundaries (import-linter)
	$(UV) run lint-imports

check: lint typecheck imports test test-scoring test-identity test-phasec test-phasef ## Everything CI enforces

shell: ## Django shell
	$(MANAGE) shell
