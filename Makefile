# Shortcuts for the Docker Compose dev environment. Run `make help`.
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help up down logs migrate test shell

help: ## Show this help
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

up: ## Build and start all services in the background
	$(COMPOSE) up -d --build

down: ## Stop all services (the database volume is kept)
	$(COMPOSE) down

logs: ## Follow service logs
	$(COMPOSE) logs -f

migrate: ## Apply Django migrations
	$(COMPOSE) exec api python manage.py migrate

test: ## Run backend tests in a one-off api container (installs dev dependencies)
	$(COMPOSE) run --rm api sh -c "uv sync --frozen && pytest"

shell: ## Open a Django shell
	$(COMPOSE) exec api python manage.py shell
