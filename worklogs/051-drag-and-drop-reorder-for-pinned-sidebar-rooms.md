# feat(rooms): drag-and-drop reorder for pinned sidebar rooms (#47) (#51)

- Commit: `d4903ad` (d4903ade6c4e913b8337b73711c1c6783e4c99d5)
- Author: Changyong Um
- Date: 2026-04-15T21:51:00+09:00
- PR: #51

## Situation

The sidebar listed every top-level room with a fixed alphabetical sort plus the
existing tree structure. There was no way for a user to elevate frequently used
rooms above the rest — they always lived wherever their title sorted. Issue #47
asked for a "Pinned" area with a user-controlled manual order that survived
across sessions and multi-tab usage.

## Task

- Introduce per-user pin state and manual order without disturbing the
  alphabetical sort for the non-pinned majority.
- Keep reorders cheap (no full-list rewrites on every drag).
- Propagate pin/order changes to the user's other open tabs in real time.
- Preserve navigation: a drag handle must not steal plain-click selection.
- Provide keyboard accessibility for the drag interaction.
- Out of scope: sub-room manual order, project-level manual order, multi-select drag.

## Action

Backend
- Alembic `015_participant_pin_order.py` adds `pinned` and `sort_order` columns
  to `Participant`, plus a composite index on `(user_id, pinned, sort_order)`.
  Sparse integer spacing (stride 1024) keeps typical mid-list reorders O(1).
- `packages/cluster/doorae/rooms/service.py`: `set_room_pinned` performs
  tail placement when pinning; `reorder_pinned_rooms` rewrites the snapshot
  idempotently from a client-supplied ordered ID list.
- `packages/cluster/doorae/rooms/router.py`: `PATCH /api/v1/rooms/{id}/pin`
  and `PUT /api/v1/rooms/pin-order` endpoints. Both are guest-forbidden.
  Each fans a new `RoomPinOrderChangedOut` frame (defined in
  `doorae/ws/protocol.py`) to the caller's other WS sessions for multi-tab sync.
- `GET /api/v1/rooms` response extended with caller-specific `pinned` and
  `sort_order` so the sidebar can render the initial state in a single round-trip.

Frontend
- Added `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities` to
  `packages/cluster/frontend/package.json`. Pointer sensor activates at 6px
  so plain clicks still navigate; keyboard sensor handles Space pick-up +
  arrow-key movement.
- `Sidebar.tsx` splits top-level pinned rooms into a dedicated DnD section
  with drag handles, adds a pin affordance on unpinned rows, and an unpin
  affordance on pinned rows. Non-pinned rooms keep the existing tree.
- `hooks/useRooms.ts` exposes `pinRoom` and `reorderPinnedRooms` with
  optimistic updates and full rollback on failure.
- `hooks/useWebSocket.ts` forwards the `room_pin_order_changed` WS frame as
  a `doorae:rooms:pin-order` window event; the `RoomsProvider` applies the
  snapshot directly without refetching.

Tests
- `packages/cluster/tests/test_rooms_pin.py` (new, 374 lines) covers service
  invariants (tail placement, idempotent reorder, sparse-spacing behavior)
  and router authz/broadcast.
- `packages/cluster/tests/test_migrations.py` updated for the 015 migration.

## Result

Users can now drag top-level rooms into a Pinned section with a persistent
manual order, pin/unpin from either section, and see the change reflected
in their other open tabs without a refresh. Alphabetical behavior for
non-pinned rooms and existing sub-room flows are unchanged. The sparse
`sort_order` layout means typical reorders touch a single row; full-snapshot
rewrites only happen when the caller sends a new explicit order. CHANGELOG
entry added under `packages/cluster/CHANGELOG.md`. Follow-up work (sub-room
and project-level manual ordering, multi-select drag) intentionally deferred.
