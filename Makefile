# Shortcuts for the Docker Compose dev environment. Run `make help`.
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help up down logs migrate seed test shell api-schema api-client api-check

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

up: ## Build and start all services in the background
	$(COMPOSE) up -d --build

down: ## Stop all services (the database volume is kept)
	$(COMPOSE) down

logs: ## Follow service logs
	$(COMPOSE) logs -f

migrate: ## Apply Django migrations
	$(COMPOSE) exec api python manage.py migrate

seed: ## Create sample data and dev users (needs DEBUG on; see backend/README.md)
	$(COMPOSE) exec api python manage.py seed_dev_data

test: ## Run backend tests in a one-off api container (installs dev dependencies)
	$(COMPOSE) run --rm api sh -c "uv sync --frozen && pytest"

shell: ## Open a Django shell
	$(COMPOSE) exec api python manage.py shell

api-schema: ## Regenerate docs/api/openapi.yaml from the backend (needs uv, no database)
	cd backend && DJANGO_SETTINGS_MODULE=config.settings.test uv run python manage.py spectacular --validate --fail-on-warn --file ../docs/api/openapi.yaml

api-client: api-schema ## Regenerate the schema and the frontend types (needs npm)
	cd frontend && npm run gen:api

api-check: api-client ## Fail if the committed schema or generated types are stale
	git diff --exit-code -- docs/api/openapi.yaml frontend/src/lib/api/schema.ts
