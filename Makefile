SHELL := /bin/bash
.DEFAULT_GOAL := help
PY ?= uv run

.PHONY: help install install-dev lint format typecheck test test-unit test-integration \
        privacy scan gate demo-up demo-down demo-logs build-portal clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies into the uv environment
	uv sync --no-dev

install-dev: ## Install runtime and development dependencies
	uv sync --extra dev

lint: ## Run the Python linter
	$(PY) ruff check src tests tools demo
	$(PY) ruff format --check src tests tools demo

format: ## Apply automatic Python formatting
	$(PY) ruff format src tests tools demo
	$(PY) ruff check --fix src tests tools demo

typecheck: ## Run static type checks
	$(PY) mypy

test: ## Run the Python test suite
	$(PY) pytest

test-unit: ## Run fast unit tests only
	$(PY) pytest -m "not integration and not requires_db and not e2e"

test-integration: ## Run integration tests (external services may be mocked)
	$(PY) pytest -m "integration"

privacy: ## Scan the working tree and full history for private information
	$(PY) python tools/privacy/scan.py --scope tree --scope history

scan: ## Run the staged-content privacy and secret scan
	$(PY) python tools/privacy/scan.py --scope staged

gate: ## Run the complete local publication gate (never pushes)
	$(PY) python tools/publication_gate.py --check-only

demo-up: ## Start the synthetic demo stack
	docker compose -f deploy/compose/compose.demo.yml up -d --build

demo-down: ## Stop the synthetic demo stack
	docker compose -f deploy/compose/compose.demo.yml down -v

demo-logs: ## Follow demo stack logs
	docker compose -f deploy/compose/compose.demo.yml logs -f --tail=100

build-portal: ## Build the public portal bundle
	cd apps/portal && npm ci && npm run build

clean: ## Remove local caches and build outputs
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage dist build
	rm -rf apps/portal/dist apps/portal/node_modules
