.PHONY: install setup test lint clean dev release-agent release-machine release-cluster

install:                ## Install all packages (workspace)
	uv sync --all-packages

setup:                  ## One-time dev setup: install + activate git hooks
	git config core.hooksPath .githooks
	uv sync --all-packages
	@echo "[setup] git hooks enabled via .githooks/ — 'git pull' will auto-run 'uv sync --all-packages'."

test:                   ## Run tests across all packages
	uv run pytest packages/

lint:                   ## Run ruff across all packages
	uv run ruff check packages/

dev:                    ## Run cluster dev server + frontend
	$(MAKE) -C packages/cluster dev

release-agent:          ## Build and publish doorae-agent to PyPI
	cd packages/agent && rm -rf dist/ && uv build && twine upload dist/*

release-machine:        ## Build and publish doorae-machine to PyPI
	cd packages/machine && rm -rf dist/ && uv build && twine upload dist/*

release-cluster:        ## Build and publish doorae-cluster to PyPI
	cd packages/cluster && rm -rf dist/ && uv build && twine upload dist/*

clean:                  ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name dist -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +

help:                   ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
