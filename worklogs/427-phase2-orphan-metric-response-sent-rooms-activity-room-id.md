# feat(observability): Phase 2 — orphan metric, response_sent span, /rooms/{id}/activity, room_id column (#427)

- Commit: `f55c089` (f55c089 on branch feat/427-instrumentation-phase2)
- Author: Changyong Um
- Date: 2026-06-09
- PR: #427

## Situation

Phase 1 (#425) closed the data-consumption gaps. Phase 2 prepares the ground for
Phase 3's multi-agent flow view and makes a turn's start *and* end honest in the
trace. Per the assessment (`docs/llm-instrumentation-state.html`): orphaned turns
were buried in ActivityLog (not alertable), `response_sent` was invisible on the
trace (root closes on `handler_finished`), per-room timelines required a
full-scan + json_extract because `room_id` lived only in `details` JSON, and the
DB orphan sweeper and in-memory span reaper ran on two independent timers.

## Task

- Surface orphaned turns as a metric and reconcile the DB/​span orphan mechanisms.
- Put the delivered reply on the trace.
- Add a per-room activity endpoint backed by an indexed `room_id` column.
- Revive the dead `machines_online` / `agents_by_state` gauges.
- Constraints: gateway-free; bounded metric labels; migration must apply +
  backfill; admin-gate the room endpoint (matches `/agents/{id}/activity`).

## Action

- `scheduler/lifecycle.py`: `sweep_orphaned_requests` now returns `list[str]` of
  newly-orphaned `request_id`s (was `int`); the orphan-row write also fills the new
  `room_id` column.
- `app.py`: `_run_orphan_sweeper` bumps `agent_turns_orphaned_total` by `len` and
  calls `tracing.reap_request(rid)` per id; new `_reconcile_agents_by_state` sets
  the gauge from a COUNT GROUP BY on the 60s cadence.
- `observability/metrics.py`: `agent_turns_orphaned_total` counter.
- `observability/tracing.py`: `reap_request` (single-request orphan close, reusing
  `finish_request(outcome="orphaned")`) and `note_response_sent` (root `add_event`).
- `ws/handler.py`: `note_response_sent` at the response_sent write; `room_id=`
  filled on the message_received / response_sent / lifecycle-persist writes.
- `ws/machine_handler.py`: `machines_online.inc()/.dec()` on connect/disconnect.
- `rooms/router.py`: `GET /{room_id}/activity` (admin-gated) reading the indexed
  `room_id` column, returning `ActivityLogOut[]`.
- `db/models.py` + `migrations/versions/041_*.py`: `ActivityLog.room_id` column +
  `ix_activity_logs_room_ts` index + Python backfill from `details->room_id`.
- Tests: `test_observability_phase2.py`, `test_rooms_activity.py`; sweeper tests
  updated for the list return; migration head bumped 040→041.

## Decisions

- **`sweep_orphaned_requests` returns the request_ids, not a count.** One change
  serves both the orphan counter (`len`) and the span bridge (reap each); a separate
  re-query would duplicate work the sweeper already did. Cost: 5 sweeper-test
  assertions updated to `len()`. Rejected keeping `int` + re-querying.
- **`reap_request` = `finish_request(outcome="orphaned")`, not a new code path.**
  `finish_request` already closes root+children with the given outcome and sets
  ERROR status for "orphaned"; a thin alias documents the sweeper's intent without
  duplicating logic.
- **`/rooms/{id}/activity` returns a flat `ActivityLogOut[]`, grouped client-side.**
  Reuses the frontend `splitLogs`; server-side grouping + causal ordering is Phase 3.
  Admin-gated to match `/agents/{id}/activity` (a room view exposes every agent).
- **`room_id` promoted to a first-class indexed column with a backfill, vs. querying
  `details->>'room_id'`.** JSON-path filtering can't use an index and the dialect
  syntax differs (SQLite vs Postgres); the column makes the new endpoint scale. The
  backfill is a portable Python pass (same reason the sweeper does a second pass for
  room_id) — fine at current scale.
- **Gauges reconciled/inc-dec, not per-transition wired.** `agents_by_state` from a
  60s COUNT GROUP BY (cheap, no per-state-change hooks); `machines_online` inc/dec at
  the WS connect/disconnect, balanced by the `finally`. Assumption: orphan/turn
  volumes are modest (single-transaction backfill; sweeper second-pass acceptable).

## Result

- cluster suite 1055 passed (new: tracing reap/note tests, room-activity endpoint
  tests, sweeper list-return); ruff clean; `alembic upgrade head` applies 041
  (040→041 confirmed) and backfills room_id from details.
- `/metrics` gains `anygarden_agent_turns_orphaned_total` and live
  `machines_online` / `agents_by_state`; traces show a `response_sent` root event;
  `/api/v1/rooms/{id}/activity` returns a room's turns off an indexed column; DB and
  span orphan decisions now agree immediately.
- No wire/protocol change. Pending: Phase 3 (A→B causal links via parent_request_id
  + OTEL span links, room-flow swimlane frontend view consuming this endpoint,
  optional per-engine LLM detail).
