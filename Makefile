# ThreadLoop task runner
# `make help` lists everything.

COMPOSE := docker compose -f infra/docker/docker-compose.yml --env-file .env

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------- Bootstrap ----------

.PHONY: env
env: ## Copy .env.example -> .env if missing
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

.PHONY: install
install: ## Install all workspace deps (web, mobile, shared)
	cd shared && npm install
	cd frontend-web && npm install
	cd frontend-mobile && npm install

# ---------- Local stack ----------

.PHONY: dev
dev: env ## Start full stack (db, redis, meili, api, web) via docker compose
	$(COMPOSE) up --build

.PHONY: dev-detached
dev-detached: env ## Start stack in background
	$(COMPOSE) up --build -d

.PHONY: down
down: ## Stop stack
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop stack and remove volumes (DESTROYS local data)
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f

.PHONY: ps
ps: ## Show running services
	$(COMPOSE) ps

# ---------- Database ----------

.PHONY: migrate
migrate: ## Apply Alembic migrations inside the backend container
	$(COMPOSE) exec backend alembic upgrade head

.PHONY: migration
migration: ## Create a new Alembic revision: `make migration m="add foo"`
	$(COMPOSE) exec backend alembic revision --autogenerate -m "$(m)"

.PHONY: psql
psql: ## Open psql shell against the dev database
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-threadloop} -d $${POSTGRES_DB:-threadloop}

# ---------- Quality ----------

.PHONY: lint
lint: ## Lint backend + web
	$(COMPOSE) exec backend ruff check .
	cd frontend-web && npm run lint

.PHONY: format
format: ## Auto-format backend + web
	$(COMPOSE) exec backend ruff format .
	cd frontend-web && npm run format

.PHONY: test
test: test-backend test-web ## Run all tests

.PHONY: test-backend
test-backend: ## Run Pytest in backend container
	$(COMPOSE) exec backend pytest -q

.PHONY: test-web
test-web: ## Run web unit tests
	cd frontend-web && npm test --silent

.PHONY: e2e
e2e: ## Run Cypress E2E against running stack
	cd frontend-web && npm run cypress:run

# ---------- Health ----------

.PHONY: health
health: ## Curl the API health endpoint
	@curl -fsS http://localhost:$${BACKEND_PORT:-8000}/api/health | jq . || \
		echo "API not reachable on :$${BACKEND_PORT:-8000} — is the stack up?"
