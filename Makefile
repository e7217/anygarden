.PHONY: install setup test lint clean dev release-agent release-machine release-cluster

install:                ## Install all packages (workspace) + litellm gateway CLI (#197)
	uv sync --all-packages
	@# Install the litellm CLI as a uv tool so doorae-server can spawn
	@# it as a subprocess via PATH (#197 / ADR-004). Using ``uv tool install``
	@# instead of ``uvx`` gives version stability and zero per-invocation
	@# overhead — see docs/design/12-llm-gateway.md §12.5.
	@uv tool install 'litellm[proxy]' 2>&1 | tail -2 || true

setup:                  ## One-time dev setup: install + activate git hooks
	git config core.hooksPath .githooks
	uv sync --all-packages
	@uv tool install 'litellm[proxy]' 2>&1 | tail -2 || true
	@echo "[setup] git hooks enabled via .githooks/ — 'git pull' will auto-run 'uv sync --all-packages'."

test:                   ## Run tests across all packages
	uv run pytest packages/

lint:                   ## Run ruff across all packages
	uv run ruff check packages/

dev:                    ## Run cluster dev server + frontend
	$(MAKE) -C packages/cluster dev

release-agent:          ## Build and publish dragent to PyPI
	cd packages/agent && rm -rf dist/ && uv build && twine upload dist/*

release-machine:        ## Build and publish drmachine to PyPI
	cd packages/machine && rm -rf dist/ && uv build && twine upload dist/*

release-cluster:        ## Build and publish drhub to PyPI (source dir kept as packages/cluster/)
	cd packages/cluster && rm -rf dist/ && uv build && twine upload dist/*

clean:                  ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name dist -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +

help:                   ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
