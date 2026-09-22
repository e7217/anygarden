.PHONY: install dev server server-dev frontend test lint e2e e2e-setup clean

install:                ## Install all dependencies
	uv sync --dev
	cd frontend && npm install

DEV_PORT ?= 8001

# No migration step here on purpose (#647). The server migrates its own
# database during startup (`_ensure_schema_ready` in anygarden/app.py),
# against the URL it actually opens. The `migrate` target that used to
# run first used alembic.ini's hardcoded URL, which pointed at a file the
# app never opens -- so it migrated a phantom DB and reported success.
dev:                    ## Run server + frontend concurrently (dev mode)
	$(MAKE) -j2 server-dev frontend

server:                 ## Run backend server
	uv run anygarden-server

server-dev:             ## Run backend server in dev mode (reload, port 8001)
	ANYGARDEN_PORT=$(DEV_PORT) uv run uvicorn anygarden.app:create_app --factory --reload --host 0.0.0.0 --port $(DEV_PORT) --log-level debug --ws-ping-interval 60 --ws-ping-timeout 600

frontend:               ## Run frontend dev server
	cd frontend && npm install --silent && npm run dev

test:                   ## Run backend tests
	uv run pytest -v

lint:                   ## Run linter
	uv run ruff check anygarden tests

e2e:                    ## Run full E2E pipeline test
	uv run python scripts/e2e_full_pipeline.py

# Deliberately without --with-deps (which CI uses): that switch shells out to
# sudo apt-get and fails on any workstation without passwordless sudo. Run
# `npx playwright install-deps chromium` yourself if chromium won't launch.
e2e-setup:              ## Install the Playwright browser for frontend/ e2e (one-off)
	cd frontend && npx playwright install chromium

clean:                  ## Remove build artifacts and caches
	rm -rf dist/ .pytest_cache/ __pycache__/
	find . -type d -name __pycache__ -exec rm -rf {} +

help:                   ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
