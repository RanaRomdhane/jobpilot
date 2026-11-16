SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV ?= uv
PNPM ?= pnpm
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
DEMO := docker compose -f infra/docker-compose.yml -f infra/docker-compose.demo.yml --env-file .env
WEB := apps/web
PY_SRC := packages apps/api apps/worker apps/mcp_gmail apps/agents scripts tests

.PHONY: help env install up down restart logs ps infra migrate migrate-local revision \
        demo demo-reset e2e \
        test test-unit test-integration eval lint fmt typecheck check gen-client \
        api-dev worker-dev web-dev clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- setup
env: ## Create .env from .env.example with generated secrets (never overwrites)
	$(UV) run python scripts/make_env.py

install: ## Install Python (uv) and web (pnpm) dependencies + git hooks
	$(UV) sync
	cd $(WEB) && $(PNPM) install
	$(UV) run pre-commit install

# ---------------------------------------------------------------- docker stack
up: env ## Build and start the whole stack (postgres, redis, migrate, api, worker, latex, web)
	$(COMPOSE) up -d --build --wait
	@echo "Dashboard: http://localhost:3000   API docs: http://localhost:8000/api/v1/docs"

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

restart: ## Recreate api and worker so they reload .env (plain `docker compose restart` does not)
	$(COMPOSE) up -d --force-recreate --no-deps --wait api worker

logs: ## Follow logs (s=<service> to filter)
	$(COMPOSE) logs -f --tail=100 $(s)

ps: ## Show service status and health
	$(COMPOSE) ps

demo: env ## Start the stack on synthetic demo data (a separate database; `make up` switches back)
	$(COMPOSE) up -d --wait postgres redis
	$(COMPOSE) exec -T postgres sh -c "psql -U jobpilot -d postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='jobpilot_demo'\" | grep -q 1 || createdb -U jobpilot jobpilot_demo"
	$(DEMO) up -d --build --wait
	$(DEMO) exec -T worker python - < scripts/seed_demo.py
	@echo "Demo dashboard: http://localhost:3000 (synthetic data). Run 'make up' to return to your data."

demo-reset: ## Reload the demo data (the demo stack must be running)
	$(DEMO) exec -T worker python - < scripts/seed_demo.py

infra: env ## Start only postgres and redis (for host development and tests)
	$(COMPOSE) up -d --wait postgres redis

migrate: ## Apply migrations (in the stack)
	$(COMPOSE) run --rm migrate

migrate-local: ## Apply migrations from the host against DATABASE_URL
	$(UV) run alembic -c packages/core/alembic.ini upgrade head

revision: ## Autogenerate a migration: make revision m="add something"
	$(UV) run alembic -c packages/core/alembic.ini revision --autogenerate -m "$(m)"

password: ## Set the dashboard password (hash only, in .env); SHOW=1 echoes what you type
	$(UV) run python scripts/set_password.py $(if $(SHOW),--show,)

# ---------------------------------------------------------------- gmail
gmail-auth: ## Connect Gmail (opens Google's consent page; see docs/setup/google-oauth.md)
	$(UV) run python -m jobpilot_mcp_gmail.auth --scope triage

gmail-auth-send: ## Allow JobPilot to send approved applications from Gmail (separate gmail.send token)
	$(UV) run python -m jobpilot_mcp_gmail.auth --scope send

triage: ## Start a Gmail sync + triage run in the worker and follow its progress
	@tid=$$(curl -sf -X POST http://127.0.0.1:8000/api/v1/gmail/triage | $(UV) run python -c "import sys,json;print(json.load(sys.stdin)['task_id'])") && 	  echo "task $$tid" && curl -sN http://127.0.0.1:8000/api/v1/tasks/$$tid/events | grep --line-buffered '^data:'

# ---------------------------------------------------------------- quality
test: ## Run all Python and web tests
	$(UV) run pytest
	cd $(WEB) && $(PNPM) test

test-unit: ## Python unit tests only
	$(UV) run pytest tests/unit

test-integration: ## Python integration tests (needs `make infra`)
	$(UV) run pytest tests/integration

eval: ## LLM evaluation suites (classification accuracy, injection precision/recall)
	$(UV) run pytest -m eval -o addopts="" tests/eval -v -s

lint: ## Lint Python and web
	$(UV) run ruff check $(PY_SRC)
	$(UV) run ruff format --check $(PY_SRC)
	cd $(WEB) && $(PNPM) lint && $(PNPM) format:check

fmt: ## Auto-format Python and web
	$(UV) run ruff check --fix $(PY_SRC)
	$(UV) run ruff format $(PY_SRC)
	cd $(WEB) && $(PNPM) format

typecheck: ## mypy (strict) + tsc
	$(UV) run mypy $(PY_SRC)
	cd $(WEB) && $(PNPM) typecheck

check: lint typecheck test ## Everything CI runs

e2e: ## Playwright journeys on an isolated stack with a stub LLM and Gmail (needs `make infra`)
	$(UV) run python -m tests.e2e.run_stack

gen-client: ## Regenerate the typed TS API client from FastAPI's OpenAPI schema
	$(UV) run python -m jobpilot_api.export_openapi > $(WEB)/openapi.json
	cd $(WEB) && $(PNPM) gen:api

# ---------------------------------------------------------------- host development
api-dev: ## Run the API on the host with reload (needs `make infra`)
	$(UV) run uvicorn jobpilot_api.main:app --reload --host 127.0.0.1 --port 8000

worker-dev: ## Run the worker on the host (needs `make infra`)
	$(UV) run arq jobpilot_worker.main.WorkerSettings --watch packages

web-dev: ## Run the dashboard on the host with hot reload (lighter than the container)
	cd $(WEB) && API_INTERNAL_URL=http://127.0.0.1:8000 $(PNPM) dev

clean: ## Remove caches and build output
	rm -rf .pytest_cache .mypy_cache .ruff_cache $(WEB)/.next
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
