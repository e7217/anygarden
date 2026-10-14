# build: list the workspace member explicitly (#832)

- Commit: `af3794d` (af3794d7cbe4b6e2f1bbc9e91b9852b6f145b24c)
- Author: Changyong Um
- Date: 2026-10-14T16:23:29+09:00
- PR: #832

## Situation

PR #832 removed the `packages/agent` directory from the monorepo. However, existing
checkouts retained a stale `packages/agent/` directory (containing caches and an old
`uv.lock`) that was no longer tracked by git. The `pyproject.toml` workspace glob
`packages/*` still matched this leftover directory, causing `uv sync` to fail with
"Workspace member packages/agent is missing a pyproject.toml".

## Task

- Fix `uv sync` failures on existing checkouts that still have the `packages/agent/`
  directory lingering from before #832.
- Ensure the workspace member list is explicit and cannot accidentally match stale or
  future directories that lack a `pyproject.toml`.

## Action

- `pyproject.toml` (line 11): Changed `members = ["packages/*"]` to
  `members = ["packages/cluster"]`, listing the only remaining workspace member
  explicitly instead of using a glob.

## Decisions

- **Glob vs. explicit list**: The `packages/*` glob was convenient when multiple
  packages existed but became fragile once only `packages/cluster` remained. Any
  leftover directory (from old checkouts, CI caches, etc.) would break `uv sync`.
- **Explicit listing chosen**: Listing `packages/cluster` by name is the minimal,
  safe fix; it fails loudly if `packages/cluster` disappears rather than silently
  succeeding with zero members.
- **Alternative — delete the stale directory in CI/scripts**: Rejected as it would
  require every developer and CI pipeline to run a cleanup step; fixing the source
  (the glob) is more robust.
- **Assumption**: If new workspace members are added under `packages/`, `pyproject.toml`
  must be updated manually. Forgetting to do so would cause them to be silently excluded
  from the workspace — worth revisiting if the packages directory grows again.

## Result

`uv sync` succeeds on existing checkouts without requiring manual deletion of the
stale `packages/agent/` directory. No behavior change for clean checkouts.
