# fix(api): enforce input validation on project/task/secret create (#471)

- Commit: `5d27b89` (5d27b89338baf8bd4ebfe2c3e68aeb7d71aaf040)
- Author: Changyong Um
- Date: 2026-06-22T12:27:20+09:00
- PR: #471

## Situation

QA review surfaced four input-validation gaps where Pydantic request models accepted unconstrained raw `str` and persisted meaningless values with a 2xx, diverging from sibling create schemas (`goals.py`, `auth/routes.py`) that already enforce `Field(min_length=1)`:

1. `POST /api/v1/projects` accepted an empty `name` (`""`) → 201.
2. `POST /api/v1/rooms/{id}/tasks` accepted an empty `title` (`""`) → 201.
3. Task `status` was a free `String(32)` with no enum check on either `TaskCreate.status` or `TaskUpdate.status`, so any arbitrary string (e.g. `"not-a-status"`) was stored — out of band from the canonical set the MCP `mark_task_status` path enforces.
4. `POST /api/v1/llm-gateway/secrets` accepted a shell-unsafe `env_var_name` (e.g. `"bad-name"`, leading-digit `"1KEY"`), even though that name is interpolated into the gateway child process' environment.

## Task

- Reject all four with a 422 at the schema edge, matching the existing validated-create convention.
- Validate task status against a single source of truth rather than hardcoding a `Literal[...]` in the schema, because the codebase already had one status-vocabulary drift incident (#319) — the MCP `mark_task_status` enum and the goals sweeper's stored `failed` value diverged. Reuse `TASK_STATUS_VALUES` (`mcp/tools.py`) so the new REST validation can't drift from the MCP path.
- Keep the optional `TaskUpdate.status` a no-op on partial updates (title/assignee-only changes) — the validator must pass `None` through.
- Do not break existing seed/bootstrap data: conventional upper-snake env var names must still pass.

## Action

- `packages/cluster/anygarden/tasks_status.py` (new) — hoisted the canonical `TASK_STATUS_VALUES` and `TERMINAL_STATUSES` into a tiny, import-light module (no FastAPI / models / router imports). This lets the REST schemas in `api/v1/tasks.py` reuse the vocabulary at import time without dragging the MCP router (~0.37s cold import) into their import graph, and structurally prevents the #319 drift.
- `packages/cluster/anygarden/mcp/tools.py` — replaced the inline `TASK_STATUS_VALUES` / `TERMINAL_STATUSES` definitions with a re-export `from anygarden.tasks_status import ...`, preserving the historical `from anygarden.mcp.tools import TASK_STATUS_VALUES` import path used by the resolve-wake hook and tool schemas.
- `packages/cluster/anygarden/api/v1/projects.py` — `ProjectCreate.name` → `Field(min_length=1, max_length=255)` (255 mirrors the `Project.name String(255)` column).
- `packages/cluster/anygarden/api/v1/tasks.py` — `TaskCreate.title` → `Field(min_length=1, max_length=500)`; added a shared `_validate_task_status` helper wired as a `@field_validator("status")` on both `TaskCreate` and `TaskUpdate` (raises `ValueError` → 422 for an out-of-set status, passes `None` through).
- `packages/cluster/anygarden/api/v1/llm_gateway.py` — `SecretCreate.env_var_name` gained `pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"` (POSIX env-var shape; rejects hyphens and leading digits).
- Tests (TDD, Red→Green): `test_projects.py` (empty-name 422); `test_tasks_api.py::TestTaskInputValidation` (empty title 422, unknown status 422 on create + update, canonical status accepted on both, partial update without status still 200); `test_llm_gateway_admin_api.py` (invalid `env_var_name` 422 for hyphen + leading-digit, valid upper-snake 201).

## Decisions

- **Status validation via `field_validator` + canonical reuse, not `Literal[...]`.** A hardcoded `Literal` would auto-generate the OpenAPI enum but duplicate the status set, re-creating exactly the dual-source condition that caused #319. Reusing the single canonical tuple was the safer structural choice; the loss of an auto-generated OpenAPI enum is an acceptable trade for guaranteed single-sourcing.
- **Shared module extraction over a direct `from anygarden.mcp.tools import`.** The import was verified non-circular, but `anygarden.mcp`'s `__init__` eagerly pulls in the MCP router. Rather than couple the tasks API to that surface just to read a 5-element tuple, the vocabulary was moved to `anygarden/tasks_status.py` and re-exported — the plan's contingency for a "heavy" import.
- **`env_var_name` pattern allows lowercase.** POSIX permits lowercase env vars and they are valid in a shell, so the pattern only blocks structurally-broken names (hyphens, leading digits, special chars); it does not force upper-case.

## Result

All four surfaces now return 422 on invalid input while continuing to accept well-formed values. Verified no regression to existing data shapes (every `env_var_name` literal in code/tests is conventional upper-snake and passes). Full cluster suite: **1184 passed, 1 deselected** (122s), including the new validation tests; the MCP status tests (`test_mark_task_status`, `test_task_blockers`, `test_create_task_tool`) stay green after the re-export. `ruff check` clean on all modified/new files.
