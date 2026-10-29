SHELL := /bin/bash
UV ?= uv
COMPOSE ?= docker compose
MANAGE := $(UV) run python manage.py

.DEFAULT_GOAL := help

.PHONY: help up down logs migrate makemigrations run worker test test-nocov lint fmt typecheck imports check shell

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

test: ## Run the full test suite with coverage
	$(UV) run pytest

test-nocov: ## Run tests without the coverage gate (fast inner loop)
	$(UV) run pytest --no-cov

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

check: lint typecheck imports test ## Everything CI enforces

shell: ## Django shell
	$(MANAGE) shell
