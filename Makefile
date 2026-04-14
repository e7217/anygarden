.PHONY: install test lint clean dev

install:                ## Install all packages (workspace)
	uv sync --all-packages

test:                   ## Run tests across all packages
	uv run pytest packages/

lint:                   ## Run ruff across all packages
	uv run ruff check packages/

dev:                    ## Run cluster dev server + frontend
	$(MAKE) -C packages/cluster dev

clean:                  ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name dist -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +

help:                   ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
