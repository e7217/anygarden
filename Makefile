.PHONY: install setup test lint clean dev release-agent release-cluster req-report bench-report

install:                ## Install all packages (workspace)
	uv sync --all-packages --all-extras

setup:                  ## One-time dev setup: install + activate git hooks
	git config core.hooksPath .githooks
	uv sync --all-packages --all-extras
	@echo "[setup] git hooks enabled via .githooks/ — 'git pull' will auto-run 'uv sync --all-packages --all-extras'."

test:                   ## Run tests across all packages
	uv run pytest packages/

req-report:             ## Requirement coverage from pytest JUnit (frontend layers need CI's vitest/e2e JUnit)
	mkdir -p .tmp/junit
	cd packages/agent && uv run pytest -q --junitxml=../../.tmp/junit/agent.xml
	cd packages/cluster && uv run pytest -q -n logical --junitxml=../../.tmp/junit/cluster.xml
	python3 scripts/req_report.py .tmp/junit/*.xml --spec docs/requirements.md

bench-report:           ## Trend report from committed live benchmark results
	uv run python -m benchmarks.runner report

lint:                   ## Run ruff across all packages
	uv run ruff check packages/

dev:                    ## Run cluster dev server + frontend
	$(MAKE) -C packages/cluster dev

release-agent:          ## Build and publish anygarden-agent to PyPI
	rm -rf dist/ && uv build --package anygarden-agent && twine upload dist/anygarden_agent-*

release-cluster:        ## Build and publish anygarden to PyPI (source dir kept as packages/cluster/)
	rm -rf dist/ && uv build --package anygarden && twine upload dist/anygarden-*

clean:                  ## Remove build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name dist -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +

help:                   ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
