# feat(campaigns): react to execution ends, sweeps and room archives at once (#930)

- Commit: `9d3b537` (9d3b537)
- Author: Claude Opus 5.5
- Date: 2026-10-26
- PR: #930

## Situation

The campaign cycle lifecycle was implemented in isolation: a cycle's execution could complete or be cancelled without the campaign tracking that result. Similarly, the stuck-task sweeper could fail a cycle Task without closing its cycle, and room archive/retire endpoints could archive rooms that still owned running campaigns. There was also no guard preventing the generic Task API or `mark_task_status` MCP tool from directly mutating a campaign cycle Task's status, which would bypass the evaluation pipeline.

## Task

Wire campaign cycle lifecycle hooks into four existing callsites — all within the caller's transaction — so that no cycle ends without the campaign evaluating it:

1. **Execution terminal events** (`_event` in `project_executions/service.py`): call `on_execution_terminal` for all four terminal event types.
2. **Stuck-task sweeper** (`goals/sweeper.py`): when a cycle Task is failed due to pickup/execution timeout, call `on_cycle_task_closed`.
3. **Room retire** (`rooms/retire.py`): call `stop_room_campaigns` before archiving (CMP-12).
4. **Room archive endpoint** (`rooms/router.py`): call `stop_room_campaigns` for the archived room and all descendants (CMP-12).
5. **Generic Task API** (`api/v1/tasks.py`): reject `PUT /tasks/{id}`, `POST /tasks/{id}/requeue`, and `DELETE /tasks/{id}` on open cycle Tasks with HTTP 409 `CAMPAIGN_CYCLE_TASK_MANAGED`.
6. **MCP `mark_task_status`** (`mcp/tools.py`): reject `done`/`failed`/`blocked` transitions on a non-terminal campaign cycle Task with the same error code, directing agents to use `complete_project_execution` instead.
7. Register `CAMPAIGN_CYCLE_TASK_MANAGED` in the public error code registry (`api/v1/errors.py`).

## Action

- **`packages/cluster/anygarden/api/v1/errors.py`**: Added `"CAMPAIGN_CYCLE_TASK_MANAGED"` to `PUBLIC_ERROR_CODES`.
- **`packages/cluster/anygarden/api/v1/tasks.py`**: Added `_reject_open_cycle_mutation(task)` helper (imports `cycle_task_is_open` lazily); called it in `update_task` (when status or assignee changes), `requeue_task`, and `delete_task`.
- **`packages/cluster/anygarden/goals/sweeper.py`**: After `fail_stale_task_cas` succeeds, checks `task.campaign_id is not None` and calls `on_cycle_task_closed(db, task, reason=reason, clock=lambda: now)`.
- **`packages/cluster/anygarden/mcp/tools.py`**: In `mark_task_status`, added a guard that returns `_error_result("CAMPAIGN_CYCLE_TASK_MANAGED: ...")` when the task has a `campaign_id`, status is `done`/`failed`/`blocked`, and it is not yet terminal.
- **`packages/cluster/anygarden/project_executions/service.py`**: Added `_CAMPAIGN_TERMINAL_EVENTS` frozenset; after a successful `_event` insert calls `on_execution_terminal(db, execution, event_type)` for those four event types.
- **`packages/cluster/anygarden/rooms/retire.py`**: After cancelling open executions/turns, calls `stop_room_campaigns(db, room.id, actor=f"user:{identity.id}")` before setting `archived_at`.
- **`packages/cluster/anygarden/rooms/router.py`**: In `archive_room`, iterates `[room_id, *descendant_ids]` and calls `stop_room_campaigns` for each before commit.
- **Tests** (`test_api_errors.py`, `test_campaigns_execution.py`, `test_campaigns_cycles.py`): Ported storage spike scenarios to the real services; S8c now asserts the 409 response and that attempt history survives a Task delete attempt; added `mark_task_status` case; coverage for evaluation dispatch, deadlines, late results and judging.

## Decisions

- **Hook into the caller's transaction rather than using a background job**: Guarantees that no execution ends without the cycle being accounted for. A background job could miss or double-process an event under load, and the event key on `ProjectExecutionEvent` provides natural idempotency.
- **`_CAMPAIGN_TERMINAL_EVENTS` frozenset over a conditional check**: Makes the set of terminal events explicit and testable, consistent with how the existing terminal state machine is expressed elsewhere in the codebase.
- **409 `CAMPAIGN_CYCLE_TASK_MANAGED` vs. silently ignoring**: Returning an error rather than a no-op ensures callers (human operators and agents) learn that direct Task mutation is the wrong path; a silent no-op would mask bugs. The API error code is registered in `PUBLIC_ERROR_CODES` so it carries a stable contract.
- **`mark_task_status` returns `_error_result` instead of raising**: Consistent with the MCP tool's existing error contract — MCP tool errors are returned as result objects the LLM can read, not raised as HTTP exceptions.
- **Lazy imports for campaign modules**: Avoids circular import chains between `mcp/tools`, `goals/sweeper`, `rooms/*` and the `campaigns` package at module load time.

## Result

Campaign cycles now close correctly when their execution ends (any of the four terminal events), when the sweeper fires on a stuck cycle Task, or when the owning room is archived or retired. The generic Task API and `mark_task_status` reject mutations that would bypass the evaluator. Tests cover these paths including the 409, surviving attempt history, deadline handling, late results and judging.
