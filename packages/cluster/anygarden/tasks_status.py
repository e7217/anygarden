"""Canonical task-status vocabulary — the single source of truth.

A task's ``status`` is a free ``String(32)`` column in the DB, so the
legal set lives in code. It is consumed in two independent surfaces:

- the MCP ``mark_task_status`` tool (``mcp/tools.py``), and
- the REST ``POST /rooms/{id}/tasks`` / ``PUT /tasks/{id}`` schemas
  (``api/v1/tasks.py``).

History (#319): ``failed`` was once stamped by the goals sweeper while the
LLM-facing enum still rejected it, so an agent marking its own task failed
got a 4xx. Keeping the set in one tiny, import-light module — rather than
duplicating a ``Literal[...]`` in each schema — structurally prevents that
drift. ``mcp/tools.py`` re-exports ``TASK_STATUS_VALUES`` from here so the
historical import path is preserved.

This module imports nothing from the app graph (no FastAPI, no models), so
either consumer can pull it in at import time without dragging in the MCP
router or risking a cycle.
"""

from __future__ import annotations

# Terminal task statuses — a blocker is "satisfied" once it reaches one of
# these. Reused by the #459 resolve-wake hook and the cycle/blocker walks.
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed"})

# Allowed task status values (#266, #319). ``failed`` (#319) joined the
# legal set when the goals sweeper started stamping it on pickup/execution
# timeouts; before that the LLM-facing enum and the sweeper's stored value
# drifted, so an agent that wanted to mark its own task as failed got a 4xx
# from the MCP RPC.
TASK_STATUS_VALUES: tuple[str, ...] = (
    "todo",
    "in_progress",
    "done",
    "blocked",
    "failed",
)

__all__ = ["TASK_STATUS_VALUES", "TERMINAL_STATUSES"]
