# feat(sidebar): unread update indicators (#385)

- Commit: pending in `feat/385-sidebar-update-indicators`
- Author: Changyong Um
- Date: 2026-05-20T02:50:00+09:00
- PR: pending

## Situation

The sidebar showed agent presence, but it did not show whether rooms or
DMs had new messages while the user was reading a different room. Users
had to open rooms manually to discover missed updates.

## Task

- Store a per-user, per-room read cursor with minimal schema change.
- Expose a boolean `has_updates` field on room list responses.
- Mark the current room read when the user enters it and after visible
  messages arrive.
- Render a compact update dot on project rooms, DM rows, and grouped
  agent DM headers.

## Action

- Added `participants.last_read_message_seq` via Alembic revision `040`
  and mirrored it on the `Participant` SQLAlchemy model.
- Added `doorae.rooms.unread` with `compute_has_updates_map` and
  monotonic `mark_room_read` helpers.
- Extended `GET /api/v1/rooms` with caller-specific `has_updates` and
  added `POST /api/v1/rooms/{room_id}/read`.
- Added `Room.has_updates`, `markRoomRead`, visibility refresh, and a
  60-second sidebar refresh loop to `useRooms`.
- Added `UpdateDot` and rendered it for normal rooms, pinned rooms,
  single DMs, multi-DM children, orphan DMs, and aggregated multi-DM
  agent headers.
- Added backend unread tests and sidebar rendering tests.

## Result

- Sidebar rows now show a 6px blue dot when the current user has not
  marked the room read at the latest message sequence.
- Opening a room clears its dot after the server accepts the read
  update; active chat views debounce follow-up read updates by 1.5s.
- Verification:
  - `uv run --extra dev pytest -x -v` in `packages/cluster`:
    985 passed, 1 deselected, 1 warning.
  - `uv run --extra dev ruff check doorae tests/test_rooms_unread.py tests/test_migrations.py`:
    clean.
  - `npm test` in `packages/cluster/frontend`: 423 passed.
  - `npm run build` in `packages/cluster/frontend`: passed.
