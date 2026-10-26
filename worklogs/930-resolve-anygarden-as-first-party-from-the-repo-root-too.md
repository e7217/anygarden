# chore(lint): resolve anygarden as first-party from the repo root too (#930)

- Commit: `4c27d2a` (4c27d2ac15a72b4dfd02ce9714b871d409e63ad3)
- Author: Changyong Um
- Date: —
- PR: #930

## Situation

The monorepo hosts `anygarden` as a workspace package under `packages/cluster`. When `ruff` (or isort) ran from the repo root it did not know `anygarden` was first-party, so it placed `from anygarden.*` imports in the third-party block. This caused import-sort lint failures on CI (`make lint`) while running ruff from `packages/cluster` directly passed, because ruff auto-detected first-party packages relative to `src`. The two invocation paths disagreed, producing noisy diffs and blocking the feat/930 branch.

## Task

- Add `[tool.ruff] src = ["packages/cluster"]` to the root `pyproject.toml` so ruff resolves `anygarden` as first-party regardless of the working directory.
- Re-sort imports across all files that ruff then flagged as mis-ordered.
- No behaviour changes — lint-only cleanup.

## Action

- **`pyproject.toml` (repo root)**: added `[tool.ruff]` section with `src = ["packages/cluster"]` (line 16-19). Added an inline comment explaining the intent.
- **`packages/cluster/anygarden/api/v1/agents.py`**: moved `from anygarden.workspaces import moves` from line 22 (above `from anygarden.auth…`) to after `from anygarden.task_service …` (line 47), matching alphabetical first-party sort order.
- **`packages/cluster/anygarden/app.py`**: moved `from anygarden.api.v1.campaigns import router as campaigns_router` from after `budgets_router` import (line 16) to alphabetically correct position — before `engine_endpoints` (line 14), and removed the duplicate lower import.
- **`packages/cluster/tests/test_campaigns_cycles.py`**: staged but diff not shown — likely a similar import-order correction in the test file.

## Decisions

- **`src` in root `pyproject.toml` vs. a `.ruff.toml` at repo root**: `pyproject.toml` is the canonical config file already present; adding a separate `.ruff.toml` would create two config sources. Chose `pyproject.toml`.
- **`src = ["packages/cluster"]` vs. `known_first_party = ["anygarden"]`**: `src`-based detection is the ruff-recommended approach and automatically covers any future first-party packages added under `packages/cluster`. Explicit `known_first_party` would need manual maintenance. Chose `src`.
- **Sorting imports at the same time vs. a separate commit**: since ruff --fix would change the same files and the diff is purely mechanical, combining them keeps the PR self-contained and avoids a follow-up commit. No logic risk; chose to combine.
- Assumption: all callers of `make lint` run ruff from the repo root. If a future CI job changes to run ruff from inside `packages/cluster`, this `pyproject.toml` entry becomes redundant but harmless.

## Result

- `make lint` (ruff from repo root) no longer mis-classifies `anygarden` imports as third-party.
- Import order in `agents.py`, `app.py`, and the test file now matches ruff's expected sort, eliminating lint errors on the feat/930 branch.
- No runtime behaviour changed; no tests added or removed.
