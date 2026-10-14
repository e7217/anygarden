# fix(tasks): deliver assignment messages to the assignee only through the leased outbox (#824)

- Commit: `ac94b1e` (ac94b1e4d5f7aceb29870472a5dd5fb8b0006947)
- Author: Changyong Um
- Date: 2026-10-14T14:58:53+09:00
- PR: #824

## Situation

REST (`POST /api/v1/rooms/{room_id}/tasks`, `create_message_task`, `update_task`, `requeue_task`) and MCP assignment paths each created a durable turn for the assignee and then broadcast the same message to the whole room—including the assignee. The broadcast copy carried no turn lease. When it arrived before the outbox could dispatch the durable copy, `handler_started` moved the still-pending attempt to `started`, the outbox dispatcher found nothing to lease, and every subsequent execution tool call from the agent was refused with `"Execution tools require the current delivered turn lease"`.

## Task

- Replace all `manager.broadcast(room_id, _message_to_frame(msg))` calls in assignment paths with `broadcast_project_messages`, which explicitly excludes the durable turn's target from the fan-out.
- Add a guard in `turns/service.py::record_lifecycle` to reject lifecycle frames for an attempt that is still in `pending` state (i.e., arrived before the outbox leased it).
- Add `EXE-22` to `docs/requirements.md` with tests covering all assignment paths and an end-to-end lease check through `mark_task_status`.

## Action

- **`packages/cluster/anygarden/api/v1/tasks.py`**
  - Removed `_message_to_frame` helper and its imports (`message_to_frame`, `MessageOut`).
  - Added `_broadcast_assignment(db, request, msg)` which delegates to `broadcast_project_messages`; used at lines ~447, ~550, ~810, ~817, ~953 (create, message-task, update/woken-deps, requeue).
- **`packages/cluster/anygarden/mcp/router.py`** (~277)
  - Collapsed the prior branching logic (`execution_id` check → different broadcast paths) for woken dependency messages into a single unconditional `broadcast_project_messages` call.
- **`packages/cluster/anygarden/turns/service.py`** (~1350)
  - Inserted a `pending`-state fence inside `record_lifecycle`: if the attempt is still `pending`, the frame is audited as `lifecycle_before_lease` and `False` is returned, aborting the state transition.
- **`packages/cluster/tests/test_tasks_api.py`** — `TestAssignmentDeliveryOnlyThroughOutbox`: create, message-task, reassign, requeue and REST dependency-wake paths leave the assignee out of the live broadcast; an end-to-end test delivers the outbox frame and calls `mark_task_status` (`in_progress` → `done`) with its lease, then checks the MCP dependency-wake path the same way.
- **`packages/cluster/tests/test_durable_turns.py`** — a lease-less `handler_started` on a pending attempt leaves it pending, and the next outbox delivery still leases it.
- **`docs/requirements.md`** — one-line registration of `EXE-22`.

## Decisions

- **`broadcast_project_messages` vs. filtered `manager.broadcast`**: `broadcast_project_messages` already knows the durable turn's target and excludes it; adding that filtering inline to `manager.broadcast` would have required passing the target identity down to the WebSocket layer. Using the existing abstraction was both simpler and correct by construction.
- **Pending-state fence in `record_lifecycle` vs. filtering at the WebSocket receive layer**: Filtering at receive is fragile (race window is between DB commit and the broadcast, hard to unit-test). A fence in `record_lifecycle` is the canonical place for attempt-state invariants and is fully unit-testable.
- **Rejected: treating broadcast copy as a no-op if a lease exists**: Would require an additional DB round-trip per broadcast and still wouldn't close the race for the first delivery.
- **Assumption**: `broadcast_project_messages` continues to exclude the durable turn's target. If its semantics change, the lease race may reopen; that function's contract should be covered by its own tests.

## Result

Assignment messages now reach room members via the lease-less broadcast and reach the assignee exclusively via the outbox with the turn lease attached. The pending-state fence prevents any out-of-band lifecycle frame from advancing a not-yet-leased attempt. Cluster suite: 2941 passed, 5 skipped; `EXE-22` reports pass with 7 tests. Ruff error count unchanged from main. Not yet verified against a live Codex agent.
