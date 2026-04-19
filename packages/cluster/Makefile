.PHONY: install dev server server-dev frontend test lint migrate e2e clean

install:                ## Install all dependencies
	uv sync --dev
	cd frontend && npm install

DEV_PORT ?= 8001

dev: migrate           ## Run server + frontend concurrently (dev mode, migrating first)
	$(MAKE) -j2 server-dev frontend

server:                 ## Run backend server
	uv run doorae-server

server-dev:             ## Run backend server in dev mode (reload, port 8001)
	uv run uvicorn doorae.app:create_app --factory --reload --host 0.0.0.0 --port $(DEV_PORT) --log-level debug

frontend:               ## Run frontend dev server
	cd frontend && npm install --silent && npm run dev

test:                   ## Run backend tests
	uv run pytest -v

lint:                   ## Run linter
	uv run ruff check doorae tests

migrate:                ## Run alembic migrations
	uv run alembic upgrade head

e2e:                    ## Run full E2E pipeline test
	uv run python scripts/e2e_full_pipeline.py

clean:                  ## Remove build artifacts and caches
	rm -rf dist/ .pytest_cache/ __pycache__/
	find . -type d -name __pycache__ -exec rm -rf {} +

help:                   ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
